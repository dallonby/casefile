#!/usr/bin/env python3
"""PostToolUse hook: file interesting Bash results as casefile observations.

SPEC §13 hook adapter. Best-effort by design (P9): any failure exits 0
silently — the hook must never block the session. Volume control: only test
runs, commits, and failing commands are recorded, and casefile's own
invocations are skipped. Obvious token/key patterns are redacted before
append (SPEC §15 — the log rides in git). After appending, mechanical
compaction (§6.1) runs opportunistically to keep steady-state noise down.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_BODY = 500

INTERESTING = re.compile(
    r"\b(pytest|unittest|npm test|yarn test|pnpm test|cargo test|go test"
    r"|make (test|check)|tox|git commit)\b")
FAILURE = re.compile(r"(?i)\b(traceback|error:|failed|fatal|exception)\b")

KV_SECRET = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)(\s*[=:]\s*)\S+")
SECRET_PATTERNS = [
    re.compile(r"\b(sk|pk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{16,}"),
]


def redact(s: str) -> str:
    s = KV_SECRET.sub(r"\1\2[REDACTED]", s)
    for rx in SECRET_PATTERNS:
        s = rx.sub("[REDACTED]", s)
    return s


def _cli(root):
    """Resolve the CLI: repo-root copy, then the .casefile/cli pointer the
    installer records, then a PATH-installed `casefile`."""
    local = root / "casefile.py"
    if local.exists():
        return [sys.executable, str(local)]
    try:
        p = Path((root / ".casefile" / "cli").read_text().strip())
        if p.is_file():
            return [sys.executable, str(p)]
    except OSError:
        pass
    return ["casefile"]


MAINTENANCE_INTERVAL_S = 600


def _maintenance_due(root, now=None, interval=MAINTENANCE_INTERVAL_S):
    # Compaction and journal sync ride hook batches (§6.1/§13) but each is a
    # whole-log pass; on a large store that is seconds per tool call. Run
    # them at most once per interval per store, tracked by a stamp file.
    import time
    stamp = Path(root) / ".casefile" / "state" / "hook-maintenance.stamp"
    now = time.time() if now is None else now
    try:
        if now - stamp.stat().st_mtime < interval:
            return False
    except OSError:
        pass
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now))
        import os
        os.utime(stamp, (now, now))
    except OSError:
        pass
    return True


def _field(hook, *names, default=None):
    """Claude/Codex use snake_case; Grok uses camelCase. Accept both."""
    for name in names:
        if name in hook and hook[name] is not None:
            return hook[name]
    return default


def _observe(hook):
    # Claude: tool_name=Bash; Grok: toolName=run_terminal_command (matcher
    # still fires on Bash via alias, but the payload carries the real name).
    tool = str(_field(hook, "tool_name", "toolName", default="") or "")
    if tool not in ("Bash", "bash", "run_terminal_command"):
        return
    tool_input = _field(hook, "tool_input", "toolInput", default={}) or {}
    if not isinstance(tool_input, dict):
        return
    cmd = tool_input.get("command", "")
    if not cmd or "casefile" in cmd:
        return  # never observe the tool observing itself
    # Claude: tool_response; Grok: toolResult
    resp = _field(hook, "tool_response", "toolResult", default={}) or {}
    if not isinstance(resp, dict):
        resp = {"stdout": str(resp)}
    stdout = str(resp.get("stdout", ""))
    stderr = str(resp.get("stderr", ""))
    failed = bool(FAILURE.search(stderr) or FAILURE.search(stdout[-2000:]))
    if not (INTERESTING.search(cmd) or failed):
        return
    out = (stdout + "\n" + stderr).strip()
    body = redact(f"$ {cmd.splitlines()[0][:120]}\n{out[-MAX_BODY:] if out else '(no output)'}")
    root = Path(__file__).resolve().parents[2]  # <repo>/.casefile/hooks/observe.py
    cli = _cli(root)
    subprocess.run(cli + ["add", "-t", "observation", "-a", "system",
                          "--source", "hook:post-bash", body],
                   cwd=root, capture_output=True, timeout=10)
    if _maintenance_due(root):
        subprocess.run(cli + ["compact"],  # §6.1: compaction rides hook batches
                       cwd=root, capture_output=True, timeout=10)
        subprocess.run(cli + ["sync-journal"],  # §13: external journals ride too
                       cwd=root, capture_output=True, timeout=10)


SWEEP_REMINDER_INTERVAL_S = 300


def _sweep_reminder(hook, now=None):
    """Codex reminders belong during work, never in a Stop continuation.

    Codex supplies turn_id; Claude/Grok retain their existing Stop adapter.
    Reuse the bounded sweep policy without calling its blocking main().
    Checking is limited to once per five minutes per session, including
    quiet checks. This reminder neither files entries nor blocks tools.
    """
    if "turn_id" not in hook:
        return
    import hashlib
    import importlib.util
    import os
    import time

    root = Path(__file__).resolve().parents[2]
    now = time.time() if now is None else now
    sid = str(hook.get("session_id") or "default")
    key = hashlib.sha256(sid.encode()).hexdigest()[:24]
    stamp = root / ".casefile" / "state" / f"post-tool-sweep-{key}.stamp"
    try:
        if 0 <= now - stamp.stat().st_mtime < SWEEP_REMINDER_INTERVAL_S:
            return
    except FileNotFoundError:
        pass
    spec = importlib.util.spec_from_file_location(
        "casefile_sweep_policy", root / ".casefile" / "hooks" / "sweep.py")
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    case = sweep._active_case(root)
    if not case:
        return
    tail = sweep._parse(sweep.tail_lines(sweep.LOG, sweep.SWEEP_TAIL_LINES))
    due = sweep.sweep_due(tail, stamp=sweep._stamp(root))
    stamp.parent.mkdir(parents=True, exist_ok=True)
    tmp = stamp.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(str(now))
    os.utime(tmp, (now, now))
    os.replace(tmp, stamp)
    if due:
        author = (os.environ.get("CASEFILE_AUTHOR") or "").strip() or "codex"
        msg = (
            "Casefile checkpoint reminder (nonblocking): file any "
            "unrecorded decisions, constraints and evidence with the "
            f"correct author using `{sweep.CLI} add ...`, then record "
            "a secretary sweep marker before handoff. "
            "Codex no longer runs a casefile Stop hook."
        )
        checklist = sweep.open_state_checklist(tail, case, sweep.CLI, author)
        return f"{msg}\n{checklist}" if checklist else msg


def _board_reader(hook, now=None):
    """Deliver bounded board previews at Bash checkpoints, without receipts.

    Quiet/error checks are throttled too. A slow/unavailable store is limited
    to two seconds and cannot block or force continuation of an agent turn.
    Per-session cursors leave unread activity available to the next session.
    """
    import hashlib
    import os
    import shlex
    import time

    root = Path(__file__).resolve().parents[2]
    author = os.environ.get("CASEFILE_AUTHOR") or (
        "codex" if "turn_id" in hook else
        "grok" if (os.environ.get("GROK_HOOK_EVENT") and os.environ.get("GROK_SESSION_ID"))
        or "toolName" in hook else "claude")
    sid = str(_field(hook, "session_id", "sessionId", default="default"))
    key = hashlib.sha256((author + ":" + sid).encode()).hexdigest()[:24]
    stamp = root / ".casefile" / "state" / f"post-tool-board-{key}.stamp"
    now = time.time() if now is None else now
    try:
        if 0 <= now - stamp.stat().st_mtime < 30:
            return
    except FileNotFoundError:
        pass
    stamp.parent.mkdir(parents=True, exist_ok=True)
    tmp = stamp.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(str(now))
    os.utime(tmp, (now, now))
    os.replace(tmp, stamp)
    cli = _cli(root)
    result = subprocess.run(
        cli + ["board", "watch", "--once", "--for", author,
               "--following", "--newest-first", "--reader", "hook:" + sid,
               "--limit", "3", "--cooldown", "30"],
        cwd=root, env={**os.environ, "CASEFILE_ROOT": str(root)},
        capture_output=True, text=True, timeout=2)
    if result.returncode or not result.stdout.strip():
        return
    return ("New casefile board activity (preview delivery, not acknowledgement):\n"
            + result.stdout.strip() + "\nRead full messages with `"
            + shlex.join(cli) + " board read MESSAGE --message --full -a "
            + shlex.quote(author) + "`, then reply or acknowledge explicitly.")


def main():
    hook = json.loads(sys.stdin.read())
    try:
        _observe(hook)
    finally:
        context = []
        for producer in (_sweep_reminder, _board_reader):
            try:
                message = producer(hook)
                if message:
                    context.append(message)
            except Exception:
                pass  # optional readers/reminders must not disrupt observation handling
        if context:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse", "additionalContext": "\n\n".join(context)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
