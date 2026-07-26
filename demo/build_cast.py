#!/usr/bin/env python3
"""Build a paced asciinema cast: real agent handoff via casefile.

Story (agents run casefile — the user never types it):

  1. Open Codex on checkout-demo; user asks about free shipping.
  2. Codex reproduces, files hyp+obs, verifies, packets → grok.
  3. Pause. Close Codex / open Grok (empty chat, same repo).
  4. User: "where are we on the free shipping bug?"
  5. Grok casefile boots and already knows the verified root cause.

Timings: quick user prompts, brief agent tools, a long beat on the model
switch, and a long hold on the final frame so the GIF loop can be read.
Scene text is from a real `codex exec` + `grok -p` run on demo/fixture/
(see demo/scenes.json).

  bash demo/render.sh
  # or manually:
  python3 demo/build_cast.py
  agg --cols 100 --rows 30 --font-size 14 --idle-time-limit 30 \\
    demo/casefile-continuity.cast demo/casefile-continuity.gif
  python3 demo/build_cast.py --pad-gif   # GIF hack: force last-frame delay
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
WIDTH, HEIGHT = 100, 30
# Content target (before the final loop-hold). Keep readable, not draggy.
CONTENT_S = 48.0
# Desired park time on the punchline before the GIF loops.
# agg clamps long idle gaps (~3s in practice), so the cast hold alone is not
# enough — pad_gif_last_frame() rewrites the last frame's Graphic Control
# Extension delay after render.
END_HOLD_S = 15.0
# User prompt typing speed (chars/sec). High on purpose: content is later
# scaled to CONTENT_S, which would otherwise stretch a "natural" type rate.
USER_CPS = 90.0

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
    "grok_think": "no chat history — reading the casefile…",
    "grok_tools": [
        ("$ casefile boot", [
            "(read log · 1 verified claim · packet from codex)",
        ]),
    ],
    "grok_reply": [
        "Synopsis — already on the log, no need to re-debug:",
        "",
        "  Free shipping is wrong because we compare the tax-inclusive total",
        "  to the $500 threshold. A $480 cart + tax looks like $518, so it",
        "  ships free even though marketing means $500 of merchandise.",
        "",
        "  Codex verified that (hyp 5fbd68af). Investigation only — don't fix yet.",
        "  I didn't re-run the tests; the casefile already has the answer.",
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

    def type_text(self, text: str, cps: float = USER_CPS) -> None:
        """Type text at ~cps characters/sec (user prompts: snappy)."""
        delay = 1.0 / max(cps, 1.0)
        for ch in text:
            if ch == "\n":
                self.out("\r\n", after=delay * 1.5)
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
        self.line(f"{BOLD}{BLU}▸ you{RST}", after=0.1)
        self.pause(0.25)  # short beat, then type quickly
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            self.out("  ")
            self.type_text(ln, cps=USER_CPS)
            self.out("\r\n", after=0.06 if i < len(lines) - 1 else 0.15)
        self.pause(0.4)  # hold after user message before agent moves

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
            if ln == "":
                self.line("", after=0.06)
            else:
                self.line(f"  {ln}", after=0.1)
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

    # ── SWITCH (make the handoff unmissable) ───────────────
    c.pause(0.6)
    c.clear()
    c.line()
    c.line(f"{BOLD}{YEL}{'═' * 72}{RST}", after=0.08)
    c.line(f"{BOLD}{YEL}  CONTEXT RESET{RST}", after=0.12)
    c.line(f"{YEL}  close Codex · wipe the chat · same repo on disk{RST}", after=0.1)
    c.line()
    c.line(f"{BOLD}{GRN}  >>>  NOW OPENING GROK  <<<{RST}", after=0.15)
    c.line(f"{DIM}{GRN}  (new model · empty context · casefile is the only memory){RST}", after=0.1)
    c.line(f"{BOLD}{YEL}{'═' * 72}{RST}", after=0.1)
    c.pause(1.8)  # product moment: sit on the switch

    # ── GROK ───────────────────────────────────────────────
    c.agent_hdr("grok", GRN)
    c.pause(0.7)
    c.user_prompt(sc["user_grok"])
    c.think(sc["grok_think"], GRN)
    for cmd, out_lines in sc["grok_tools"]:
        c.tool(cmd, out_lines, GRN)
    c.agent_say(sc["grok_reply"], GRN, "grok")
    c.pause(0.9)

    # ── coda ───────────────────────────────────────────────
    c.line()
    c.line(
        f"{BOLD}{CYAN}  ✓ survived the reset{RST}{DIM}  "
        f"— grades + abstract + packet; no shared chat required{RST}",
        after=0.15,
    )
    c.pause(0.8)

    # Scale content only, then append a hard end-hold so the GIF loop
    # parks on the punchline (≥ END_HOLD_S). agg --idle-time-limit must
    # be > END_HOLD_S or the renderer will clamp this pause.
    if c.events:
        end = c.events[-1][0]
        if end > 0.05:
            scale = CONTENT_S / end
            for ev in c.events:
                ev[0] = round(ev[0] * scale, 4)
        last_t = c.events[-1][0]
        # zero-width space keeps the frame alive without changing the screen
        c.events.append([round(last_t + END_HOLD_S, 4), "o", "\x1b[0m"])

    header = {
        "version": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "timestamp": int(time.time()),
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
        "title": "casefile — codex files · grok resumes after context reset",
        "idle_time_limit": END_HOLD_S + 2,
    }
    cast_path = OUT_DIR / "casefile-continuity.cast"
    with cast_path.open("w") as f:
        f.write(json.dumps(header) + "\n")
        for ev in c.events:
            f.write(json.dumps(ev) + "\n")

    dur = c.events[-1][0] if c.events else 0
    content = dur - END_HOLD_S if c.events else 0
    print(f"wrote {cast_path}")
    print(
        f"duration≈{dur:.1f}s (content≈{content:.1f}s + hold={END_HOLD_S:.0f}s) "
        f"events={len(c.events)}"
    )


def pad_gif_last_frame(gif_path: Path, hold_s: float = END_HOLD_S) -> None:
    """Force the last GIF frame to display for hold_s seconds.

    asciinema/agg drops long idle gaps (often to ~3s even with a high
    --idle-time-limit), so the cast-level END_HOLD_S never makes it into
    the GIF. Patching the last Graphic Control Extension delay is the
    reliable loop-pause hack for GitHub README embeds.
    """
    data = bytearray(gif_path.read_bytes())
    # GCE: 21 F9 04 <packed> <delay_lo> <delay_hi> <trans> 00
    positions: list[int] = []
    i = 0
    while True:
        j = data.find(b"\x21\xf9\x04", i)
        if j < 0:
            break
        positions.append(j)
        i = j + 1
    if not positions:
        raise SystemExit(f"no GIF Graphic Control Extensions in {gif_path}")
    delay_cs = max(1, min(int(round(hold_s * 100)), 65535))
    j = positions[-1]
    old = data[j + 4] | (data[j + 5] << 8)
    data[j + 4] = delay_cs & 0xFF
    data[j + 5] = (delay_cs >> 8) & 0xFF
    gif_path.write_bytes(data)
    print(
        f"padded {gif_path.name}: last-frame delay "
        f"{old / 100:.2f}s → {delay_cs / 100:.2f}s"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pad-gif",
        action="store_true",
        help=f"only pad last frame of casefile-continuity.gif to {END_HOLD_S:.0f}s",
    )
    ap.add_argument(
        "--hold",
        type=float,
        default=END_HOLD_S,
        help=f"seconds to park on final GIF frame (default {END_HOLD_S})",
    )
    args = ap.parse_args()
    if args.pad_gif:
        pad_gif_last_frame(OUT_DIR / "casefile-continuity.gif", hold_s=args.hold)
        sys.exit(0)
    scenes_path = OUT_DIR / "scenes.json"
    if not scenes_path.exists():
        scenes_path.write_text(json.dumps(DEFAULT_SCENES, indent=2) + "\n")
        print(f"seeded {scenes_path}")
    build()
