#!/usr/bin/env python3
"""Stop hook: secretary sweep + liveness pulse (SPEC §13; decision 52694aa9).

A stop blocks for a secretary sweep only when the log tail shows something
worth sweeping since the last sweep marker (see the SWEEP_* policy below);
otherwise it ends the turn silently, so a quiet turn does not have to file
a "nothing unrecorded" note. The re-fire (`stop_hook_active` / Grok
`stopHookActive`) is the final pass: every write of the turn — model-filed
and sweep-filed — is already in the log, so it emits at most ONE honest
liveness pulse (synthesis H7): 'casefile +3 since last look (2 hypothesis,
1 observation) — 74 total'. The diff is 'since this session last looked'
via a session-keyed atomic cursor — no per-session write provenance is
claimed. Suppressed while the tmux UI holds a fresh heartbeat lease (it is
the liveness surface then); the cursor still advances. Silent when idle.

Reads only the local log (the postgres mirror is kept in step by every
append), and only its tail — never the whole store.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LEASE_FRESH_S = 10

# Sweep policy — operators may edit. The hook prompts for a sweep when, since
# the last marker (a note whose body starts with SWEEP_MARKER_PREFIX), at
# least one holds:
#   (a) a non-system entry of a type in SWEEP_TYPES was filed, or
#   (b) at least SWEEP_OBS_THRESHOLD non-system observations were filed, or
#   (c) the marker is older than SWEEP_STALE_MIN minutes and any non-system
#       entry was filed.
# No marker within the tail (never swept, or SWEEP_TAIL_LINES entries since
# the last one) always prompts. Entries by SWEEP_NOISE_AUTHORS or with a
# `hook:*` source are automatic and never trigger a sweep on their own.
SWEEP_TYPES = {"decision", "constraint", "hypothesis", "question", "verification"}
SWEEP_OBS_THRESHOLD = 20
SWEEP_STALE_MIN = 30
SWEEP_TAIL_LINES = 2000
SWEEP_NOISE_AUTHORS = {"system"}
SWEEP_MARKER_PREFIX = "secretary sweep"  # same test as lint's UNSWEPT rule

ROOT = Path(__file__).resolve().parents[2]  # <repo>/.casefile/hooks/sweep.py

# Installing vendor may pass author as argv[1] (codex); otherwise prefer an
# explicit CASEFILE_AUTHOR.  A plain Grok launch does not set that variable,
# but its hook runner always injects both reserved GROK_* values, so use those
# as the runtime discriminator before the bare claude-code default.
AUTHOR = (
    (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    or (os.environ.get("CASEFILE_AUTHOR") or "").strip()
    or ("grok" if os.environ.get("GROK_HOOK_EVENT")
        and os.environ.get("GROK_SESSION_ID") else "")
    or "claude"
)


def _field(hook, *names, default=None):
    """Claude/Codex: snake_case. Grok: camelCase. Accept both."""
    for name in names:
        if name in hook and hook[name] is not None:
            return hook[name]
    return default


def _cli_display(root):
    """The invocation to tell the model: repo-root copy, then the
    .casefile/cli pointer the installer records, then PATH."""
    if (root / "casefile.py").exists():
        return "python3 casefile.py"
    try:
        p = Path((root / ".casefile" / "cli").read_text().strip())
        if p.is_file():
            return f"python3 {p}"
    except OSError:
        pass
    return "casefile"


CLI = _cli_display(ROOT)


def _base_reason(cli: str = CLI, author: str = AUTHOR) -> str:
    return (
        "Secretary sweep (casefile): before ending, diff this conversation against "
        "the casefile log. Anything decided, constrained, observed, or ruled out "
        f"here that isn't recorded? File it with `{cli} add ...` using "
        f"the correct type and author (user for the user's words, {author} for your "
        "own; use self-contained bodies and `--scope turn` for one-turn directives). "
        "Close any open questions, decisions, or hypotheses settled in this turn "
        "inline (`--answer`, `--fulfill`, `--verify`, `--supersede`). Then file "
        "the sweep marker — "
        f"`{cli} add -t note -a {author} \"secretary sweep: <gaps filed, "
        "or 'nothing unrecorded'>\"` — and finish."
    )


REASON = _base_reason(CLI, AUTHOR)


LOG = ROOT / ".casefile" / "log.jsonl"


def _parse(lines) -> list[dict]:
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def raw_lines(path: Path) -> list[str]:
    """Every non-empty log line, unparsed (the pulse only decodes its delta)."""
    if not path.exists():
        return []
    return [l for l in path.read_text().splitlines() if l.strip()]


def tail_lines(path: Path, n: int) -> list[str]:
    """The last n lines, read backwards in blocks — not the whole store."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    chunks = []
    newlines = 0
    with path.open("rb") as f:
        pos = size
        while pos > 0 and newlines <= n:
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step)
            chunks.append(data)
            newlines += data.count(b"\n")
    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def _is_marker(e: dict) -> bool:
    return (e.get("type") == "note"
            and str(e.get("body") or "").lower().startswith(SWEEP_MARKER_PREFIX))


