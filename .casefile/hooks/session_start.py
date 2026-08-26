#!/usr/bin/env python3
"""SessionStart hook: one-line casefile liveness summary (decision 52694aa9).

Absolute totals only — a fresh session has no cursor, so no delta is
claimed. Also seeds this session's pulse cursor so the first Stop-pass
pulse diffs from session start, not from zero.
"""
import json
import os
import sys
from pathlib import Path


def _field(hook, *names, default=None):
    """Claude/Codex: snake_case. Grok: camelCase. Accept both."""
    for name in names:
        if name in hook and hook[name] is not None:
            return hook[name]
    return default


def main():
    hook = json.load(sys.stdin)
    root = Path(__file__).resolve().parents[2]
    cf = root / ".casefile"
    entries = [l for l in (cf / "log.jsonl").read_text().splitlines() if l.strip()] \
        if (cf / "log.jsonl").exists() else []
    active = None
    if (cf / "active").exists():
        active = (cf / "active").read_text().strip() or None
    if not active:
        try:
            active = json.loads((cf / "meta.json").read_text()).get("active_case")
        except Exception:
            return
    if not active:
        return
    open_q = 0
    resolved = set()
    parsed = []
    for line in entries:
        try:
            parsed.append(json.loads(line))
        except Exception:
            pass
    for e in parsed:
        if e.get("type") == "resolution":
            resolved.update(e.get("refs", []))
    open_q = sum(1 for e in parsed if e.get("type") == "question"
                 and e["id"] not in resolved)
    sid = str(_field(hook, "session_id", "sessionId", default="") or "") or "default"
    state = cf / "state"
    state.mkdir(parents=True, exist_ok=True)
    tmp = state / f"pulse-{sid}.tmp"
    tmp.write_text(str(len(parsed)))
    os.replace(tmp, state / f"pulse-{sid}")
    author = (os.environ.get("CASEFILE_AUTHOR") or "").strip()
    if author:
        id_line = f"CASEFILE_AUTHOR={author} (ok)"
    else:
        id_line = ("CASEFILE_AUTHOR unset — export CASEFILE_AUTHOR="
                   "claude|codex|grok|fable before filing "
                   "(run: casefile whoami && casefile boot)")
    print(json.dumps({"systemMessage":
                      f"casefile: {active} — {len(parsed)} entries, "
                      f"{open_q} open questions. {id_line}"}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
