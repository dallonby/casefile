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


def _field(hook, *names, default=None):
    """Claude/Codex use snake_case; Grok uses camelCase. Accept both."""
    for name in names:
        if name in hook and hook[name] is not None:
            return hook[name]
    return default


def main():
    hook = json.loads(sys.stdin.read())
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
    subprocess.run(cli + ["compact"],  # §6.1: compaction rides hook batches
                   cwd=root, capture_output=True, timeout=10)
    subprocess.run(cli + ["sync-journal"],  # §13: external journals ride too
                   cwd=root, capture_output=True, timeout=10)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
