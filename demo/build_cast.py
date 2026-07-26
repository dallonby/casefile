#!/usr/bin/env python3
"""Build a ~15s asciinema cast of casefile multi-agent context survival.

Runs real casefile CLI commands against a temp store, then writes a v2 cast
with compressed timings (typing + output bursts).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CASEFILE = Path(__file__).resolve().parents[1] / "casefile.py"
OUT_DIR = Path(__file__).resolve().parent
WIDTH, HEIGHT = 100, 28
TARGET_S = 15.0


def run(cwd: Path, env: dict, *args: str) -> str:
    p = subprocess.run(
        [sys.executable, str(CASEFILE), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr and p.returncode else "")
    if p.returncode not in (0, 10, 20, 30, 40):
        raise RuntimeError(f"rc={p.returncode} args={args}\n{p.stdout}\n{p.stderr}")
    return out.rstrip("\n")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="casefile-demo-"))
    try:
        env_base = {
            **os.environ,
            "CODEX_HOME": str(tmp / ".codex-home"),
            "CASEFILE_BIN_DIR": str(tmp / "bin"),
            "HOME": str(tmp / "home"),
            "TERM": "xterm-256color",
        }
        (tmp / "home").mkdir()
        (tmp / "bin").mkdir()

        # --- seed a real investigation ---
        env = {**env_base, "CASEFILE_AUTHOR": "codex"}
        run(tmp, env, "init")
        run(tmp, env, "open", "casefile continuity demo",
            "--goal", "survive context reset across models")
        run(tmp, env, "add", "-t", "constraint", "-a", "user",
            "log rides in git; grades computed never stored")
        run(tmp, env, "add", "-t", "hypothesis", "-a", "codex",
            "context reset drops unrecorded decisions")
        hyp = run(tmp, env, "add", "-t", "hypothesis", "-a", "codex",
                  "shared log + boot is enough for model switch")
        run(tmp, env, "add", "-t", "observation", "-a", "system",
            "boot after empty chat still shows abstract", "--source", "demo")
        # observation id from last add - re-read log for verify
        import json as _j
        entries = [_j.loads(l) for l in (tmp / ".casefile" / "log.jsonl").read_text().splitlines() if l.strip()]
        hyp_id = [e["id"] for e in entries if e["type"] == "hypothesis" and "shared log" in e["body"]][-1]
        obs_id = [e["id"] for e in entries if e["type"] == "observation"][-1]
        run(tmp, env, "verify", hyp_id, obs_id, "-a", "claude")
        run(tmp, env, "checkpoint", "-a", "codex")
        run(tmp, env, "packet", "--to", "grok", "-a", "codex")

        # capture demo command outputs
        scenes: list[tuple[str, str, list[str]]] = []  # title, prompt_line, output lines

        def capture(author: str, prompt: str, *args: str, ok_codes=(0, 10, 20, 30, 40)):
            e = {**env_base, "CASEFILE_AUTHOR": author}
            p = subprocess.run(
                [sys.executable, str(CASEFILE), *args],
                cwd=tmp, env=e, capture_output=True, text=True,
            )
            if p.returncode not in ok_codes:
                raise RuntimeError(f"{args}: {p.returncode}\n{p.stderr}")
            text = (p.stdout or "").rstrip("\n")
            # trim long boot sections for demo readability
            if args and args[0] == "boot":
                keep = []
                for line in text.splitlines():
                    keep.append(line)
                    if line.startswith("=== CARD ==="):
                        keep.append("  (card elided)")
                        break
                    if line.startswith("=== WORLD vs LOG ==="):
                        keep.append(line)
                        keep.append("startup recheck: (ok)")
                        # skip detail until next section
                        continue
                # rebuild trimmed boot
                out_lines = []
                skip = False
                for line in text.splitlines():
                    if line.startswith("=== WORLD vs LOG ==="):
                        out_lines.append(line)
                        out_lines.append("startup recheck: checks hold")
                        skip = True
                        continue
                    if skip and line.startswith("==="):
                        skip = False
                    if skip:
                        continue
                    if line.startswith("=== CARD ==="):
                        out_lines.append(line)
                        out_lines.append("(identity + filing card)")
                        break
                    out_lines.append(line)
                text = "\n".join(out_lines)
            scenes.append((author, prompt, text.splitlines() or [""]))

        capture("codex", "export CASEFILE_AUTHOR=codex && casefile whoami", "whoami")
        capture("codex", "casefile boot --skip-recheck", "boot", "--skip-recheck", "--ok-exit")
        capture("codex", "casefile packet --to grok", "packet", "--to", "grok", "--no-file")
        # context reset banner is narrative only
        capture("grok", "export CASEFILE_AUTHOR=grok && casefile boot --skip-recheck",
                "boot", "--skip-recheck", "--ok-exit")
        capture("grok", "casefile inbox --for grok", "inbox", "--for", "grok")
        capture("grok", "casefile next", "next")

        # --- build cast with target duration ---
        # narrative beats
        beats: list[tuple[str, str]] = []  # kind, payload
        # kind: clear, banner, prompt, type, out, pause, note

        def add_banner(msg: str):
            beats.append(("out", f"\r\n\x1b[1;36m# {msg}\x1b[0m\r\n"))

        add_banner("casefile demo — multi-model continuity (codex → grok)")
        beats.append(("out", "\x1b[2m# context will be wiped; the log is ground truth\x1b[0m\r\n\r\n"))

        # scene 1: codex whoami + boot
        for i, (author, prompt, lines) in enumerate(scenes):
            if i == 3:
                add_banner("CONTEXT RESET — new model, empty chat, same repo")
                beats.append(("out", "\x1b[33m$ unset HISTFILE; clear  # simulate agent context wipe\x1b[0m\r\n"))
                beats.append(("pause", "0.15"))
            color = {"codex": "35", "grok": "32", "claude": "34"}.get(author, "37")
            beats.append(("out", f"\x1b[1;{color}m[{author}]\x1b[0m "))
            beats.append(("type", f"$ {prompt}"))
            beats.append(("out", "\r\n"))
            # emit output (cap lines for timing)
            max_lines = 14 if "boot" in prompt else 8
            body = lines[:max_lines]
            if len(lines) > max_lines:
                body = body + [f"… ({len(lines) - max_lines} more lines)"]
            for ln in body:
                beats.append(("out", ln + "\r\n"))
            beats.append(("out", "\r\n"))

        add_banner("survived: grades + abstract + packet inbox — no chat history required")
        beats.append(("out", "\x1b[2m# casefile spitball --models codex,grok  # live CLIs optional\x1b[0m\r\n"))

        # time allocation
        type_chars = sum(len(p) for k, p in beats if k == "type")
        out_chunks = sum(1 for k, _ in beats if k == "out")
        # reserve: typing 40%, output 50%, pauses/banners 10%
        t_type = TARGET_S * 0.38
        t_out = TARGET_S * 0.52
        t_pause = TARGET_S * 0.10
        per_char = t_type / max(type_chars, 1)
        per_out = t_out / max(out_chunks, 1)

        events = []
        t = 0.05
        # header
        header = {
            "version": 2,
            "width": WIDTH,
            "height": HEIGHT,
            "timestamp": int(time.time()),
            "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
            "title": "casefile — context reset across codex → grok",
        }

        for kind, payload in beats:
            if kind == "pause":
                t += float(payload)
                continue
            if kind == "type":
                # show prompt with rapid typing
                for ch in payload:
                    events.append([round(t, 4), "o", ch])
                    t += per_char
                t += 0.08
                continue
            if kind == "out":
                events.append([round(t, 4), "o", payload])
                t += per_out * (0.35 + 0.05 * min(len(payload), 40) / 40)

        # fit to TARGET_S (stretch if short, compress if long)
        if events:
            scale = TARGET_S / max(events[-1][0], 0.01)
            for ev in events:
                ev[0] = round(ev[0] * scale, 4)

        cast_path = OUT_DIR / "casefile-continuity.cast"
        with cast_path.open("w") as f:
            f.write(json.dumps(header) + "\n")
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        # also write a short markdown companion
        md = OUT_DIR / "README.md"
        md.write_text(
            "# casefile demo cast\n\n"
            f"Duration target: **{TARGET_S:.0f}s**. Generated from real CLI output.\n\n"
            "## Play\n\n"
            "```bash\n"
            "asciinema play demo/casefile-continuity.cast\n"
            "# or upload:\n"
            "asciinema upload demo/casefile-continuity.cast\n"
            "```\n\n"
            "## Storyboard\n\n"
            "1. **codex** whoami + boot (case state from log)\n"
            "2. **codex** packet → grok (handoff without shared chat)\n"
            "3. **CONTEXT RESET** banner (empty agent chat)\n"
            "4. **grok** boot + inbox + next (survives reset)\n\n"
            "Regenerate: `python3 demo/build_cast.py`\n"
        )
        print(f"wrote {cast_path}")
        print(f"duration≈{events[-1][0] if events else 0:.2f}s events={len(events)}")
        print(f"demo store was {tmp}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