def _is_noise(e: dict) -> bool:
    return (e.get("author") in SWEEP_NOISE_AUTHORS
            or str(e.get("source") or "").startswith("hook:"))


def _is_substantive(e: dict) -> bool:
    if _is_noise(e) or _is_marker(e):
        return False
    b = e.get("board")
    if e.get("type") == "note" and isinstance(b, dict) \
            and b.get("op") in ("read", "ack", "follow", "unfollow"):
        return False
    return True


def _head(body: str, width: int = 72) -> str:
    first = ""
    for ln in (body or "").splitlines():
        if ln.strip():
            first = " ".join(ln.split())
            break
    return (first[:width - 1].rstrip() + "…") if len(first) > width else first


def open_state_checklist(entries: list[dict], case: str | None,
                         cli: str = CLI, author: str = AUTHOR) -> str:
    """Bounded open-state delta from the parsed log tail (zero subprocesses).

    Surfaces live questions, disputes, unfulfilled decisions, unverified
    hypotheses, and failing rechecks in `case` with exact closure flags so the
    agent closes transitions while turn context is still hot.
    """
    if not case or not entries:
        return ""
    by_id: dict[str, dict] = {}
    superseded: set[str] = set()
    revoked: set[str] = set()
    resolved: set[str] = set()
    fulfilled: set[str] = set()
    dispute_outcomes: dict[str, str] = {}
    verified: set[str] = set()
    expired_turn: set[str] = set()
    open_turn: list[tuple[str, datetime | None]] = []
    last_recheck: dict[str, bool] = {}
    gap_s = 30 * 60

    for e in entries:
        eid = e.get("id")
        if not eid:
            continue
        by_id[eid] = e
        t = e.get("type")
        refs = e.get("refs") or []
        if (t == "digest" and e.get("kind") != "candidate") \
                or t in ("hypothesis", "constraint", "decision"):
            superseded.update(e.get("supersedes") or [])
        if t == "revocation":
            revoked.update(refs)
        elif t == "resolution":
            resolved.update(refs)
            outcome = str(e.get("outcome") or "")
            for r in refs:
                dispute_outcomes[r] = outcome
            if outcome == "fulfilled":
                fulfilled.update(refs)
        elif t == "verification":
            if any(by_id.get(r, {}).get("type") == "observation" or r not in by_id
                   for r in refs):
                verified.update(refs)
        elif t == "observation":
            src = str(e.get("source") or "")
            if src.startswith("recheck:"):
                tid = src.split(":", 1)[1]
                b = str(e.get("body") or "")
                if b.startswith("[FAIL]"):
                    last_recheck[tid] = False
                elif b.startswith("[PASS]"):
                    last_recheck[tid] = True

        if e.get("case") != case:
            continue
        if open_turn:
            if _is_marker(e) or (t == "digest"
                                 and e.get("kind") in ("abstract", "judgment")):
                expired_turn.update(tid for tid, _ in open_turn)
                open_turn.clear()
            elif _is_substantive(e):
                ts = _parse_ts(e.get("ts"))
                if ts is not None:
                    keep = []
                    for tid, tts in open_turn:
                        if tts is not None and (ts - tts).total_seconds() > gap_s:
                            expired_turn.add(tid)
                        else:
                            keep.append((tid, tts))
                    open_turn = keep
        if t in ("decision", "constraint") and e.get("scope") == "turn":
            open_turn.append((eid, _parse_ts(e.get("ts"))))

    refuted: set[str] = set()
    for e in entries:
        if e.get("type") == "dispute" \
                and dispute_outcomes.get(e.get("id", "")) == "upheld":
            refuted.update(e.get("refs") or [])

    case_entries = [e for e in entries
                    if e.get("case") == case and e.get("id") not in superseded]
    qs = [e for e in case_entries
          if e.get("type") == "question" and e["id"] not in resolved]
    ds = [e for e in case_entries
          if e.get("type") == "dispute" and e["id"] not in resolved]
    decs = [e for e in case_entries
            if e.get("type") == "decision"
            and e["id"] not in revoked
            and e["id"] not in fulfilled
            and e["id"] not in expired_turn]
    hyps = [e for e in case_entries
            if e.get("type") == "hypothesis"
            and e["id"] not in refuted
            and e["id"] not in verified]
    failing = [
        e for e in case_entries
        if e.get("type") in ("hypothesis", "constraint")
        and e.get("check")
        and last_recheck.get(e["id"]) is False
        and (e.get("type") != "constraint"
             or (e["id"] not in revoked and e["id"] not in expired_turn))
        and (e.get("type") != "hypothesis" or e["id"] not in refuted)
    ]

    bullets: list[str] = []
    for q in qs[::-1][:3]:
        bullets.append(
            f"  - open question `{q['id']}`: \"{_head(q.get('body', ''))}\" "
            f"→ `{cli} add -t decision|observation|note -a {author} --answer {q['id']} \"...\"`"
        )
    for d in ds[::-1][:2]:
        bullets.append(
            f"  - open dispute `{d['id']}`: \"{_head(d.get('body', ''))}\" "
            f"→ `{cli} resolve {d['id']} -a {author} --outcome upheld|withdrawn --reason \"...\"`"
        )
    for dec in decs[::-1][:3]:
        bullets.append(
            f"  - unfulfilled decision `{dec['id']}`: \"{_head(dec.get('body', ''))}\" "
            f"→ shipped: `{cli} add -t observation -a {author} --fulfill {dec['id']} \"...\"` "
            f"| revised: `--supersede {dec['id']} --rationale \"...\"`"
        )
    for h in hyps[::-1][:3]:
        bullets.append(
            f"  - unverified hypothesis `{h['id']}`: \"{_head(h.get('body', ''))}\" "
            f"→ tested: `{cli} add -t observation -a {author} --verify {h['id']} \"...\"` "
            f"| revised: `--supersede {h['id']}`"
        )
    for fc in failing[::-1][:2]:
        bullets.append(
            f"  - failing check `{fc['id']}`: \"{_head(fc.get('body', ''))}\" "
            f"→ fix world, `--supersede {fc['id']}`, or `dispute {fc['id']} -a {author} --reason \"...\"`"
        )
    if not bullets:
        return ""
    return (
        "Open items in active case (close any settled in this turn before sweeping):\n"
        + "\n".join(bullets)
    )


