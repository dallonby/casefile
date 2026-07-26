#!/usr/bin/env python3
"""spitball — multi-model deliberation driver (SPEC §12).

The casefile log remains the append-only source of epistemic state.  The
driver also keeps an atomic, local run journal containing every prompt,
reply, adapter session id, manifest row, and finalization check.  That
journal is operational state, not a competing source of truth: it makes a
crash diagnosable and a deliberation replayable without upgrading transient
model output into casefile evidence.

Adapters (§12.2): start(context) -> handle, send(handle, msg) -> reply,
cost(handle) -> {"usd": float|None, "tokens": int}, stop(handle). Flag sets
below were verified against the installed CLIs on 2026-07-17 (see the log,
source: manual) — re-verify on CLI upgrades, never trust memory.
"""

import json
import hashlib
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import casefile as cf

TURN_TIMEOUT_S = 600
CLI = [sys.executable, str(Path(cf.__file__).resolve())]  # works outside this repo
CLI_STR = f"python3 {Path(cf.__file__).resolve()}"

DEFAULT_ANALYSIS_LAYERS = (
    "problem definition and scope",
    "ground truth and evidence quality",
    "causal or mechanistic reasoning",
    "alternatives and counterfactuals",
    "trade-offs and distributional effects",
    "implementation, failure modes, and reversibility",
)
MANIFEST_LIST_FIELDS = (
    "requirements", "criteria", "alternatives", "evidence_domains",
    "analysis_layers", "open_questions",
)
TURN_MARKER = "CASEFILE_TURN_JSON:"
SUMMARY_MARKER = "CASEFILE_SUMMARY_JSON:"
PREFLIGHT_MARKER = "CASEFILE_PREFLIGHT_OK"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _response_score(text: str) -> int:
    """Prefer argument-bearing output over hook/progress receipts."""
    s = (text or "").strip()
    if not s:
        return -10_000
    low = s.lower()
    score = len(s)
    if (low.startswith("recorded:") or "secretary sweep:" in low) \
            and len(s) < 600:
        score -= 5_000
    progress = (
        "i'll inspect", "i will inspect", "i’m inspecting", "i'm inspecting",
        "i’ll first", "i'll first", "starting with", "let me inspect",
        "i’m going to", "i'm going to",
    )
    if any(low.startswith(p) for p in progress) and len(s) < 600:
        score -= 3_000
    if TURN_MARKER.lower() in low or SUMMARY_MARKER.lower() in low:
        score += 1_000
    return score


