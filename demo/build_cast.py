#!/usr/bin/env python3
"""Build a paced asciinema cast: real agent handoff via casefile.

Story (agents run casefile — the user never types it):

  1. Open Codex on checkout-demo; user asks about free shipping.
  2. Codex reproduces, files hyp+obs, verifies, packets → grok.
  3. Pause. Close Codex / open Grok (empty chat, same repo).
  4. User: "where are we on the free shipping bug?"
  5. Grok casefile boots and already knows the verified root cause.

Timings are cinematic (slow user prompts, brief agent tools, long pauses
on the model switch). Scene text is derived from a real `codex exec` +
`grok -p` run against demo/fixture/ (see demo/scenes.json).

  python3 demo/build_cast.py
  agg --cols 100 --rows 30 --font-size 14 \\
    demo/casefile-continuity.cast demo/casefile-continuity.gif
"""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
WIDTH, HEIGHT = 100, 30
# ~55–65s feels watchable on a README; slow prompts + long switch beat.
TARGET_S = 58.0

# ANSI
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RST = "\x1b[0m"
CYAN = "\x1b[36m"
MAG = "\x1b[35m"
GRN = "\x1b[32m"
YEL = "\x1b[33m"
BLU = "\x1b[34m"
WHT = "\x1b[37m"
BG_DIM = "\x1b[48;5;236m"


def load_scenes() -> dict:
    p = OUT_DIR / "scenes.json"
    if p.exists():
        return json.loads(p.read_text())
    # Fallback embedded if scenes.json missing (should be committed).
    return DEFAULT_SCENES


DEFAULT_SCENES = {
    "user_codex": (
        "Support keeps saying free shipping is wrong on checkout-demo.\n"
        "Find the root cause — don't fix yet. Use the casefile."
    ),
    "codex_think": "reproducing the failing shipping test…",
    "codex_tools": [
        ("$ python3 test_shipping.py", [
            "PASS test_under_threshold_pays_shipping",
            "PASS test_exactly_500_free_shipping_intended",
            "FAIL test_480_should_still_pay_shipping_per_marketing:",
            "  BUG: $480 merch got shipping=0.0  total=518.4",
        ]),
        ("$ casefile open \"free shipping threshold\" --goal \"find root cause…\"", [
            "opened free-shipping-threshold",
        ]),
        ("$ casefile add -t hypothesis -a codex --body-stdin <<'EOF'", [
            "… shipping_for compares tax-inclusive amount vs $500 merch threshold",
            "recorded: 5fbd68af",
        ]),
        ("$ casefile add -t observation -a codex --source test_shipping.py …", [
            "recorded: 11188444  [ground-truth]",
        ]),
        ("$ casefile verify 5fbd68af 11188444 -a codex", [
            "verified 5fbd68af ← 11188444",
        ]),
        ("$ casefile checkpoint -a codex && casefile packet --to grok -a codex", [
            "checkpoint abstract b6232838",
            "recorded: packet note ffd85dd7 → grok",
        ]),
    ],
    "codex_reply": [
        "Root cause: shipping_for compares tax-inclusive total, not merch subtotal.",
        "Evidence: $480 + $38.40 tax incorrectly yields $0 shipping; test fails.",
        "Filed hyp 5fbd68af (verified) · obs 11188444 · packet → grok.",
        "No code changed. Fresh model: casefile boot.",
    ],
    "user_grok": "where are we on the free shipping bug?",
    "grok_think": "empty chat — booting from the casefile…",
    "grok_tools": [
        ("$ export CASEFILE_AUTHOR=grok && casefile boot", [
            "=== WHERE ===",
            "active case: free-shipping-threshold",
            "goal: find root cause of free-shipping mismatch with marketing",
            "=== BRIEF ===",
            "STATUS: leading theory is shipping_for compares tax-inclusive",
            "  amount against the $500 threshold … (verified; id 5fbd68af)",
            "constraints:",
            "  Investigation only; do not fix the storefront yet.",
            "=== NEXT ===",
            "1. casefile packet --to codex -a grok",
        ]),
        ("$ casefile inbox --for grok", [
            "inbox for grok: 1 item",
            "  ffd85dd7 [note] from codex — PACKET for grok",
        ]),
    ],
    "grok_reply": [
        "Open case: free-shipping-threshold — free-shipping vs marketing.",
        "Root cause (verified): hyp 5fbd68af — shipping_for uses tax-inclusive",
        "  total vs $500, not merchandise subtotal.",
        "Constraint: investigation only — do not fix yet.",
        "Do not re-investigate: claim already verified. No need to re-run tests.",
        "Next: packet back to codex when ready to act on the fix.",
    ],
}