def build_sweep_reason(entries: list[dict], case: str | None,
                       cli: str = CLI, author: str = AUTHOR) -> str:
    base = _base_reason(cli, author)
    checklist = open_state_checklist(entries, case, cli, author)
    return f"{base}\n\n{checklist}" if checklist else base


def _parse_ts(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _stamp(root: Path):
    """The last quiet sweep ('nothing unrecorded'): {ts, after_id, …}. The
    CLI records that outcome as a state stamp instead of a note entry, so
    the log tail alone would keep re-prompting after a quiet sweep."""
    try:
        return json.loads((root / ".casefile" / "state" / "sweep-stamp.json").read_text())
    except Exception:
        return None


def sweep_due(entries: list[dict], now: datetime | None = None,
              stamp: dict | None = None) -> bool:
    """Apply the SWEEP_* policy to the log tail (oldest first). The last
    sweep is the newer of the last marker note and the quiet-sweep stamp
    (positioned by the id it was filed after, else by time)."""
    last = None
    for i, e in enumerate(entries):
        if _is_marker(e):
            last = i
    marker_ts = _parse_ts(entries[last].get("ts")) if last is not None else None
    stamp_ts = _parse_ts(stamp.get("ts")) if stamp else None
    if stamp_ts is not None and (marker_ts is None or stamp_ts >= marker_ts):
        pos = -1 if stamp.get("after_id") == "" else None
        for i, e in enumerate(entries):
            if e.get("id") == stamp.get("after_id"):
                pos = i
        if pos is not None:
            since = [e for e in entries[pos + 1:] if not _is_noise(e)]
        else:
            since = [e for e in entries if not _is_noise(e)
                     and (_parse_ts(e.get("ts")) or stamp_ts) > stamp_ts]
        marker_ts = stamp_ts
    elif last is None:
        return True
    else:
        since = [e for e in entries[last + 1:] if not _is_noise(e)]
    if not since:
        return False
    if any(e.get("type") in SWEEP_TYPES for e in since):
        return True
    if sum(1 for e in since if e.get("type") == "observation") >= SWEEP_OBS_THRESHOLD:
        return True
    if marker_ts is not None:
        now = now or datetime.now(timezone.utc)
        if (now - marker_ts).total_seconds() > SWEEP_STALE_MIN * 60:
            return True
    return False


def pulse(root: Path, session_id: str):
    lines = raw_lines(root / ".casefile" / "log.jsonl")
    total = len(lines)
    cur_dir = root / ".casefile" / "state"
    cur_dir.mkdir(parents=True, exist_ok=True)
    cursor = cur_dir / f"pulse-{session_id or 'default'}"
    try:
        seen = int(cursor.read_text())
    except Exception:
        seen = total  # first look: establish the baseline, report nothing
    delta = _parse(lines[seen:total] if 0 <= seen <= total else lines)
    # advance the cursor first (atomic) — suppressed pulses still count as seen
    tmp = cursor.with_suffix(".tmp")
    tmp.write_text(str(total))
    os.replace(tmp, cursor)
    if not delta:
        return
    hb = root / ".casefile" / "ui" / "heartbeat"
    try:
        if time.time() - hb.stat().st_mtime < LEASE_FRESH_S:
            return  # tmux UI lease fresh: it is the liveness surface (H6)
    except FileNotFoundError:
        pass
    by_type = {}
    for e in delta:
        by_type[e.get("type", "?")] = by_type.get(e.get("type", "?"), 0) + 1
    kinds = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items()))
    print(json.dumps({"systemMessage":
                      f"casefile +{len(delta)} since last look ({kinds}) "
                      f"— {total} total"}))


def main():
    hook = json.load(sys.stdin)
    # Claude/Codex: stop_hook_active + session_id
    # Grok: stopHookActive + sessionId  (camelCase envelope — see Grok hooks docs)
    if _field(hook, "stop_hook_active", "stopHookActive"):
        sid = str(_field(hook, "session_id", "sessionId", default="") or "")
        pulse(ROOT, sid)  # final pass: pulse (H7)
        return
    case = _active_case(ROOT)
    if not case:
        return  # no active case means nothing to sweep
    tail = _parse(tail_lines(LOG, SWEEP_TAIL_LINES))
    if not sweep_due(tail, stamp=_stamp(ROOT)):
        return  # nothing worth a sweep since the last sweep: end quietly
    print(json.dumps({"decision": "block",
                      "reason": build_sweep_reason(tail, case, CLI, AUTHOR)}))


def _active_case(root: Path) -> str | None:
    """Mirror casefile.load_active: the pointer lives in the untracked
    .casefile/active file, with a legacy fallback to meta.json."""
    ap = root / ".casefile" / "active"
    if ap.exists():
        return ap.read_text().strip() or None
    try:
        return json.loads((root / ".casefile" / "meta.json").read_text()).get("active_case")
    except Exception:
        return None


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