def _substantive(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 120 or _response_score(s) <= 0:
        return False
    # A real argument should contain more than a single progress/receipt line.
    return len([line for line in s.splitlines() if line.strip()]) >= 2


def _extract_json_marker(text: str, marker: str) -> dict | None:
    """Return the last valid one-line JSON object following ``marker``."""
    found = None
    for line in (text or "").splitlines():
        if marker not in line:
            continue
        raw = line.split(marker, 1)[1].strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            found = value
    return found


def _last_json_object(raw: str) -> dict | None:
    """Find the last complete JSON object in noisy CLI output."""
    decoder = json.JSONDecoder()
    found = None
    best = (-1, -10**18)
    for match in re.finditer(r"\{", raw):
        try:
            value, end = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        # Prefer the object ending latest; when a nested object shares its
        # parent's closing brace, prefer the earlier/larger parent.
        rank = (match.start() + end, -match.start())
        if isinstance(value, dict) and rank > best:
            found = value
            best = rank
    return found


def _validate_model_reply(text: str, phase: str,
                          allowed_coverage: set[str],
                          expected_rounds: list[str] | None = None
                          ) -> tuple[bool, str, dict]:
    """Validate the explicit turn/summary contract used by live adapters."""
    def string_list(value) -> bool:
        return isinstance(value, list) and all(
            isinstance(x, str) and x.strip() and x.strip() not in ("...", "…")
            for x in value)

    if not _substantive(text):
        return False, "reply is empty, receipt-only, or progress-only", {}
    marker = SUMMARY_MARKER if phase == "summary" else TURN_MARKER
    envelope = _extract_json_marker(text, marker)
    if envelope is None:
        return False, f"missing valid {marker} envelope", {}
    coverage = envelope.get("coverage")
    if not string_list(coverage):
        return False, "envelope coverage must be a list of nonempty row ids", {}
    unknown = sorted(set(coverage) - allowed_coverage)
    if unknown:
        return False, "unknown manifest coverage id(s): " + ", ".join(unknown), {}
    if phase == "summary":
        rounds = envelope.get("rounds")
        if not string_list(rounds):
            return (
                False,
                "summary envelope requires non-placeholder string list "
                "field 'rounds'",
                {},
            )
        if expected_rounds is not None and rounds != expected_rounds:
            return (
                False,
                "summary rounds must exactly equal "
                + json.dumps(expected_rounds),
                {},
            )
        for key in ("decided", "ruled_out", "open"):
            if not string_list(envelope.get(key)):
                return (
                    False,
                    f"summary envelope requires non-placeholder string list "
                    f"field {key!r}",
                    {},
                )
        if envelope.get("conclusion_class") != "model-recommendation":
            return (
                False,
                "summary conclusion_class must be 'model-recommendation'",
                {},
            )
    else:
        position = envelope.get("position")
        if not isinstance(position, str) or not position.strip() \
                or position.strip() in ("...", "…"):
            return (
                False,
                "turn envelope requires non-placeholder string field "
                "'position'",
                {},
            )
        for key in ("filed", "falsifiers"):
            if not string_list(envelope.get(key)):
                return (
                    False,
                    f"turn envelope requires non-placeholder string list "
                    f"field {key!r}",
                    {},
                )
    return True, "", envelope


def _ferry_payload(text: str, envelope: dict) -> str:
    """Remove receipt/protocol noise while preserving a structured turn delta."""
    kept = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        low = stripped.casefold()
        if TURN_MARKER in line or SUMMARY_MARKER in line:
            continue
        if low.startswith("recorded:"):
            continue
        kept.append(line)
    prose = "\n".join(kept).strip()
    if not prose:
        prose = (text or "").strip()
    if envelope:
        delta = {
            key: envelope.get(key)
            for key in ("coverage", "filed", "position", "falsifiers")
        }
        prose += "\n\nCASEFILE TURN DELTA: " + json.dumps(
            delta, ensure_ascii=False, separators=(",", ":"))
    return prose


def _row_id(prefix: str, text: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{text}".encode()).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _manifest_item(value, field: str, default_source: str,
                   default_status: str) -> dict:
    if isinstance(value, str):
        item = {"text": value}
    elif isinstance(value, dict):
        item = dict(value)
    else:
        raise ValueError(f"manifest {field} items must be strings or objects")
    text = str(item.get("text") or item.get("name") or "").strip()
    if not text:
        raise ValueError(f"manifest {field} item has no text/name")
    prefix = {
        "requirements": "req",
        "criteria": "crit",
        "alternatives": "alt",
        "evidence_domains": "evidence",
        "analysis_layers": "layer",
        "open_questions": "question",
    }[field]
    item["text"] = text
    item["id"] = str(item.get("id") or _row_id(prefix, text))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", item["id"]):
        raise ValueError(
            f"manifest {field} id {item['id']!r} is not a safe row id")
    item["source"] = str(item.get("source") or default_source)
    item["status"] = str(item.get("status") or default_status)
    item["required"] = bool(item.get("required", True))
    item.pop("name", None)
    return item


def _merge_manifest_items(items: list[dict]) -> list[dict]:
    """Stable de-duplication by id, then case-insensitive text."""
    out, ids, texts = [], set(), set()
    for item in items:
        key = item["text"].casefold()
        if item["id"] in ids or key in texts:
            continue
        ids.add(item["id"])
        texts.add(key)
        out.append(item)
    return out


def build_manifest(root: Path, case: str, topic: str,
                   manifest_path: str | None = None,
                   requirements=(), criteria=(), alternatives=(),
                   evidence_domains=(), analysis_layers=(),
                   open_questions=(), weighting=None,
                   mode: str = "warn") -> dict:
    """Build a domain-neutral coverage contract from the case and CLI input.

    Explicit items are user-confirmed.  Casefile constraints/decisions and
    live hypotheses are included with provenance, so a debate cannot silently
    shed context that was already recorded.
    """
    if mode not in ("enforce", "warn", "off"):
        raise ValueError("manifest mode must be enforce, warn, or off")
    raw = {}
    if manifest_path:
        p = Path(manifest_path)
        if not p.is_absolute():
            p = (root / p).resolve()
        try:
            raw = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as ex:
            raise ValueError(f"cannot read manifest {p}: {ex}") from ex
        if not isinstance(raw, dict):
            raise ValueError("manifest root must be a JSON object")
        allowed_top = {
            "schema", "topic", "case", "mode", "created_at", "weighting",
            "coverage_rows", "required_coverage", "warnings", "metadata",
            *MANIFEST_LIST_FIELDS,
        }
        unknown = sorted(set(raw) - allowed_top)
        if unknown:
            raise ValueError(
                "unknown manifest field(s): " + ", ".join(unknown))
        supplied_topic = str(raw.get("topic") or "").strip()
        if supplied_topic and supplied_topic != topic:
            raise ValueError(
                f"manifest topic {supplied_topic!r} does not match --topic {topic!r}")
        supplied_case = str(raw.get("case") or "").strip()
        if supplied_case and supplied_case != case:
            raise ValueError(
                f"manifest case {supplied_case!r} does not match active case {case!r}")

    values: dict[str, list] = {field: list(raw.get(field) or [])
                               for field in MANIFEST_LIST_FIELDS}
    explicit = {
        "requirements": requirements,
        "criteria": criteria,
        "alternatives": alternatives,
        "evidence_domains": evidence_domains,
        "analysis_layers": analysis_layers,
        "open_questions": open_questions,
    }
    for field, additions in explicit.items():
        values[field].extend(additions or ())

    entries = cf.read_entries(root)
    hidden = cf.superseded_ids(entries)
    revoked = cf.revoked_ids(entries)
    grades = cf.compute_grades(entries)
    ce = [e for e in entries if e["case"] == case and e["id"] not in hidden]

    # The topic itself is always a required scope row, preserving it verbatim.
    values["requirements"].insert(0, {
        "id": _row_id("scope", topic), "text": topic, "kind": "scope",
        "source": "command", "status": "confirmed", "required": True,
    })
    for e in ce:
        if e["type"] == "constraint" and e["id"] not in revoked:
            values["requirements"].append({
                "id": f"entry-{e['id']}", "text": e["body"],
                "kind": "constraint", "entry_id": e["id"],
                "source": "casefile", "status": (
                    "confirmed" if cf.normalize_author(e["author"]) == "user"
                    else "asserted"),
            })
        elif e["type"] == "decision" and grades.get(e["id"]) not in (
                "revoked", "fulfilled"):
            values["requirements"].append({
                "id": f"entry-{e['id']}", "text": e["body"],
                "kind": "decision", "entry_id": e["id"],
                "source": "casefile", "status": (
                    "confirmed" if cf.normalize_author(e["author"]) == "user"
                    else "asserted"),
            })
        elif e["type"] == "hypothesis" and grades.get(e["id"]) != "refuted":
            values["alternatives"].append({
                "id": f"entry-{e['id']}", "text": e["body"],
                "kind": "hypothesis", "entry_id": e["id"],
                "source": "casefile", "status": grades.get(e["id"], "hypothesis"),
            })
    qs, _ = cf.open_items(ce)
    for q in qs:
        values["open_questions"].append({
            "id": f"entry-{q['id']}", "text": q["body"],
            "entry_id": q["id"], "source": "casefile", "status": "open",
            "required": True,
        })

    if not values["analysis_layers"]:
        values["analysis_layers"] = list(DEFAULT_ANALYSIS_LAYERS)

    manifest = {
        "schema": "casefile-deliberation-manifest/1",
        "topic": topic,
        "case": case,
        "mode": mode,
        "created_at": _utcnow(),
    }
    raw_weighting = raw.get("weighting")
    if weighting is not None:
        manifest["weighting"] = {
            "scheme": str(weighting), "source": "provided",
            "status": "confirmed",
        }
    elif isinstance(raw_weighting, str):
        manifest["weighting"] = {
            "scheme": raw_weighting, "source": "manifest",
            "status": "inferred",
        }
    elif isinstance(raw_weighting, dict):
        manifest["weighting"] = dict(raw_weighting)
        manifest["weighting"].setdefault("source", "manifest")
        manifest["weighting"].setdefault("status", "inferred")
    elif raw_weighting is not None:
        raise ValueError("manifest weighting must be a string or object")
    else:
        manifest["weighting"] = None
    for field in MANIFEST_LIST_FIELDS:
        normalized = []
        for item in values[field]:
            source = "provided" if item in (explicit.get(field) or ()) else "manifest"
            status = "confirmed" if source == "provided" else "inferred"
            normalized.append(_manifest_item(item, field, source, status))
        manifest[field] = _merge_manifest_items(normalized)

    warnings = []
    if not any(item.get("kind") != "scope"
               for item in manifest["requirements"]):
        warnings.append("no explicit requirements beyond the topic supplied")
    if not manifest["criteria"]:
        warnings.append("no evaluation criteria or user weights supplied")
    elif not manifest["weighting"]:
        warnings.append("no criterion weighting/priority scheme supplied")
    elif str(manifest["weighting"].get("status", "")).casefold() not in (
            "confirmed", "user-confirmed"):
        warnings.append(
            "criterion weighting is inferred rather than user-confirmed")
    if len(manifest["alternatives"]) < 2:
        warnings.append("fewer than two alternatives/hypotheses supplied")
    if not manifest["evidence_domains"]:
        warnings.append("no required evidence domains supplied")

    rows = []
    for field in ("requirements", "criteria", "alternatives",
                  "evidence_domains", "analysis_layers", "open_questions"):
        for item in manifest[field]:
            if item.get("required", True):
                rows.append({
                    "id": item["id"], "kind": field, "text": item["text"],
                })
    if manifest["weighting"]:
        scheme = str(manifest["weighting"].get("scheme")
                     or manifest["weighting"].get("text")
                     or manifest["weighting"])
        rows.append({
            "id": _row_id("weighting", scheme),
            "kind": "criterion_weighting", "text": scheme,
        })
    # Package-symmetry grid: every alternative must be assessed against every
    # criterion, rather than a favoured package receiving richer safeguards.
    for alt in manifest["alternatives"]:
        for criterion in manifest["criteria"]:
            rows.append({
                "id": _row_id(
                    "matrix", f"{alt['id']}:{criterion['id']}"),
                "kind": "alternative_x_criterion",
                "text": f"{alt['text']} × {criterion['text']}",
                "alternative_id": alt["id"],
                "criterion_id": criterion["id"],
            })
    manifest["coverage_rows"] = rows
    manifest["required_coverage"] = (
        [] if mode == "off" else [row["id"] for row in rows])
    manifest["warnings"] = warnings
    if mode == "enforce" and warnings:
        raise ValueError("strict manifest incomplete: " + "; ".join(warnings))
    return manifest


def manifest_prompt(manifest: dict) -> str:
    """Compact, exact contract injected into both private model sessions."""
    rows = "\n".join(
        f"- {r['id']}: [{r['kind']}] {r['text']}"
        for r in manifest["coverage_rows"])
    first_row = (
        [manifest["coverage_rows"][0]["id"]]
        if manifest["coverage_rows"] else [])
    turn_shape = json.dumps({
        "coverage": first_row,
        "filed": [],
        "position": "state the current position here",
        "falsifiers": [],
    }, separators=(",", ":"))
    return (
        "\nDRIVER INVARIANTS (apply even when role files are customized):\n"
          "- Inspect and deliberate; do not edit project/application files or "
          "mutate external systems. Only casefile filings and transient "
          "read-only test artifacts may write.\n"
          "- Ranking-driving hypotheses require mode, comparator, falsifier "
          "analysis layer, counterfactual, horizon, testability and, for "
          "causal/mechanistic claims, mechanism. Keep observations, "
          "causal inference, normative weights, and implementation claims "
          "separate.\n"
          "- Model recommendation, cross-model consensus, and user decision "
          "are distinct. Never impersonate user authority.\n"
          "\nDELIBERATION MANIFEST (frozen for this run):\n"
        + json.dumps({k: manifest[k] for k in (
            "topic", "requirements", "criteria", "weighting", "alternatives",
            "evidence_domains", "analysis_layers", "open_questions")},
            ensure_ascii=False, indent=2)
        + "\n\nREQUIRED COVERAGE ROWS:\n"
        + (rows or "- none (manifest mode off)")
        + "\n\nCoverage is not agreement. Only list a row after substantively "
          "addressing it, with comparable scrutiny for competing alternatives.\n"
          f"End every argumentative turn with one single-line {TURN_MARKER} "
          f"{turn_shape}\n")


def adapter_preflight(adapter, root: Path) -> dict:
    """Cheap, non-model preflight before any paid call."""
    report = {
        "adapter": type(adapter).__name__,
        "context_transport": getattr(adapter, "context_transport", "unknown"),
        "hook_isolation": getattr(adapter, "hook_isolation", "unknown"),
        "root": str(root),
        "casefile_cli": str(Path(cf.__file__).resolve()),
        "checked_at": _utcnow(),
    }
    if not (root / ".casefile" / "log.jsonl").is_file():
        raise RuntimeError("adapter preflight: casefile log is missing")
    if not os.access(root / ".casefile", os.W_OK):
        raise RuntimeError("adapter preflight: .casefile is not writable")
    exe = getattr(adapter, "executable", None)
    if not exe:
        report.update({"status": "skipped", "reason": "scripted/custom adapter"})
        return report
    path = shutil.which(exe)
    if not path:
        raise RuntimeError(f"adapter preflight: executable {exe!r} not found")
    p = subprocess.run([path, "--version"], cwd=root, capture_output=True,
                       text=True, timeout=15)
    if p.returncode != 0:
        raise RuntimeError(
            f"adapter preflight: {exe} --version failed: {p.stderr[:200]}")
    report.update({"status": "ok", "executable": path,
                   "version": (p.stdout or p.stderr).strip().splitlines()[0]})
    return report


def _public_handle(handle: dict | None) -> dict:
    """Serializable adapter state for crash diagnosis/recovery."""
    if not handle:
        return {}
    allowed = (str, int, float, bool, type(None))
    return {k: v for k, v in handle.items()
            if isinstance(v, allowed) and k != "reply"}


def _atomic_json(path: Path, value: dict):
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        # Persist the rename itself, not only the temporary file contents.
        try:
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass  # directory fsync is unavailable on some platforms
    finally:
        tmp.unlink(missing_ok=True)


class RunJournal:
    """Atomic operational journal for one deliberation."""

    def __init__(self, path: Path, initial: dict):
        self.path = path
        self.data = initial
        self.flush()

    def flush(self):
        self.data["updated_at"] = _utcnow()
        _atomic_json(self.path, self.data)

    def set(self, **values):
        self.data.update(values)
        self.flush()

    def begin_call(self, model: str, phase: str, prompt: str) -> int:
        seq = len(self.data["calls"]) + 1
        self.data["phase"] = phase
        self.data["status"] = "running"
        self.data["calls"].append({
            "seq": seq, "model": model, "phase": phase,
            "status": "pending", "prompt": prompt, "started_at": _utcnow(),
        })
        self.flush()  # input is durable before control passes to a vendor
        return seq

    def finish_call(self, seq: int, reply: str, handle: dict, cost: dict):
        call = self.data["calls"][seq - 1]
        call.update({
            "status": "completed", "reply": reply,
            "completed_at": _utcnow(), "handle": _public_handle(handle),
            "cost_after": cost,
        })
        self.data.setdefault("adapter_handles", {})[call["model"]] = \
            _public_handle(handle)
        self.flush()  # reply is durable before transcript/ferrying

    def fail_call(self, seq: int, ex: BaseException):
        self.data["calls"][seq - 1].update({
            "status": "failed", "failed_at": _utcnow(),
            "error": f"{type(ex).__name__}: {ex}",
        })
        self.data["status"] = "aborted"
        self.data["error"] = f"{type(ex).__name__}: {ex}"
        self.flush()

    def annotate_call(self, seq: int, **values):
        self.data["calls"][seq - 1].update(values)
        self.flush()


def _eligible_digest_span(entries: list[dict], case: str,
                          since_n: int) -> list[str]:
    """Argument-state entries a session judgment must not silently drop."""
    types = {
        "hypothesis", "endorsement", "dispute", "resolution",
        "verification", "question",
    }
    return [e["id"] for e in entries[since_n:]
            if e["case"] == case and e["type"] in types
            and not cf.digest_invariant_violations(entries, [e["id"]])]


def _finalization_blockers(root: Path, case: str, start_n: int, manifest: dict,
                           coverage: dict[str, set[str]],
                           summary_coverage: dict[str, set[str]],
                           divergence: bool, outcome: str) -> list[str]:
    blockers = []
    entries = cf.read_entries(root)
    resolved = cf.resolved_ref_ids(entries)
    hidden = cf.superseded_ids(entries)
    revoked = cf.revoked_ids(entries)
    if outcome != "converged":
        blockers.append(f"outcome is {outcome}, not converged")
    if manifest["mode"] != "off":
        blockers.extend(f"manifest incomplete: {w}" for w in manifest["warnings"])
        required = set(manifest["required_coverage"])
        union = set().union(*coverage.values()) if coverage else set()
        missing = sorted(required - union)
        if missing:
            blockers.append("uncovered manifest rows: " + ", ".join(missing))
        for model, covered in summary_coverage.items():
            missing_summary = sorted(required - covered)
            if missing_summary:
                blockers.append(
                    f"{model} summary omitted rows: "
                    + ", ".join(missing_summary))
        unresolved_manifest = [
            q["id"] for q in manifest["open_questions"]
            if q.get("required", True)
            and str(q.get("status", "")).lower()
            not in ("answered", "closed", "resolved")
            and not (q.get("entry_id") and q["entry_id"] in resolved)
        ]
        if unresolved_manifest:
            blockers.append(
                "manifest questions unresolved: "
                + ", ".join(unresolved_manifest))
        changed_requirements = [
            item["entry_id"] for item in manifest["requirements"]
            if item.get("entry_id")
            and item.get("kind") in ("constraint", "decision")
            and (item["entry_id"] in hidden or item["entry_id"] in revoked)
        ]
        if changed_requirements:
            blockers.append(
                "frozen manifest requirements changed during run; re-freeze: "
                + ", ".join(changed_requirements))
    if divergence:
        blockers.append("model summaries still diverge after reconciliation")

    ce = [e for e in entries if e["case"] == case and e["id"] not in hidden]
    questions, disputes = cf.open_items(ce)
    if questions:
        blockers.append("open questions: " + ", ".join(q["id"] for q in questions))
    if disputes:
        blockers.append("open disputes: " + ", ".join(d["id"] for d in disputes))
    for h in entries[start_n:]:
        if h["case"] != case or h["type"] != "hypothesis":
            continue
        required = [
            "claim_mode", "comparator", "analysis_layer", "falsifier",
            "counterfactual", "horizon", "testability",
        ]
        if h.get("claim_mode") in (
                "causal-inference", "diagnosis", "mechanistic"):
            required.append("mechanism")
        missing = [key for key in required if not h.get(key)]
        if missing:
            blockers.append(
                f"ranking claim {h['id']} lacks claim-card fields: "
                + ", ".join(missing))
    case_ids = {e["id"] for e in ce}
    for problem in cf.lint_problems(entries):
        if problem.startswith(("CONTRADICTION", "EXPIRED-SOURCE",
                               "PROVENANCE", "DIGEST-VIOLATION")) \
                and any(eid in problem for eid in case_ids):
            blockers.append("lint gate: " + problem)
    return blockers


def _candidate_digest(entries: list[dict], before_n: int, case: str,
                      author: str, required_span: set[str],
                      required_refs: set[str] | None = None
                      ) -> tuple[dict | None, str]:
    candidates = [
        e for e in entries[before_n:]
        if e["case"] == case and e["type"] == "digest"
        and e.get("kind") == "candidate"
        and cf.normalize_author(e["author"]) == cf.normalize_author(author)
    ]
    if len(candidates) != 1:
        return None, (
            f"expected exactly one new candidate digest from {author}; "
            f"found {len(candidates)}")
    candidate = candidates[0]
    actual_span = set(candidate.get("supersedes", []))
    missing = sorted(required_span - actual_span)
    if missing:
        return None, "candidate digest dropped session entries: " + ", ".join(missing)
    extra = sorted(actual_span - required_span)
    if extra:
        return None, (
            "candidate digest included entries outside the exact session span: "
            + ", ".join(extra))
    missing_refs = sorted(
        (required_refs or set()) - set(candidate.get("refs", [])))
    if missing_refs:
        return None, (
            "candidate digest omitted manifest entry reference(s): "
            + ", ".join(missing_refs))
    return candidate, ""


def _critic_verdict(entries: list[dict], before_n: int, digest_id: str,
                    critic: str) -> tuple[str | None, str | None]:
    reviews = [
        e for e in entries[before_n:]
        if e["type"] in ("endorsement", "dispute")
        and digest_id in e.get("refs", [])
        and cf.normalize_author(e["author"]) == cf.normalize_author(critic)
    ]
    if not reviews:
        return None, None
    review = reviews[-1]
    return ("endorsed" if review["type"] == "endorsement" else "disputed",
            review["id"])


# ------------------------------------------------------------------ adapters

class ClaudeAdapter:
    """claude -p resume-mode (§12.2 v1). Safe mode excludes hooks and other
    customizations while preserving auth and built-in analysis tools."""
    name = "claude"
    executable = "claude"
    strict_output = True
    context_transport = "vendor-session-resume"
    hook_isolation = "safe mode; custom hooks/settings disabled"

    def __init__(self, root: Path):
        self.root = root
        # one comma-joined value: --allowedTools is variadic and would
        # otherwise swallow any later positional (live-run failure, 2026-07-17)
        self.base = ["claude", "-p", "--output-format", "json",
                     "--safe-mode", "--permission-mode", "auto",
                     "--disallowedTools", "Edit,Write,NotebookEdit",
                     "--allowedTools",
                     "Read,Grep,Glob,WebSearch,WebFetch,"
                     f"Bash({CLI_STR}:*),Bash(python3 casefile.py:*)"]

    def start(self, context: str) -> dict:
        return self._call(None, context)

    def send(self, handle: dict, msg: str) -> str:
        h = self._call(handle["sid"], msg, handle)
        return h["reply"]

    def _call(self, sid, prompt, handle=None):
        cmd = list(self.base) + (["-r", sid] if sid else [])
        p = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True,
                           input=prompt,  # stdin: immune to variadic-flag capture
                           timeout=TURN_TIMEOUT_S)
        if p.returncode != 0:
            raise RuntimeError(f"claude adapter: rc={p.returncode}: {p.stderr[:300]}")
        d = json.loads(p.stdout)
        h = handle or {"sid": None, "usd": 0.0, "tokens": 0,
                       "cache_read_tokens": 0}
        h["sid"] = d.get("session_id", h["sid"])
        h["usd"] += float(d.get("total_cost_usd") or 0.0)
        usage = d.get("usage") or {}
        h["tokens"] += int(
            usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        h["cache_read_tokens"] = h.get("cache_read_tokens", 0) + int(
            usage.get("cache_read_input_tokens", 0))
        h["reply"] = d.get("result", "")
        return h

    def cost(self, handle):
        return {"usd": handle["usd"], "tokens": handle["tokens"]}

    def stop(self, handle):
        pass  # -p sessions end per call; nothing to tear down


class StreamClaudeAdapter:
    """M6: one long-lived `claude -p --input/output-format stream-json`
    process per session. Measured 2026-07-17 (see log): second turns ~3x
    faster than resume-mode (no session reload), and only this transport can
    interject mid-turn (§12.3 hot path). Protocol verified by probe: user
    messages as JSONL in; system/assistant/result events out; `--verbose`
    required for stream output with -p."""
    name = "claude"
    executable = "claude"
    strict_output = True
    context_transport = "long-lived-stream"
    hook_isolation = "safe mode; custom hooks/settings disabled"

    def __init__(self, root: Path):
        self.root = root
        self.cmd = ["claude", "-p", "--input-format", "stream-json",
                    "--output-format", "stream-json", "--verbose",
                    "--safe-mode", "--permission-mode", "auto",
                    "--disallowedTools", "Edit,Write,NotebookEdit",
                    "--allowedTools",
                    "Read,Grep,Glob,WebSearch,WebFetch,"
                    f"Bash({CLI_STR}:*),Bash(python3 casefile.py:*)"]

    @staticmethod
    def _umsg(text: str) -> str:
        return json.dumps({"type": "user", "message": {
            "role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"

    @staticmethod
    def _apply_event(handle: dict, ev: dict) -> bool:
        """Fold one stream event into the handle; True when the turn is done."""
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            handle["sid"] = ev.get("session_id", handle.get("sid"))
        elif ev.get("type") == "result":
            handle["reply"] = ev.get("result", "")
            handle["usd"] += float(ev.get("total_cost_usd") or 0.0)
            usage = ev.get("usage") or {}
            handle["tokens"] += int(
                usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
            handle["cache_read_tokens"] = handle.get(
                "cache_read_tokens", 0) + int(
                    usage.get("cache_read_input_tokens", 0))
            handle["sid"] = ev.get("session_id", handle.get("sid"))
            return True
        return False

    def start(self, context: str) -> dict:
        proc = subprocess.Popen(self.cmd, cwd=self.root, text=True, bufsize=1,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        h = self._attach(proc)
        h["reply"] = self.send(h, context)
        return h

    def _attach(self, proc) -> dict:
        # stdout is drained by a thread so send() can enforce its deadline
        # even when the child goes silent (a blocking readline never returns)
        q = queue.Queue()
        threading.Thread(target=self._pump, args=(proc.stdout, q),
                         daemon=True).start()
        return {"proc": proc, "q": q, "sid": None, "usd": 0.0, "tokens": 0,
                "cache_read_tokens": 0, "reply": ""}

    @staticmethod
    def _pump(stream, q):
        for line in stream:
            q.put(line)
        q.put(None)  # EOF sentinel

    @staticmethod
    def _terminate(proc, graceful=False):
        """Reap a stream child and close parent-side pipes deterministically."""
        try:
            if proc.poll() is None:
                if graceful and proc.stdin:
                    proc.stdin.close()
                else:
                    proc.kill()
            proc.wait(timeout=10)
        except Exception:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        finally:
            for stream in (proc.stdin, proc.stdout):
                if stream and not stream.closed:
                    stream.close()

    def send(self, handle: dict, msg: str) -> str:
        p = handle["proc"]
        if p.poll() is not None:
            raise RuntimeError(f"stream claude died (rc={p.returncode})")
        p.stdin.write(self._umsg(msg))
        p.stdin.flush()
        deadline = time.time() + TURN_TIMEOUT_S
        while True:
            try:
                line = handle["q"].get(timeout=max(0.0, deadline - time.time()))
            except queue.Empty:
                self._terminate(p)  # the turn is wedged; reclaim the child
                raise RuntimeError("stream claude: turn timeout")
            if line is None:
                self._terminate(p)
                raise RuntimeError("stream claude: stdout closed before result")
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._apply_event(handle, ev):
                return handle["reply"]

    def interject(self, handle: dict, msg: str):
        """§12.3 hot path: inject without waiting for the turn to finish."""
        handle["proc"].stdin.write(self._umsg(msg))
        handle["proc"].stdin.flush()

    def cost(self, handle):
        return {"usd": handle["usd"], "tokens": handle["tokens"]}

    def stop(self, handle):
        p = handle.get("proc")
        if p:
            self._terminate(p, graceful=True)


class CodexAdapter:
    """codex exec with session resume (§12.2). thread_id from the
    thread.started event; reply is the last agent_message item."""
    name = "codex"
    executable = "codex"
    strict_output = True
    context_transport = "vendor-session-resume"
    hook_isolation = "user config ignored; auth retained"

    def __init__(self, root: Path):
        self.root = root
        # high effort on every call: recorded user constraint (codex consults)
        effort = ["-c", "model_reasoning_effort=high"]
        # `--ignore-user-config` retains CODEX_HOME authentication but excludes
        # the globally installed casefile Stop hook.  Without this, a nested
        # spitball turn can have its substantive answer replaced by a
        # secretary-sweep receipt from the outer project.
        self.opts = ["--json", "--ignore-user-config", "--skip-git-repo-check",
                     "--search", "--sandbox", "danger-full-access", *effort]
        # `exec resume` rejects --sandbox (live-run failure 2026-07-17);
        # the config-override spelling is accepted by both subcommands
        self.resume_opts = ["--json", "--ignore-user-config",
                            "--skip-git-repo-check",
                            "-c", 'sandbox_mode="danger-full-access"', *effort]

    def start(self, context: str) -> dict:
        return self._call(["codex", "exec", *self.opts, "-"],
                          {"tid": None, "usd": None, "tokens": 0,
                           "cache_read_tokens": 0}, context)

    def send(self, handle: dict, msg: str) -> str:
        h = self._call(["codex", "exec", "resume", handle["tid"],
                        *self.resume_opts, "-"], handle, msg)
        return h["reply"]

    def _call(self, cmd, handle, prompt):
        p = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True,
                           timeout=TURN_TIMEOUT_S, input=prompt)
        if p.returncode != 0:
            raise RuntimeError(f"codex adapter: rc={p.returncode}: {p.stderr[:300]}")
        replies = []
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "thread.started":
                handle["tid"] = ev.get("thread_id", handle["tid"])
            elif ev.get("type") == "item.completed" \
                    and ev.get("item", {}).get("type") == "agent_message":
                replies.append(ev["item"].get("text", ""))
            elif ev.get("type") == "turn.completed":
                u = ev.get("usage", {})
                cached = (u.get("cached_input_tokens")
                          or (u.get("input_tokens_details") or {}).get(
                              "cached_tokens", 0)
                          or 0)
                handle["tokens"] += (
                    max(0, u.get("input_tokens", 0) - cached)
                    + u.get("output_tokens", 0))
                handle["cache_read_tokens"] = handle.get(
                    "cache_read_tokens", 0) + cached
        # Hooks and other wrappers may emit more than one agent_message.  Keep
        # the most substantive candidate rather than blindly selecting the
        # last receipt-sized message.
        reply = max(replies, key=_response_score, default="")
        handle["reply"] = reply
        return handle

    def cost(self, handle):
        return {"usd": handle["usd"], "tokens": handle["tokens"]}

    def stop(self, handle):
        pass


class GrokAdapter:
    """Live xAI Grok Build CLI (`grok`) — subscription headless mode.

    Uses ``grok --prompt-file`` with ``--output-format json`` for each turn
    and ``-r <sessionId>`` to resume the same conversation (verified against
    grok 0.2.x: fields text, sessionId, total_cost_usd, usage.*).

    Author identity for casefile filings is always ``grok`` (family id);
    version nicknames (grok45, …) normalize the same way as casefile aliases.
    Requires an authenticated ``grok`` on PATH (live subscription).
    """
    name = "grok"
    executable = "grok"
    strict_output = True
    context_transport = "vendor-session-resume"
    hook_isolation = "adapter-controlled permission mode"

    def __init__(self, root: Path):
        self.root = root
        # Allow casefile CLI filings; auto-approve so deliberation is unattended.
        # Match Claude adapter's tool allowlist shape.
        self.base = [
            "grok",
            "--output-format", "json",
            "--permission-mode", "auto",
            "--always-approve",
            "--cwd", str(root),
            "--allow", f"Bash({CLI_STR}:*)",
            "--allow", "Bash(python3 casefile.py:*)",
            "--allow", "Bash(casefile:*)",
        ]

    def start(self, context: str) -> dict:
        return self._call(None, context)

    def send(self, handle: dict, msg: str) -> str:
        h = self._call(handle.get("sid"), msg, handle)
        return h["reply"]

    def _call(self, sid, prompt, handle=None):
        env = {**os.environ, "CASEFILE_AUTHOR": "grok"}
        cmd = list(self.base)
        if sid:
            cmd += ["-r", sid]
        # Avoid ARG_MAX/process-list leakage for long manifest/recovery
        # prompts. Grok's prompt-file is the documented single-turn input.
        prompt_dir = self.root / ".casefile" / "state"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        fd, prompt_name = tempfile.mkstemp(
            prefix="grok-prompt-", dir=prompt_dir, text=True)
        prompt_path = Path(prompt_name)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(prompt)
                f.flush()
                os.fsync(f.fileno())
            cmd += ["--prompt-file", str(prompt_path)]
            p = subprocess.run(
                cmd, cwd=self.root, capture_output=True, text=True,
                timeout=TURN_TIMEOUT_S, env=env)
        finally:
            prompt_path.unlink(missing_ok=True)
        if p.returncode != 0:
            raise RuntimeError(
                f"grok adapter: rc={p.returncode}: "
                f"{(p.stderr or p.stdout)[:400]}")
        # stdout is one JSON object (possibly with leading/trailing noise)
        raw = (p.stdout or "").strip()
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            # Tolerate prefix/suffix junk without confusing the last nested
            # object for the top-level result.
            d = _last_json_object(raw)
            if d is None:
                raise RuntimeError(f"grok adapter: non-JSON stdout: {raw[:300]}")
        h = handle or {"sid": None, "usd": 0.0, "tokens": 0, "reply": "",
                       "cache_read_tokens": 0}
        h["sid"] = d.get("sessionId") or d.get("session_id") or h["sid"]
        h["usd"] = (h.get("usd") or 0.0) + float(
            d.get("total_cost_usd") or 0.0)
        usage = d.get("usage") or {}
        # Grok's `total_tokens` includes cache reads.  Summing it over a
        # resumed discussion made the status bar report millions of tokens
        # even though most were cache hits.  The primary counter is now
        # uncached input + output; cache traffic remains separately visible
        # in the durable run journal.
        h["tokens"] = (h.get("tokens") or 0) + int(
            usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        h["cache_read_tokens"] = h.get("cache_read_tokens", 0) + int(
            usage.get("cache_read_input_tokens", 0))
        h["reply"] = d.get("text") or d.get("result") or ""
        return h

    def cost(self, handle):
        return {"usd": handle.get("usd"), "tokens": handle.get("tokens") or 0}

    def stop(self, handle):
        pass  # -p sessions end per call


class FakeAdapter:
    """Scripted adapter for tests and the CI kill test (SPEC §18). The script
    file maps model name -> list of replies; a reply may be a string or
    {"sleep": s, "text": …} to hold a turn open (so tests can kill -9
    mid-round). Runs out of script -> replies 'pass'."""

    def __init__(self, root: Path, name: str, script: Path):
        self.root, self.name, self.script = root, name, script
        self.strict_output = False
        self.executable = None
        self.context_transport = "scripted"
        self.hook_isolation = "not applicable"

    def start(self, context: str) -> dict:
        return {"i": 0}

    def send(self, handle: dict, msg: str) -> str:
        replies = json.loads(self.script.read_text()).get(self.name, [])
        if handle["i"] >= len(replies):
            return "pass"
        r = replies[handle["i"]]
        handle["i"] += 1
        if isinstance(r, dict):
            time.sleep(r.get("sleep", 0))
            return r.get("text", "")
        return r

    def cost(self, handle):
        return {"usd": 0.0, "tokens": 0}

    def stop(self, handle):
        pass


def make_adapter(name: str, root: Path, fake_script: Path | None = None):
    if fake_script:
        return FakeAdapter(root, name, fake_script)
    key = (name or "").strip().lower()
    # family aliases: versioned xAI names still use the grok adapter/author
    if key in ("grok", "grok45", "grok-45", "grok4", "grok-4", "grok-4.5",
               "xai") or key.startswith("grok"):
        return GrokAdapter(root)
    if key == "claude":  # stream promoted to default per the M6 measurement
        return StreamClaudeAdapter(root)
    if key == "claude-resume":  # fallback transport (M4 v1)
        return ClaudeAdapter(root)
    if key == "codex":
        return CodexAdapter(root)
    raise SystemExit(
        f"unknown model '{name}' "
        "(adapters: claude, claude-resume, codex, grok)")


# ------------------------------------------------------------------- briefs

DEFAULT_BRIEFS = {
    "proposer": """\
You are the PROPOSER in a recorded two-model deliberation. Rules:
- Deliberate and inspect; do not edit project/application files or mutate
  external systems. Only casefile filings and transient read-only test
  artifacts are permitted writes.
- Treat the frozen manifest as a coverage contract, not a suggestion. Compare
  every alternative against the same criteria and call out inferred rather
  than user-confirmed weights.
- File every ranking-driving claim you make: `{cli} add -t hypothesis
  -a {name} "<claim>" --claim-mode causal-inference --mechanism "…"
  --comparator "…" --analysis-layer "…" --falsifier "…"
  --counterfactual "…" --horizon "…" --testability within-session`.
- File decisions/constraints/questions likewise (author {name}); ground truth
  only as observations with --source and, when available, structured source
  provenance. Never edit .casefile/log.jsonl by hand.
- Attack the critic's leading hypothesis harder than you defend your own —
  dispute via `{cli} dispute <id> -a {name} --reason "…"`.
- Endorse the other model's claims only when genuinely persuaded
  (`{cli} endorse <id> -a {name}`); agreement is not verification.
- Separate observation, causal inference, and value judgment. State the
  counterfactual and the evidence that would change your mind.
- Never turn a model recommendation into a user decision. Only the user can
  author a user decision.
""",
    "critic": """\
You are the CRITIC in a recorded two-model deliberation. Rules:
- Deliberate and inspect; do not edit project/application files or mutate
  external systems. Only casefile filings and transient read-only test
  artifacts are permitted writes.
- Audit the frozen manifest before ranking anything. Your job includes finding
  missing requirements, criteria, evidence domains, and asymmetric treatment
  of alternatives.
- Break the proposer's leading hypothesis: find the discriminating test,
  counterexample, causal bundle, or unstated assumption. File disputes via
  `{cli} dispute <id> -a {name} --reason "…"`.
- File your own alternatives as hypotheses (author {name}); endorse the
  proposer's claims only when they withstand your attack.
- Give rival packages the same implementation detail and safeguards. Separate
  observations, causal inferences, and normative weights; state falsifiers and
  counterfactuals for ranking-driving claims.
- Ground truth only as observations with --source and structured provenance
  where available. Never edit the log by hand.
- Never turn a model recommendation into a user decision. Only the user can
  author a user decision.
""",
}


def role_brief(root: Path, role: str, model: str) -> str:
    p = root / ".casefile" / "roles" / f"{role}.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_BRIEFS[role])
    # casefile author id: version nicknames collapse to family (grok45→grok)
    author = cf.normalize_author(model) if hasattr(cf, "normalize_author") else model
    return (p.read_text()
            .replace("{name}", author)
            .replace("{cli}", CLI_STR))


# -------------------------------------------------------------------- driver

def converged(root: Path, case: str, since_n: int = 0) -> bool:
    """§12.1: no open disputes; leading hypothesis endorsed or verified.
    Only hypotheses filed at/after log position `since_n` count — a settled
    claim from before this deliberation must not converge it (485f4fbc)."""
    entries = cf.read_entries(root)
    new_ids = {e["id"] for e in entries[since_n:]}
    ce = [e for e in entries if e["case"] == case]
    grades = cf.compute_grades(entries)
    _, ds = cf.open_items(ce)
    if ds:
        return False
    hyps = [e for e in ce if e["type"] == "hypothesis"
            and e["id"] in new_ids
            and grades[e["id"]] not in ("refuted",)]
    return any(grades[h["id"]] in ("verified", "consensus") for h in hyps)


def seed_context(root: Path, case: str, topic: str, blind: bool) -> str:
    cmd = CLI + ["resume-context"]
    if blind:
        cmd.append("--blind")
    ctx = subprocess.run(cmd, cwd=root, capture_output=True, text=True).stdout
    rec = subprocess.run(CLI + ["recall", topic], cwd=root,
                         capture_output=True, text=True).stdout
    seed = f"TOPIC: {topic}\n\n{ctx}"
    if rec and "no matches" not in rec:
        seed += f"\nPRIOR CASES (compost matches — dig before re-treading):\n{rec}"
    return seed


def recovery_contexts(root: Path, session: str,
                      models: tuple[str, str]) -> tuple[dict[str, str], dict]:
    """Reconstruct each model's private view from an interrupted run journal."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session or ""):
        raise SystemExit("invalid spitball session id")
    tdir = root / ".casefile" / "transcripts" / session
    path = tdir / "run.json"
    if not path.is_file():
        raise SystemExit(f"no run journal for spitball session {session!r}")
    try:
        old = json.loads(path.read_text())
    except json.JSONDecodeError as ex:
        raise SystemExit(f"corrupt run journal {path}: {ex}") from ex
    if tuple(old.get("models", ())) != tuple(models):
        raise SystemExit("recovery model identities do not match the old run")
    out = {}
    for model in models:
        blocks = [
            "RECOVERY CONTEXT FROM DURABLE SPITBALL JOURNAL",
            f"Prior session: {session}",
            "This is transcript replay into a fresh vendor session. Treat "
            "completed replies as prior conversation; do not assume a pending "
            "call completed or that model output is casefile ground truth.",
        ]
        for call in old.get("calls", []):
            if call.get("model") != model:
                continue
            blocks.append(
                f"\n--- call {call.get('seq')} [{call.get('phase')}] "
                f"status={call.get('status')}\nPROMPT:\n"
                f"{call.get('prompt', '')}")
            if call.get("status") == "completed":
                blocks.append("REPLY:\n" + call.get("reply", ""))
            else:
                blocks.append("REPLY: [NOT DURABLY RECORDED — re-evaluate]")
        out[model] = "\n".join(blocks)
    return out, old


def recover(session: str, turns: int | None = None,
            budget_usd: float | None = None, fake_script: str | None = None,
            root: Path | None = None) -> dict:
    """Resume semantically from an interrupted run using its atomic journal.

    Vendor sessions are still used continuously inside each run. Across a
    driver/process crash, journal replay is the portable recovery mechanism:
    it works even when a vendor session cannot be reattached.
    """
    root = root or cf.find_root()
    if root is None:
        raise SystemExit("no .casefile here")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session or ""):
        raise SystemExit("invalid spitball session id")
    path = root / ".casefile" / "transcripts" / session / "run.json"
    if not path.is_file():
        raise SystemExit(f"no run journal for spitball session {session!r}")
    old = json.loads(path.read_text())
    if old.get("status") == "complete":
        raise SystemExit(
            f"session {session} already completed; inspect {path} instead")
    meta = cf.load_meta(root)
    if cf.load_active(root, meta) != old.get("case"):
        raise SystemExit(
            f"active case must be {old.get('case')!r} before recovery")
    models = tuple(old.get("models") or ())
    if len(models) != 2:
        raise SystemExit("old run journal does not name exactly two models")
    contexts, old = recovery_contexts(root, session, models)
    remaining = max(
        0, int(old.get("turn_budget", 0))
        - int(old.get("rounds_completed", 0)))
    if turns is None:
        turns = remaining
    old_tdir = (
        root / ".casefile" / "transcripts" / session).resolve()
    manifest_file = (
        old_tdir / str(old.get("manifest") or "manifest.json")).resolve()
    if old_tdir not in manifest_file.parents or not manifest_file.is_file():
        raise SystemExit(
            "old run journal manifest must resolve to a file inside its "
            "session directory")
    return run(
        topic=old["topic"], models=models, turns=turns,
        budget_usd=budget_usd if budget_usd is not None
        else old.get("budget_usd"),
        fake_script=fake_script, root=root,
        manifest_path=str(manifest_file),
        manifest_mode=old.get("manifest_mode", "warn"),
        recovery_from=session, recovery_views=contexts,
    )


def run(topic: str, models=("claude", "codex"), turns: int = 6,
        budget_usd: float | None = None, blind: str | None = None,
        fake_script: str | None = None, root: Path | None = None,
        manifest_path: str | None = None, requirements=(), criteria=(),
        alternatives=(), evidence_domains=(), analysis_layers=(),
        open_questions=(), weighting=None, manifest_mode: str = "warn",
        output_retries: int = 1, recovery_from: str | None = None,
        recovery_views: dict[str, str] | None = None) -> dict:
    root = root or cf.find_root()
    if root is None:
        raise SystemExit("no .casefile here (run `casefile init`)")
    meta = cf.load_meta(root)
    case = cf.load_active(root, meta)
    if not case:
        raise SystemExit("no active case (run `casefile open`)")
    if len(models) != 2:
        raise SystemExit("spitball needs exactly two models")
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", m or "")
           for m in models):
        raise SystemExit("model names must be safe channel identifiers")
    if turns < 0:
        raise SystemExit("--turns must be non-negative")
    if output_retries < 0:
        raise SystemExit("--output-retries must be non-negative")
    if budget_usd is not None and budget_usd < 0:
        raise SystemExit("--budget-usd must be non-negative")
    a_name, b_name = models
    if cf.normalize_author(a_name) == cf.normalize_author(b_name):
        raise SystemExit("spitball models must resolve to different author identities")

    try:
        manifest = build_manifest(
            root, case, topic, manifest_path=manifest_path,
            requirements=requirements, criteria=criteria,
            alternatives=alternatives, evidence_domains=evidence_domains,
            analysis_layers=analysis_layers, open_questions=open_questions,
            weighting=weighting, mode=manifest_mode)
    except ValueError as ex:
        raise SystemExit(str(ex)) from ex

    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    tdir = root / ".casefile" / "transcripts" / session
    tdir.mkdir(parents=True, mode=0o700, exist_ok=False)
    for m in models:  # channels exist from turn one (§14 viewport)
        (tdir / f"{m}.log").touch(mode=0o600)
    _atomic_json(tdir / "manifest.json", manifest)
    print(f"viewport channels: casefile channel {' | '.join(models)} | state")

    fake = Path(fake_script) if fake_script else None
    A = make_adapter(a_name, root, fake)
    B = make_adapter(b_name, root, fake)
    roles = {a_name: "proposer", b_name: "critic"}
    adapters = {a_name: A, b_name: B}
    preflight_specs = {}
    for model in models:
        nonce = hashlib.sha256(
            f"{session}:{model}:casefile-preflight".encode()).hexdigest()
        preflight_specs[model] = {
            "nonce": nonce,
            "path": tdir / f"preflight-{model}.json",
            "author": cf.normalize_author(model),
        }
    allowed_coverage = set(manifest["required_coverage"])
    # Envelope validation permits optional/non-required rows too.
    allowed_coverage.update(r["id"] for r in manifest["coverage_rows"])
    manifest_entry_refs = {
        item["entry_id"]
        for field in MANIFEST_LIST_FIELDS
        for item in manifest[field]
        if item.get("entry_id")
    }

    start_n = len(cf.read_entries(root))
    journal = RunJournal(tdir / "run.json", {
        "schema": "casefile-spitball-run/1",
        "session": session,
        "case": case,
        "topic": topic,
        "models": list(models),
        "roles": roles,
        "status": "initializing",
        "phase": "preflight",
        "started_at": _utcnow(),
        "start_log_position": start_n,
        "turn_budget": turns,
        "budget_usd": budget_usd,
        "manifest": "manifest.json",
        "manifest_mode": manifest_mode,
        "manifest_warnings": manifest["warnings"],
        "recovery_from": recovery_from,
        "recovery_mode": "journal-replay" if recovery_from else None,
        "calls": [],
        "coverage": {a_name: [], b_name: []},
        "summary_coverage": {a_name: [], b_name: []},
        "adapter_handles": {},
        "adapter_preflight": {},
        "telemetry_anomalies": [],
        "rounds_completed": 0,
        "finalization": {"status": "not-attempted"},
    })
    print(f"spitball session: {session} | journal: {tdir / 'run.json'}")

    def log_t(model, tag, text):
        with (tdir / f"{model}.log").open("a") as f:
            f.write(f"--- {tag} {_utcnow()}\n{text}\n")
            f.flush()
            os.fsync(f.fileno())
        sample = (text or "").strip()
        print(f"[{model}] {sample[:400]}" + ("…" if len(sample) > 400 else ""))

    ha = hb = None
    handles = {a_name: None, b_name: None}
    last_cost = {
        a_name: {"usd": 0.0, "tokens": 0},
        b_name: {"usd": 0.0, "tokens": 0},
    }
    coverage = {a_name: set(), b_name: set()}
    summary_coverage = {a_name: set(), b_name: set()}

    def spend():
        costs = []
        for model, ad in adapters.items():
            h = handles[model]
            if h is not None:
                costs.append(ad.cost(h))
        return (sum(x.get("usd") or 0.0 for x in costs),
                sum(x.get("tokens") or 0 for x in costs))

    def cache_tokens():
        return sum(
            int((handles[m] or {}).get("cache_read_tokens", 0))
            for m in models)

    def note_cost(model, cost):
        before = last_cost[model]
        delta_tokens = max(0, (cost.get("tokens") or 0)
                           - (before.get("tokens") or 0))
        delta_usd = max(0.0, (cost.get("usd") or 0.0)
                        - (before.get("usd") or 0.0))
        if delta_tokens > 1_000_000:
            journal.data["telemetry_anomalies"].append({
                "model": model, "at": _utcnow(), "kind": "token-spike",
                "delta_tokens": delta_tokens, "delta_usd": delta_usd,
            })
        last_cost[model] = dict(cost)

    def invoke_start(model, prompt, tag):
        ad = adapters[model]
        seq = journal.begin_call(model, tag, prompt)
        try:
            h = ad.start(prompt)
            handles[model] = h
            reply = h.get("reply", "")
            cost = ad.cost(h)
            note_cost(model, cost)
            journal.finish_call(seq, reply, h, cost)
            log_t(model, tag, reply)
            return h, reply, seq
        except BaseException as ex:
            journal.fail_call(seq, ex)
            raise

    def invoke_send(model, prompt, tag):
        ad, h = adapters[model], handles[model]
        seq = journal.begin_call(model, tag, prompt)
        try:
            reply = ad.send(h, prompt)
            cost = ad.cost(h)
            note_cost(model, cost)
            journal.finish_call(seq, reply, h, cost)
            log_t(model, tag, reply)
            return reply, seq
        except BaseException as ex:
            journal.fail_call(seq, ex)
            raise

    def accept_envelope(model, envelope, seq, summary=False):
        covered = set(envelope.get("coverage", []))
        coverage[model].update(covered)
        if summary:
            summary_coverage[model] = covered
        journal.data["coverage"][model] = sorted(coverage[model])
        journal.data["summary_coverage"][model] = sorted(summary_coverage[model])
        journal.annotate_call(seq, validation="accepted", envelope=envelope)

    def preflight_command(model):
        spec = preflight_specs[model]
        return (
            f"{CLI_STR} preflight -a {shlex.quote(spec['author'])} --json "
            f"--receipt {shlex.quote(str(spec['path']))} "
            f"--nonce {shlex.quote(spec['nonce'])}")

    def preflight_receipt_error(model) -> str | None:
        spec = preflight_specs[model]
        try:
            receipt = json.loads(spec["path"].read_text())
        except (OSError, json.JSONDecodeError):
            return "adapter did not create a valid preflight receipt"
        if receipt.get("nonce") != spec["nonce"]:
            return "adapter preflight receipt nonce mismatch"
        if Path(str(receipt.get("root", ""))).resolve() != root.resolve():
            return "adapter preflight receipt root mismatch"
        if cf.normalize_author(str(receipt.get("author", ""))) != spec["author"]:
            return "adapter preflight receipt author mismatch"
        if not receipt.get("ok") or not receipt.get("state_writable") \
                or not receipt.get("log_appendable") \
                or not receipt.get("log_lockable"):
            return "adapter preflight did not prove casefile writeability"
        return None

    def validate_or_repair(model, reply, seq, contract_phase,
                           require_preflight=False):
        ad = adapters[model]
        if not getattr(ad, "strict_output", False):
            journal.annotate_call(seq, validation="skipped")
            return reply, {}
        attempts = 0
        while True:
            ok, reason, envelope = _validate_model_reply(
                reply, contract_phase, allowed_coverage,
                expected_rounds=(
                    ["opening", *[
                        f"round-{i}" for i in range(
                            1, journal.data["rounds_completed"] + 1)
                    ]]
                    if contract_phase == "summary" else None))
            if require_preflight and PREFLIGHT_MARKER not in reply:
                ok, reason = False, f"missing {PREFLIGHT_MARKER} marker"
            if require_preflight and ok:
                receipt_error = preflight_receipt_error(model)
                if receipt_error:
                    ok, reason = False, receipt_error
            if ok:
                accept_envelope(model, envelope, seq,
                                summary=contract_phase == "summary")
                if require_preflight:
                    receipt = json.loads(
                        preflight_specs[model]["path"].read_text())
                    journal.data["adapter_preflight"][model][
                        "model_write_receipt"] = receipt
                    journal.flush()
                return reply, envelope
            journal.annotate_call(seq, validation="rejected",
                                  validation_error=reason)
            if attempts >= output_retries:
                raise RuntimeError(
                    f"{model} {contract_phase} output invalid after "
                    f"{attempts + 1} attempt(s): {reason}")
            attempts += 1
            marker = SUMMARY_MARKER if contract_phase == "summary" else TURN_MARKER
            repair = (
                "Your previous response failed the deliberation output contract: "
                f"{reason}. Provide the substantive answer now, not a progress "
                f"update or filing receipt. End with one valid single-line {marker} "
                "JSON object using only manifest row ids."
            )
            if require_preflight:
                repair += (
                    f" First run `{preflight_command(model)}` and include "
                    f"{PREFLIGHT_MARKER} in the answer.")
            reply, seq = invoke_send(
                model, repair, f"{contract_phase}-repair-{attempts}")

    def validated_start(model, prompt):
        h, reply, seq = invoke_start(model, prompt, "seed-reply")
        reply, envelope = validate_or_repair(
            model, reply, seq, "turn", require_preflight=True)
        if getattr(adapters[model], "strict_output", False):
            public = _public_handle(h)
            if not (public.get("sid") or public.get("tid")):
                raise RuntimeError(
                    f"{model} adapter did not expose a persistent session id")
        h["reply"] = reply
        return h, reply, envelope

    def validated_send(model, prompt, tag, contract_phase="turn"):
        reply, seq = invoke_send(model, prompt, tag)
        return validate_or_repair(model, reply, seq, contract_phase)

    def drop_status(turn):
        try:  # best-effort: feeds the §14 status bar; never blocks the drive
            usd, tokens = spend()
            ui = root / ".casefile" / "ui"
            ui.mkdir(parents=True, exist_ok=True)
            _atomic_json(ui / "spitball.json", {
                "models": "+".join(models), "turn": turn,
                "spend_usd": round(usd, 4), "tokens": tokens,
                "cache_read_tokens": cache_tokens(),
                "session": session, "manifest_warnings": len(manifest["warnings"]),
            })
        except Exception:
            pass

    outcome = "aborted"
    independent_summaries = {}
    summaries = {}
    summary_divergence = True
    finalization = {"status": "not-attempted", "blockers": []}
    try:
        # Static checks happen before the first paid/model call.
        for model, ad in adapters.items():
            report = adapter_preflight(ad, root)
            journal.data["adapter_preflight"][model] = report
            journal.flush()

        contract = manifest_prompt(manifest)
        probe = (
            f"\nBefore arguing, run `{preflight_command(a_name)}` to prove "
            "this session can "
            "read and write the correct casefile root. This probe is "
            "non-epistemic and files no log entry. Include "
            f"the literal marker {PREFLIGHT_MARKER} in your opening answer.\n")
        ha, a_open, a_env = validated_start(
            a_name,
            role_brief(root, roles[a_name], a_name)
            + "\n" + seed_context(root, case, topic, blind == a_name)
            + ("\n" + (recovery_views or {}).get(a_name, "")
               if recovery_from else "")
            + contract + probe
            + "\nBegin with a coverage audit and your opening position.")

        probe_b = (
            f"\nBefore arguing, run `{preflight_command(b_name)}` to prove "
            "this session can "
            "read and write the correct casefile root. This probe is "
            "non-epistemic and files no log entry. Include "
            f"the literal marker {PREFLIGHT_MARKER} in your opening answer.\n")
        hb, b_open, b_env = validated_start(
            b_name,
            role_brief(root, roles[b_name], b_name)
            + "\n" + seed_context(root, case, topic, blind == b_name)
            + ("\n" + (recovery_views or {}).get(b_name, "")
               if recovery_from else "")
            + contract + probe_b
            + f"\nThe {a_name} opening was:\n"
            + _ferry_payload(a_open, a_env) + "\n"
            + "\nAudit its coverage and symmetry, then give your opening critique.")

        # Ferry complete, validated replies only.
        msg_to_a = _ferry_payload(b_open, b_env)
        outcome, idle_rounds = "turn-budget", 0
        entries_before = len(cf.read_entries(root))
        for turn in range(turns):
            drop_status(turn)
            if converged(root, case, start_n):
                outcome = "converged"
                break
            usd, _ = spend()
            if budget_usd is not None and usd >= budget_usd:
                outcome = "spend-budget"
                break
            ra, ra_env = validated_send(
                a_name, f"[{b_name} says]:\n{msg_to_a}",
                f"round-{turn + 1}-proposer")
            rb, rb_env = validated_send(
                b_name, f"[{a_name} says]:\n"
                + _ferry_payload(ra, ra_env),
                f"round-{turn + 1}-critic")
            msg_to_a = _ferry_payload(rb, rb_env)
            n = len(cf.read_entries(root))
            idle_rounds = idle_rounds + 1 if n == entries_before else 0
            entries_before = n
            journal.data["rounds_completed"] = turn + 1
            journal.flush()
            if idle_rounds >= 2:
                outcome = "stalemate"
                break
        else:
            if converged(root, case, start_n):
                outcome = "converged"

        # Detailed, independent round ledger. Each model still has only its
        # private continuous session and has not seen the other's summary.
        expected_summary_rounds = [
            "opening",
            *[f"round-{i}" for i in range(
                1, journal.data["rounds_completed"] + 1)],
        ]
        summary_shape = json.dumps({
            "coverage": manifest["required_coverage"],
            "rounds": expected_summary_rounds,
            "decided": [],
            "ruled_out": [],
            "open": [],
            "conclusion_class": "model-recommendation",
        }, separators=(",", ":"))
        summary_prompt = (
            "Deliberation phase complete. WITHOUT consulting the other model's "
            "summary, write a verbose independent synopsis. Include one section "
            "per round covering each side's argument, evidence, assumptions, "
            "concessions, falsifiers, and unresolved points; then a manifest "
            "coverage matrix and final decided / ruled-out / open sections. "
            f"Cover exactly these ledger sections: {expected_summary_rounds}. "
            "Do not file anything during this summary. Do not upgrade model "
            "agreement into a user decision. In decided/ruled_out/open, use "
            "exact casefile entry ids wherever a filed entry exists; otherwise "
            "use a concise proposition, never a placeholder. End with one "
            "single-line "
            f"{SUMMARY_MARKER} {summary_shape}. Remove any coverage id you did "
            "not actually substantiate.")
        sa, ea = validated_send(
            a_name, summary_prompt, "independent-summary", "summary")
        sb, eb = validated_send(
            b_name, summary_prompt, "independent-summary", "summary")
        independent_summaries = {a_name: sa, b_name: sb}
        summaries = dict(independent_summaries)
        summary_divergence = _diff_summaries(sa, sb)

        # One bounded reconciliation pass. The original independent summaries
        # remain in the journal/result for audit.
        if summary_divergence:
            reconcile = (
                "The independent summaries diverged. Compare both raw summaries "
                "below against your own session and the casefile log. Produce a "
                "corrected, detailed synthesis; preserve genuine minority views "
                "as open rather than forcing agreement. Do not file entries in "
                "this step. End with the required CASEFILE_SUMMARY_JSON line.\n\n"
                f"{a_name} SUMMARY:\n{sa}\n\n{b_name} SUMMARY:\n{sb}")
            rsa, rea = validated_send(
                a_name, reconcile, "summary-reconciliation", "summary")
            rsb, reb = validated_send(
                b_name, reconcile, "summary-reconciliation", "summary")
            summaries = {a_name: rsa, b_name: rsb}
            sa, sb, ea, eb = rsa, rsb, rea, reb
            summary_divergence = _diff_summaries(sa, sb)

        # Role-neutral secretary sweeps: neither proposer nor critic is the
        # sole custodian of what enters the log.
        sweep_prompt = (
            "Secretary sweep for YOUR own contributions: diff this entire "
            "deliberation against the casefile log. File every missing claim, "
            "constraint, decision, observation, question, concession, or ruled-"
            "out alternative with correct type and author. File a note marker "
            "'secretary sweep: gaps filed' or 'secretary sweep: nothing "
            "unrecorded'. Do NOT create any digest in this step. Report the ids "
            "and audit result, ending with CASEFILE_TURN_JSON.")
        validated_send(a_name, sweep_prompt, "secretary-sweep")
        validated_send(b_name, sweep_prompt, "secretary-sweep")

        blockers = _finalization_blockers(
            root, case, start_n, manifest, coverage, summary_coverage,
            summary_divergence, outcome)
        required_span = set(_eligible_digest_span(
            cf.read_entries(root), case, start_n))
        if not required_span:
            blockers.append("no digestible session argument entries were filed")

        finalization = {
            "status": "blocked" if blockers else "candidate-pending",
            "blockers": blockers,
            "candidate_digest_id": None,
            "review_id": None,
            "judgment_digest_id": None,
            "conclusion_class": None,
        }
        journal.data["finalization"] = finalization
        journal.flush()

        if not blockers:
            span_text = " ".join(sorted(required_span))
            refs_text = " ".join(
                f"--ref {ref}" for ref in sorted(manifest_entry_refs))
            before_candidate = len(cf.read_entries(root))
            candidate_prompt = (
                "All mechanical finalization gates passed. Propose a CANDIDATE "
                "judgment that faithfully preserves the settled span, explicit "
                "minority qualifications, manifest criteria, evidence grades, "
                "falsifiers, and implementation risks. It is a model "
                "recommendation, never a user decision. File exactly one inert "
                f"candidate with `{CLI_STR} digest --body-stdin -a "
                f"{cf.normalize_author(a_name)} --kind candidate --supersedes "
                f"{span_text} {refs_text}` (pipe the full digest body on stdin). "
                "The --ref ids bind the recommendation to the frozen casefile "
                "requirements so later replacements can mark it stale. The candidate "
                "does not hide anything until exact independent review. Explain "
                "the proposed judgment and report its exact id, ending with "
                "CASEFILE_TURN_JSON.")
            validated_send(a_name, candidate_prompt, "candidate-digest")
            entries = cf.read_entries(root)
            candidate, error = _candidate_digest(
                entries, before_candidate, case, a_name, required_span,
                manifest_entry_refs)
            if candidate is None and "found 0" in error:
                validated_send(
                    a_name,
                    "No candidate digest was found. File exactly one now using "
                    "the command and exact span from the prior message; report "
                    "the id and end with CASEFILE_TURN_JSON.",
                    "candidate-digest-repair")
                entries = cf.read_entries(root)
                candidate, error = _candidate_digest(
                    entries, before_candidate, case, a_name, required_span,
                    manifest_entry_refs)
            if candidate is None:
                finalization["status"] = "blocked"
                finalization["blockers"].append(error)
            else:
                finalization["candidate_digest_id"] = candidate["id"]
                before_review = len(entries)
                review_prompt = (
                    f"Adversarially review the exact candidate `{candidate['id']}` "
                    "against the frozen manifest, both summaries, and raw session "
                    f"entries {span_text}. Do not review 'newest' or any fallback "
                    "digest. Check for dropped claims, epistemic upgrades, package "
                    "asymmetry, causal bundling, missing falsifiers, and "
                    "recommendation/user-decision confusion. Endorse that exact id "
                    "only if it passes; otherwise dispute that exact id with a "
                    "specific reason. Report the review id and end with "
                    "CASEFILE_TURN_JSON.")
                validated_send(b_name, review_prompt, "candidate-review")
                entries = cf.read_entries(root)
                verdict, review_id = _critic_verdict(
                    entries, before_review, candidate["id"], b_name)
                if verdict is None:
                    validated_send(
                        b_name,
                        f"No review entry targeting `{candidate['id']}` was found. "
                        "Endorse or dispute that exact candidate now via the CLI; "
                        "report the id and end with CASEFILE_TURN_JSON.",
                        "candidate-review-repair")
                    entries = cf.read_entries(root)
                    verdict, review_id = _critic_verdict(
                        entries, before_review, candidate["id"], b_name)
                finalization["review_id"] = review_id
                if verdict == "endorsed":
                    p = subprocess.run(
                        CLI + ["finalize-digest", candidate["id"]],
                        cwd=root, capture_output=True, text=True)
                    if p.returncode != 0:
                        finalization["status"] = "blocked"
                        finalization["blockers"].append(
                            "mechanical promotion failed: " + p.stderr.strip())
                    else:
                        finalization.update({
                            "status": "finalized",
                            "judgment_digest_id": p.stdout.strip().splitlines()[-1],
                            "conclusion_class": "cross-model-consensus",
                        })
                elif verdict == "disputed":
                    finalization["status"] = "critic-disputed"
                    finalization["blockers"].append(
                        f"critic disputed exact candidate via {review_id}")
                else:
                    finalization["status"] = "blocked"
                    finalization["blockers"].append(
                        "critic created no exact endorsement/dispute")

        usd, tokens = spend()
        journal.set(
            status="complete", phase="complete", outcome=outcome,
            spend_usd=round(usd, 4), tokens=tokens,
            cache_read_tokens=cache_tokens(),
            summaries=summaries,
            independent_summaries=independent_summaries,
            summary_divergence=summary_divergence,
            finalization=finalization,
            completed_at=_utcnow(),
        )
    except BaseException as ex:
        # A pending call was already journaled before invocation. Preserve a
        # top-level terminal status for ordinary exceptions too; SIGKILL leaves
        # status=running/pending, which is itself a precise recovery signal.
        try:
            journal.set(status="aborted", phase="aborted", outcome="aborted",
                        error=f"{type(ex).__name__}: {ex}",
                        aborted_at=_utcnow())
        except Exception:
            pass
        raise
    finally:
        for model, ad in adapters.items():
            h = handles[model]
            if h is not None:
                try:
                    ad.stop(h)
                except Exception:
                    pass

    usd, tokens = spend()
    result = {
        "outcome": outcome, "case": case, "session": session,
        "transcripts": str(tdir), "journal": str(tdir / "run.json"),
        "manifest": str(tdir / "manifest.json"),
        "manifest_warnings": manifest["warnings"],
        "spend_usd": round(usd, 4), "tokens": tokens,
        "cache_read_tokens": cache_tokens(),
        "summaries": summaries,
        "independent_summaries": independent_summaries,
        "summary_divergence": summary_divergence,
        "coverage": {m: sorted(v) for m, v in coverage.items()},
        "finalization": finalization,
    }
    print("\n=== VERBOSE INDEPENDENT ROUND SYNOPSES ===")
    for model in models:
        print(f"\n--- {model} (independent) ---\n"
              f"{independent_summaries.get(model, '')}")
    if summaries != independent_summaries:
        print("\n=== RECONCILED SYNOPSES ===")
        for model in models:
            print(f"\n--- {model} (reconciled) ---\n"
                  f"{summaries.get(model, '')}")
    print(json.dumps({
        "outcome": outcome, "session": session,
        "journal": str(tdir / "run.json"),
        "spend_usd": round(usd, 4), "tokens": tokens,
        "cache_read_tokens": cache_tokens(),
        "summary_divergence": summary_divergence,
        "finalization": finalization["status"],
    }))
    return result


def _diff_summaries(sa: str, sb: str) -> bool:
    """Conservative deterministic divergence gate for independent synopses.

    Shared headings and protocol rows are deliberately ignored. Exact casefile
    ids must remain in the same outcome bucket; otherwise distinctive content
    terms must overlap. False positives merely preserve an open differential,
    while a false negative could manufacture a judgment.
    """
    structural = {
        "argument", "arguments", "assumption", "assumptions", "concession",
        "concessions", "coverage", "critic", "decided", "deliberation",
        "evidence", "falsifier", "falsifiers", "final", "manifest", "matrix",
        "open", "point", "points", "proposer", "round", "rounds", "ruled",
        "ruled-out",
        "section", "sections", "side", "sides", "summary", "unresolved",
        "about", "after", "against", "because", "before", "could", "explicit",
        "explicitly", "their", "there", "these", "through", "under", "which",
        "while", "would",
    }
    label_heads = {
        "alternative", "approach", "choose", "design", "model", "option",
        "package", "prefer", "proposal", "select", "system",
    }
    negations = {"avoid", "never", "no", "not", "reject", "rejected", "without"}

    def terms(value: str) -> set[str]:
        raw = re.findall(
            r"[A-Za-z0-9]+(?:[_.:-][A-Za-z0-9]+)*", value or "")
        lowered = [x.lower() for x in raw]
        out = {
            x for x in lowered
            if len(x) > 3 and x not in structural and x not in label_heads
        }
        for i, token in enumerate(lowered[:-1]):
            nxt = lowered[i + 1]
            if token in label_heads and nxt not in structural:
                out.add(f"{token}:{nxt}")
            if token in negations:
                out.add(f"neg:{nxt}")
        return out

    def prose(value: str) -> str:
        # Identical protocol metadata (especially coverage ids) must not
        # manufacture semantic agreement between divergent prose.
        return "\n".join(
            line for line in (value or "").splitlines()
            if SUMMARY_MARKER not in line and TURN_MARKER not in line)

    def meaningful(values) -> list[str]:
        none_words = {"none", "nothing", "n/a", "no open items"}
        return [
            x for x in (values or [])
            if isinstance(x, str) and x.strip().lower() not in none_words
        ]

    ea = _extract_json_marker(sa, SUMMARY_MARKER)
    eb = _extract_json_marker(sb, SUMMARY_MARKER)
    if ea and eb:
        id_bucket_a, id_bucket_b = {}, {}
        for key in ("decided", "ruled_out", "open"):
            va, vb = meaningful(ea.get(key)), meaningful(eb.get(key))
            if bool(va) != bool(vb):
                return True
            for value in va:
                for eid in re.findall(r"\b[0-9a-f]{8}\b", value.lower()):
                    id_bucket_a[eid] = key
            for value in vb:
                for eid in re.findall(r"\b[0-9a-f]{8}\b", value.lower()):
                    id_bucket_b[eid] = key
            if va and vb:
                ta, tb = terms(" ".join(va)), terms(" ".join(vb))
                if not ta or not tb:
                    if {x.strip().casefold() for x in va} != {
                            x.strip().casefold() for x in vb}:
                        return True
                elif len(ta & tb) / min(len(ta), len(tb)) < 0.35:
                    return True
        if id_bucket_a != id_bucket_b and (id_bucket_a or id_bucket_b):
            return True

    ka, kb = terms(prose(sa)), terms(prose(sb))
    if not ka or not kb:
        return True
    overlap = len(ka & kb) / min(len(ka), len(kb))
    return overlap < 0.35
