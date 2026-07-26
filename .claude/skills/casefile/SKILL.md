---
name: casefile
description: Operate the casefile investigation log in this repo — resume context at session start, file hypotheses/decisions/observations with correct types and authors as you work, and translate the user's conversational directions ("where are we", "rule that out", "don't touch X", "have we seen this before") into casefile CLI calls.
---

# casefile — porcelain behavior (SPEC §11.2, §13)

The CLI is `python3 casefile.py <cmd>` from the repo root (or `casefile` if
installed). The log (`.casefile/log.jsonl`) is append-only ground truth —
**never edit it by hand**; corrections are new entries.

## Keep current (other machines / launch)

```bash
# in the project that owns .casefile/  (or set CASEFILE_ROOT)
casefile upgrade
# = git pull this CLI + symlink onto PATH + hooks install (SKILL.md, AGENTS, hooks)
```

Put `casefile upgrade` (or `python3 /path/to/casefile.py upgrade`) in agent
launch scripts so SKILL.md never drifts from the CLI.

## Identity (do this first — every session, every agent)

**You MUST export your own identity before filing anything:**

```bash
export CASEFILE_AUTHOR=claude    # Anthropic models (fable/sonnet/opus alias here)
# export CASEFILE_AUTHOR=codex   # OpenAI / Codex
# export CASEFILE_AUTHOR=grok45  # xAI / Grok
```

- Run `casefile whoami` — if it says `from default` / author `agent`, **stop and export**.
- Boot exit code **40** means identity unset. Grades, endorse/dispute, and packets
  depend on a real author; anonymous `agent` is not multi-agent safe.
- Always pass the same identity via `-a $CASEFILE_AUTHOR` on writes if env cannot stick.

## Session start

1. `export CASEFILE_AUTHOR=…` (see Identity above). Prefer `casefile upgrade` at launch.
2. **Prefer one command:** `casefile boot`
   (discovers the store, stamps author, runs `recheck --startup`, prints
   WHERE / YOU ARE / WORLD vs LOG / BRIEF / DO NOT / NEXT / CARD).
   Exit codes: 0 ok, 10 mailbox, 20 drift, 30 abstract stale, **40 identity unset**.
   Act on NEXT; surface mailbox once, don't block.
3. Legacy equivalent: `resume-context` then `recheck --startup` then `status`.
   Ground truth beats the notes where they conflict.
4. Multi-agent handoff (no shared chat): `packet --to <peer>`, peer runs
   `inbox --for <self>` + `boot`. Checkpoint with `checkpoint` before long
   gaps so `recall` sees the distilled problem.

## Filing conventions (types and authors matter — grades are computed from them)

- **hypothesis** — falsifiable claim, author is whoever proposed it. Add
  `--check '<shell>'` when a one-liner can test it (exit 0 = still holds).
- **decision** — author `user` ONLY for choices the user actually made;
  your own proposals are author `claude` (they render as "asserted, not
  user-confirmed"). Always give `--rationale`; record losing alternatives
  with `--rejected "option:reason"` so they aren't re-proposed.
- **observation** — ground truth only: test output, command results, log
  lines, with `--source`. Never file your own inference as an observation.
- **verify** — links a hypothesis to a real observation. Model agreement is
  never verification; endorse instead (`consensus` is explicitly weaker).
- **dispute** when you disagree with a recorded claim; `resolve` with
  `--outcome upheld|withdrawn|answered` when settled.
- **question --to user** for things only the user can answer (the mailbox).
- **digest** at checkpoints (`--kind judgment`), and keep the rolling
  abstract current (`--kind abstract`; `--supersedes` is automatic for
  abstracts): problem, status with grade in words, leading theory,
  ruled-out list, key decisions, open items. Run `reindex` after.

## Recognizing casefile-directed speech

| user says | you do |
|---|---|
| "where are we on X?" | `boot` (or `resume-context`) → prose summary sized to the question |
| "don't touch X" | `add -t constraint -a user` |
| "I'm not convinced by X" | `dispute -a user` |
| "why did we rule out X?" | `dig "<query>"` (searches superseded history; expands digests) |
| "have we seen this before?" | `recall "<query>"` (searches past-case abstracts) |
| "hand off to codex" | `packet --to codex` |
| "what's waiting for me?" | `inbox --for $CASEFILE_AUTHOR` / `next` |
| "what's codex saying?" / "show me the deliberation" | `channel <model>` (ui viewport → that model's live transcript) |
| "show the case again" | `channel state` (ui viewport → live state view) |
| "rule that out" / "let's go with X" | `resolve` / `add -t decision -a user` — **confirm first** |

## Trust conventions

- **Echo-back**: every mutation of the *user's* words echoes in one line:
  `recorded: constraint "don't touch the sniffer" (user)`. This is how
  mistranscription gets caught.
- **Confirm** destructive-ish acts (resolve, digest, revoke) with one word
  before running them. Reads never confirm.
- Your own routine filing is silent by default; show it on request.
- **Reset-readiness drill** (user-adopted 2026-07-17): periodically — after
  a digest, before ending a long session, or when the abstract feels stale —
  simulate a context reset: read ONLY `resume-context` + `status` output and
  ask what a fresh instance would be missing or misled by. Fix the surface
  (abstract, mailbox, checks), not the instance. Note the drill result in
  the sweep marker.

## Importing existing notes (§11.3)

To bootstrap a case from a CLAUDE.md, notes file, or pasted scrollback:
extract typed entries into a JSONL draft — one
`{"type": …, "author": …, "body": …}` per line (decisions may carry
`rationale`/`rejected`; observations `source`; hypotheses/constraints
`check`; questions `to`) — show the user the draft for bulk confirmation,
then run `python3 casefile.py import <draft.jsonl>`. Validation is
all-or-nothing; each imported entry echoes.

## Proposing

- When a debugging/diagnosis conversation shows multi-window shape
  (reproduction attempts, competing theories, >1 hour of context) and no
  case is open, **propose** opening one; on "yes", open it and backfill via
  `import`. Before the first hypothesis, `recall` the problem statement —
  surface strong compost hits ("this resembles the March importer case…").
- When the differential stalls (two theories, no discriminating evidence,
  ~3 windows without progress), propose escalating to a spitball (once the
  driver exists — M4).