class Cast:
    def __init__(self) -> None:
        self.events: list[list] = []
        self.t = 0.12

    def pause(self, s: float) -> None:
        self.t += s

    def out(self, s: str, after: float = 0.0) -> None:
        self.events.append([round(self.t, 4), "o", s])
        self.t += after

    def line(self, s: str = "", after: float = 0.04) -> None:
        self.out(s + "\r\n", after=after)

    def type_text(self, text: str, cps: float = 18.0) -> None:
        """Type text at ~cps characters/sec (user prompts: slow)."""
        delay = 1.0 / max(cps, 1.0)
        for ch in text:
            if ch == "\n":
                self.out("\r\n", after=delay * 2.5)
            else:
                self.out(ch, after=delay)

    def clear(self) -> None:
        self.out("\x1b[2J\x1b[H", after=0.05)

    def banner(self, title: str, subtitle: str = "") -> None:
        self.line(f"{BOLD}{CYAN}{'─' * 72}{RST}", after=0.06)
        self.line(f"{BOLD}{CYAN}  {title}{RST}", after=0.08)
        if subtitle:
            self.line(f"{DIM}  {subtitle}{RST}", after=0.06)
        self.line(f"{BOLD}{CYAN}{'─' * 72}{RST}", after=0.1)

    def agent_hdr(self, name: str, color: str) -> None:
        self.line()
        self.line(f"{BOLD}{color}╭─ {name} ───────────────────────────────────────────────────╮{RST}", after=0.08)
        self.line(f"{BOLD}{color}│{RST}  {DIM}session · cwd: checkout-demo · casefile store present{RST}", after=0.05)
        self.line(f"{BOLD}{color}╰────────────────────────────────────────────────────────────╯{RST}", after=0.12)

    def user_prompt(self, text: str) -> None:
        self.line(f"{BOLD}{BLU}▸ you{RST}", after=0.15)
        self.pause(0.55)  # beat before the human types
        # indent each line of the prompt
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            prefix = "  "
            self.out(prefix)
            self.type_text(ln, cps=16.0)
            self.out("\r\n", after=0.12 if i < len(lines) - 1 else 0.35)
        self.pause(0.7)  # hold after user message before agent moves

    def think(self, text: str, color: str) -> None:
        # brief dim thought — intentionally short
        self.line(f"  {DIM}{color}… {text}{RST}", after=0.35)
        self.pause(0.25)

    def tool(self, cmd: str, lines: list[str], color: str) -> None:
        self.line(f"  {BOLD}{color}⚙{RST} {DIM}{cmd}{RST}", after=0.12)
        self.pause(0.18)
        for ln in lines:
            self.line(f"    {WHT}{ln}{RST}", after=0.055)
        self.pause(0.22)

    def agent_say(self, lines: list[str], color: str, name: str) -> None:
        self.line(f"{BOLD}{color}▸ {name}{RST}", after=0.18)
        self.pause(0.3)
        for ln in lines:
            self.line(f"  {ln}", after=0.09)
        self.pause(0.55)


def build() -> None:
    sc = load_scenes()
    c = Cast()

    # cold open
    c.clear()
    c.banner(
        "casefile — multi-model continuity",
        "agents file the log · context reset · next model already knows",
    )
    c.pause(1.1)

    # ── CODEX ──────────────────────────────────────────────
    c.agent_hdr("codex", MAG)
    c.pause(0.9)
    c.user_prompt(sc["user_codex"])
    c.think(sc["codex_think"], MAG)
    for cmd, out_lines in sc["codex_tools"]:
        c.tool(cmd, out_lines, MAG)
    c.agent_say(sc["codex_reply"], MAG, "codex")
    c.pause(1.0)

    # ── SWITCH ─────────────────────────────────────────────
    c.line()
    c.line(f"{BOLD}{YEL}  ✦ context reset{RST}", after=0.15)
    c.line(f"{DIM}    close codex · empty chat · same repo · log still on disk{RST}", after=0.12)
    c.pause(1.6)  # long beat on the switch — this is the product moment
    c.line(f"{DIM}    open grok…{RST}", after=0.2)
    c.pause(1.0)

    # ── GROK ───────────────────────────────────────────────
    c.agent_hdr("grok", GRN)
    c.pause(0.85)
    c.user_prompt(sc["user_grok"])
    c.think(sc["grok_think"], GRN)
    for cmd, out_lines in sc["grok_tools"]:
        c.tool(cmd, out_lines, GRN)
    c.agent_say(sc["grok_reply"], GRN, "grok")
    c.pause(0.8)

    # ── coda ───────────────────────────────────────────────
    c.line()
    c.line(
        f"{BOLD}{CYAN}  ✓ survived the reset{RST}{DIM}  "
        f"— grades + abstract + packet; no shared chat required{RST}",
        after=0.15,
    )
    c.pause(1.4)

    # scale to TARGET_S
    if c.events:
        end = c.events[-1][0]
        if end > 0.05:
            scale = TARGET_S / end
            for ev in c.events:
                ev[0] = round(ev[0] * scale, 4)

    header = {
        "version": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "timestamp": int(time.time()),
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
        "title": "casefile — codex files · grok resumes after context reset",
    }
    cast_path = OUT_DIR / "casefile-continuity.cast"
    with cast_path.open("w") as f:
        f.write(json.dumps(header) + "\n")
        for ev in c.events:
            f.write(json.dumps(ev) + "\n")

    dur = c.events[-1][0] if c.events else 0
    print(f"wrote {cast_path}")
    print(f"duration≈{dur:.1f}s events={len(c.events)} target={TARGET_S}s")


if __name__ == "__main__":
    # ensure scenes.json exists (seed from DEFAULT if missing)
    scenes_path = OUT_DIR / "scenes.json"
    if not scenes_path.exists():
        scenes_path.write_text(json.dumps(DEFAULT_SCENES, indent=2) + "\n")
        print(f"seeded {scenes_path}")
    build()
