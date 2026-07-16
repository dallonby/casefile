#!/usr/bin/env python3
"""statelog — append-only, epistemically-graded state log for long agentic tasks.

Source of truth is .statelog/log.jsonl (one entry per line, never edited).
Grades are computed from the log at read time, never stored.
Stdlib only. See SCHEMA.md for the design.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = ".statelog"
LOG_FILE = "log.jsonl"
META_FILE = "meta.json"

ENTRY_TYPES = {
    "hypothesis", "decision", "observation", "constraint", "question",
    "endorsement", "dispute", "resolution", "verification", "note",
}

# ---------------------------------------------------------------- storage

def find_root(start: Path | None = None) -> Path | None:
    """Walk upward looking for a .statelog directory (git-style)."""
    p = (start or Path.cwd()).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / LOG_DIR).is_dir():
            return candidate
    return None


def log_path(root: Path) -> Path:
    return root / LOG_DIR / LOG_FILE


def read_entries(root: Path) -> list[dict]:
    path = log_path(root)
    if not path.exists():
        return []
    entries = []
    with path.open() as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                sys.exit(f"error: corrupt log line {lineno} in {path}")
    return entries


def append_entry(root: Path, entry: dict) -> None:
    with log_path(root).open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def new_id(existing: set[str], body: str) -> str:
    n = 0
    while True:
        h = hashlib.sha256(f"{time.time_ns()}:{n}:{body}".encode()).hexdigest()[:8]
        if h not in existing:
            return h
        n += 1


def make_entry(entries: list[dict], type_: str, author: str, body: str,
               refs: list[str] | None = None, **extra) -> dict:
    ids = {e["id"] for e in entries}
    refs = refs or []
    missing = [r for r in refs if r not in ids]
    if missing:
        sys.exit(f"error: unknown ref(s): {', '.join(missing)}")
    e = {
        "id": new_id(ids, body),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": type_,
        "author": author,
        "body": body,
        "refs": refs,
    }
    e.update({k: v for k, v in extra.items() if v is not None})
    return e


# ---------------------------------------------------------------- grading

def compute_grades(entries: list[dict]) -> dict[str, str]:
    """id -> grade, derived per SCHEMA.md rules."""
    by_id = {e["id"]: e for e in entries}
    endorsements: dict[str, set[str]] = {}
    verified: set[str] = set()
    open_disputes: dict[str, list[str]] = {}   # target id -> dispute ids
    resolved: set[str] = set()                 # resolved dispute ids

    for e in entries:
        t = e["type"]
        if t == "endorsement":
            for r in e["refs"]:
                if r in by_id and e["author"] != by_id[r]["author"]:
                    endorsements.setdefault(r, set()).add(e["author"])
        elif t == "verification":
            obs = [r for r in e["refs"] if by_id.get(r, {}).get("type") == "observation"]
            targets = [r for r in e["refs"] if by_id.get(r, {}).get("type") == "hypothesis"]
            if obs:
                verified.update(targets)
        elif t == "dispute":
            for r in e["refs"]:
                open_disputes.setdefault(r, []).append(e["id"])
        elif t == "resolution":
            resolved.update(e["refs"])

    grades: dict[str, str] = {}
    for e in entries:
        eid, t = e["id"], e["type"]
        if t in ("decision", "constraint") and e["author"] == "user":
            grades[eid] = "stated"
        elif t == "observation":
            grades[eid] = "ground-truth"
        elif t == "hypothesis":
            still_open = [d for d in open_disputes.get(eid, []) if d not in resolved]
            if still_open:
                grades[eid] = "disputed"
            elif eid in verified:
                grades[eid] = "verified"
            elif endorsements.get(eid):
                grades[eid] = "consensus"
            else:
                grades[eid] = "hypothesis"
        elif t in ("decision", "constraint"):
            grades[eid] = "asserted"
    return grades


def open_items(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """(open questions, open disputes)."""
    resolved_refs = set()
    for e in entries:
        if e["type"] == "resolution":
            resolved_refs.update(e["refs"])
    qs = [e for e in entries if e["type"] == "question" and e["id"] not in resolved_refs]
    ds = [e for e in entries if e["type"] == "dispute" and e["id"] not in resolved_refs]
    return qs, ds


# ---------------------------------------------------------------- commands

def cmd_init(args):
    root = Path.cwd()
    d = root / LOG_DIR
    if d.exists():
        sys.exit(f"error: {d} already exists")
    d.mkdir()
    (d / META_FILE).write_text(json.dumps({
        "title": args.title,
        "goal": args.goal or "",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema": "0.1",
    }, indent=2) + "\n")
    log_path(root).touch()
    print(f"initialized statelog in {d}")


def require_root() -> tuple[Path, list[dict], dict]:
    root = find_root()
    if root is None:
        sys.exit("error: no .statelog found here or in any parent (run `statelog init`)")
    meta = json.loads((root / LOG_DIR / META_FILE).read_text())
    return root, read_entries(root), meta


def cmd_add(args):
    root, entries, _ = require_root()
    extra = {}
    if args.type == "decision":
        extra["rationale"] = args.rationale or ""
    if args.type == "observation":
        extra["source"] = args.source or "manual"
    e = make_entry(entries, args.type, args.author, args.body,
                   refs=args.refs, **extra)
    append_entry(root, e)
    print(e["id"])


def _simple_ref_cmd(args, type_: str, **extra):
    root, entries, _ = require_root()
    e = make_entry(entries, type_, args.author, extra.pop("body"),
                   refs=[args.entry], **extra)
    append_entry(root, e)
    print(e["id"])


def cmd_endorse(args):
    root, entries, _ = require_root()
    by_id = {e["id"]: e for e in entries}
    target = by_id.get(args.entry)
    if not target:
        sys.exit(f"error: unknown entry {args.entry}")
    if target["author"] == args.author:
        sys.exit("error: self-endorsement carries no weight; get another author")
    _simple_ref_cmd(args, "endorsement", body=args.comment or f"endorses {args.entry}")


def cmd_dispute(args):
    _simple_ref_cmd(args, "dispute", body=args.reason)


def cmd_resolve(args):
    root, entries, _ = require_root()
    by_id = {e["id"]: e for e in entries}
    target = by_id.get(args.entry)
    if not target or target["type"] not in ("dispute", "question"):
        sys.exit(f"error: {args.entry} is not a dispute or question")
    e = make_entry(entries, "resolution", args.author, args.reason,
                   refs=[args.entry], outcome=args.outcome)
    append_entry(root, e)
    print(e["id"])


def cmd_verify(args):
    root, entries, _ = require_root()
    by_id = {e["id"]: e for e in entries}
    if by_id.get(args.entry, {}).get("type") != "hypothesis":
        sys.exit(f"error: {args.entry} is not a hypothesis")
    obs = by_id.get(args.observation)
    if not obs or obs["type"] != "observation":
        sys.exit(f"error: {args.observation} is not an observation; "
                 "verification requires ground truth (`statelog add -t observation ...` first)")
    e = make_entry(entries, "verification", args.author,
                   args.comment or f"verified by {args.observation}",
                   refs=[args.entry, args.observation])
    append_entry(root, e)
    print(e["id"])


GRADE_ORDER = ["ground-truth", "stated", "verified", "consensus",
               "disputed", "hypothesis", "asserted"]


def cmd_show(args):
    root, entries, meta = require_root()
    grades = compute_grades(entries)
    by_type = {}
    for e in entries:
        by_type.setdefault(e["type"], []).append(e)
    qs, ds = open_items(entries)

    out = [f"# {meta['title']}", ""]
    if meta.get("goal"):
        out += [f"**Goal:** {meta['goal']}", ""]

    if by_type.get("constraint"):
        out += ["## Constraints", ""]
        out += [f"- `{e['id']}` [{grades.get(e['id'],'')}] ({e['author']}) {e['body']}"
                for e in by_type["constraint"]] + [""]

    if by_type.get("decision"):
        out += ["## Decisions", ""]
        for e in by_type["decision"]:
            line = f"- `{e['id']}` [{grades.get(e['id'],'')}] ({e['author']}) {e['body']}"
            if e.get("rationale"):
                line += f" — *{e['rationale']}*"
            out.append(line)
        out.append("")

    hyps = by_type.get("hypothesis", [])
    if hyps:
        out += ["## Differential", ""]
        for grade in GRADE_ORDER:
            group = [e for e in hyps if grades[e["id"]] == grade]
            for e in group:
                out.append(f"- `{e['id']}` **[{grade}]** ({e['author']}) {e['body']}")
        out.append("")

    if ds:
        out += ["## Open disputes", ""]
        out += [f"- `{e['id']}` ({e['author']}) disputes `{e['refs'][0]}`: {e['body']}"
                for e in ds] + [""]
    if qs:
        out += ["## Open questions", ""]
        out += [f"- `{e['id']}` ({e['author']}) {e['body']}" for e in qs] + [""]

    obs = by_type.get("observation", [])
    if obs:
        out += ["## Recent observations", ""]
        out += [f"- `{e['id']}` ({e.get('source','manual')}) {e['body']}"
                for e in obs[-args.observations:]] + [""]
    print("\n".join(out))


PROVENANCE_PHRASES = {
    "stated": "the user decided",
    "verified": "verified against ground truth",
    "consensus": "cross-model consensus — NOT independently verified",
    "disputed": "UNDER ACTIVE DISPUTE",
    "hypothesis": "an unverified hypothesis",
    "asserted": "asserted, not user-confirmed",
}


def cmd_resume_context(args):
    """Compact injection for a fresh instance. Provenance spelled out in words."""
    root, entries, meta = require_root()
    grades = compute_grades(entries)
    qs, ds = open_items(entries)
    by_type = {}
    for e in entries:
        by_type.setdefault(e["type"], []).append(e)

    out = ["You are resuming an in-progress task. Trust ground truth over these "
           "notes where they conflict; re-verify anything load-bearing.",
           "",
           f"TASK: {meta['title']}"]
    if meta.get("goal"):
        out.append(f"GOAL: {meta['goal']}")
    out.append("")

    for label, t in [("CONSTRAINTS", "constraint"), ("DECISIONS", "decision")]:
        items = by_type.get(t, [])
        if items:
            out.append(f"{label}:")
            for e in items:
                phrase = PROVENANCE_PHRASES.get(grades.get(e["id"], ""), "")
                line = f"- {e['body']} ({phrase}"
                if e.get("rationale"):
                    line += f"; rationale: {e['rationale']}"
                out.append(line + ")")
            out.append("")

    hyps = by_type.get("hypothesis", [])
    if hyps:
        out.append("CURRENT DIFFERENTIAL (grade in brackets — treat accordingly):")
        for grade in GRADE_ORDER:
            for e in (h for h in hyps if grades[h["id"]] == grade):
                out.append(f"- [{PROVENANCE_PHRASES.get(grade, grade)}] "
                           f"{e['body']} (by {e['author']})")
        out.append("")

    if ds:
        out.append("OPEN DISPUTES (resolve before relying on the disputed claim):")
        by_id = {e["id"]: e for e in entries}
        for d in ds:
            tgt = by_id.get(d["refs"][0], {})
            out.append(f"- {d['author']} disputes \"{tgt.get('body','?')}\": {d['body']}")
        out.append("")
    if qs:
        out.append("OPEN QUESTIONS:")
        out += [f"- {e['body']}" for e in qs] + [""]

    obs = by_type.get("observation", [])
    if obs:
        out.append(f"LAST {min(len(obs), args.observations)} OBSERVATIONS (ground truth):")
        out += [f"- [{e.get('source','manual')}] {e['body']}"
                for e in obs[-args.observations:]]
    print("\n".join(out))


def cmd_lint(args):
    root, entries, _ = require_root()
    grades = compute_grades(entries)
    by_id = {e["id"]: e for e in entries}
    problems = []

    # laundering: heavily-referenced but unverified hypotheses
    ref_counts: dict[str, int] = {}
    for e in entries:
        for r in e.get("refs", []):
            if e["type"] not in ("endorsement", "dispute", "verification", "resolution"):
                ref_counts[r] = ref_counts.get(r, 0) + 1
    for eid, n in ref_counts.items():
        e = by_id.get(eid)
        if e and e["type"] == "hypothesis" and grades[eid] in ("hypothesis", "consensus") \
                and n >= args.launder_threshold:
            problems.append(f"LAUNDERING  `{eid}` referenced {n}x but still "
                            f"[{grades[eid]}]: {e['body'][:70]}")

    # consensus that observations could plausibly have checked
    has_observations = any(e["type"] == "observation" for e in entries)
    if has_observations:
        for e in entries:
            if e["type"] == "hypothesis" and grades[e["id"]] == "consensus":
                problems.append(f"CONSENSUS   `{e['id']}` ground truth exists in this log "
                                f"but claim is only cross-model consensus: {e['body'][:70]}")

    # stale disputes
    _, ds = open_items(entries)
    index = {e["id"]: i for i, e in enumerate(entries)}
    for d in ds:
        age = len(entries) - index[d["id"]]
        if age >= args.stale_threshold:
            problems.append(f"STALE       dispute `{d['id']}` open for {age} entries: "
                            f"{d['body'][:70]}")

    # orphan decisions
    for e in entries:
        if e["type"] == "decision" and not e.get("refs") and not e.get("rationale"):
            problems.append(f"ORPHAN      decision `{e['id']}` has no refs and no "
                            f"rationale: {e['body'][:70]}")

    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print("clean")


def cmd_log(args):
    _, entries, _ = require_root()
    grades = compute_grades(entries)
    for e in entries[-args.n:]:
        g = grades.get(e["id"], "")
        gtxt = f" [{g}]" if g else ""
        refs = f" -> {','.join(e['refs'])}" if e.get("refs") else ""
        print(f"{e['id']}  {e['ts']}  {e['type']:<12} {e['author']:<8}{gtxt}{refs}  {e['body']}")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(prog="statelog", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create .statelog in the current directory")
    sp.add_argument("title")
    sp.add_argument("--goal")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("add", help="append an entry")
    sp.add_argument("-t", "--type", required=True, choices=sorted(ENTRY_TYPES))
    sp.add_argument("-a", "--author", required=True)
    sp.add_argument("body")
    sp.add_argument("--refs", nargs="*", default=[])
    sp.add_argument("--rationale", help="for decisions")
    sp.add_argument("--source", help="for observations (pytest, git, manual...)")
    sp.set_defaults(fn=cmd_add)

    sp = sub.add_parser("endorse", help="endorse another author's entry")
    sp.add_argument("entry")
    sp.add_argument("-a", "--author", required=True)
    sp.add_argument("--comment")
    sp.set_defaults(fn=cmd_endorse)

    sp = sub.add_parser("dispute", help="challenge an entry (blocks promotion)")
    sp.add_argument("entry")
    sp.add_argument("-a", "--author", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(fn=cmd_dispute)

    sp = sub.add_parser("resolve", help="close a dispute or question")
    sp.add_argument("entry")
    sp.add_argument("-a", "--author", required=True)
    sp.add_argument("--outcome", required=True,
                    choices=["upheld", "withdrawn", "answered"])
    sp.add_argument("--reason", required=True)
    sp.set_defaults(fn=cmd_resolve)

    sp = sub.add_parser("verify", help="link a hypothesis to a ground-truth observation")
    sp.add_argument("entry", help="hypothesis id")
    sp.add_argument("observation", help="observation id")
    sp.add_argument("-a", "--author", required=True)
    sp.add_argument("--comment")
    sp.set_defaults(fn=cmd_verify)

    sp = sub.add_parser("show", help="compiled markdown view")
    sp.add_argument("--observations", type=int, default=5)
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("resume-context", help="compact injection for a fresh instance")
    sp.add_argument("--observations", type=int, default=8)
    sp.set_defaults(fn=cmd_resume_context)

    sp = sub.add_parser("lint", help="drift detection (exit 1 on findings)")
    sp.add_argument("--launder-threshold", type=int, default=3)
    sp.add_argument("--stale-threshold", type=int, default=10)
    sp.set_defaults(fn=cmd_lint)

    sp = sub.add_parser("log", help="raw entry listing")
    sp.add_argument("-n", type=int, default=30)
    sp.set_defaults(fn=cmd_log)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
