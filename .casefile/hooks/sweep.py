#!/usr/bin/env python3
"""Stop hook: secretary sweep + liveness pulse (SPEC §13; decision 52694aa9).

First stop of a session blocks so the model diffs its conversation against
the casefile log and files the gaps. The re-fire (`stop_hook_active` /
Grok `stopHookActive`) is the final pass: every write of the turn —
model-filed and sweep-filed — is already in the log, so it emits at most
ONE honest liveness pulse (synthesis H7): 'casefile +3 since last look
(2 hypothesis, 1 observation) — 74 total'. The diff is 'since this session
last looked' via a session-keyed atomic cursor — no per-session write
provenance is claimed. Suppressed while the tmux UI holds a fresh heartbeat
lease (it is the liveness surface then); the cursor still advances. Silent
when idle.
"""
import json
import os
import sys
import time
from pathlib import Path

LEASE_FRESH_S = 10

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

REASON = (
    "Secretary sweep (casefile): before ending, diff this conversation against "
    "the casefile log. Anything decided, constrained, observed, or ruled out "
    f"here that isn't recorded? File it with `{CLI} add ...` using "
    f"the correct type and author (user for the user's words, {AUTHOR} for your "
    "own). Then file the sweep marker — "
    f"`{CLI} add -t note -a {AUTHOR} \"secretary sweep: <gaps filed, "
    "or 'nothing unrecorded'>\"` — and finish."
)


def log_lines(root: Path) -> list[dict]:
    p = root / ".casefile" / "log.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def pulse(root: Path, session_id: str):
    entries = log_lines(root)
    total = len(entries)
    cur_dir = root / ".casefile" / "state"
    cur_dir.mkdir(parents=True, exist_ok=True)
    cursor = cur_dir / f"pulse-{session_id or 'default'}"
    try:
        seen = int(cursor.read_text())
    except Exception:
        seen = total  # first look: establish the baseline, report nothing
    delta = entries[seen:total] if 0 <= seen <= total else entries
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
    if not _active_case(ROOT):
        return  # no active case means nothing to sweep
    print(json.dumps({"decision": "block", "reason": REASON}))


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
