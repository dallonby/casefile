---
name: casefile
description: Operate the casefile investigation log in this repo — resume context at session start, file hypotheses/decisions/observations with correct types and authors as you work, and translate the user's conversational directions ("where are we", "rule that out", "don't touch X", "have we seen this before") into casefile CLI calls.
---

# casefile — porcelain behavior (SPEC §11.2, §13)

The CLI is `python3 casefile.py <cmd>` from the repo root. The log
(`.casefile/log.jsonl`) is append-only ground truth — **never edit it by
hand**; corrections are new entries.

## Session start

1. Run `python3 casefile.py resume-context` and read it. Ground truth beats
   the notes: where the log and the world conflict, the world wins — record
   the discrepancy as a new observation.
2. Run `python3 casefile.py status`. Address any open questions before
   proceeding; questions marked `→ user` are waiting on the user — surface
   them once, don't block on them.
3. (When `recheck` exists — M2 — run it first: it tells you which claims
   still hold versus held-three-days-ago.)

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
  abstract current (`--kind abstract`): problem, status with grade in
  words, leading theory, ruled-out list, key decisions, open items.

## Recognizing casefile-directed speech

| user says | you do |
|---|---|
| "where are we on X?" | `resume-context` → prose summary sized to the question |
| "don't touch X" | `add -t constraint -a user` |
| "I'm not convinced by X" | `dispute -a user` |
| "why did we rule out X?" | search the log (`log`, `show`; `dig` when it exists) |
| "rule that out" / "let's go with X" | `resolve` / `add -t decision -a user` — **confirm first** |

## Trust conventions

- **Echo-back**: every mutation of the *user's* words echoes in one line:
  `recorded: constraint "don't touch the sniffer" (user)`. This is how
  mistranscription gets caught.
- **Confirm** destructive-ish acts (resolve, digest, revoke) with one word
  before running them. Reads never confirm.
- Your own routine filing is silent by default; show it on request.

## Proposing

- When a debugging/diagnosis conversation shows multi-window shape
  (reproduction attempts, competing theories, >1 hour of context) and no
  case is open, **propose** opening one; on "yes", backfill the opening
  entries from the conversation so far.
- When the differential stalls (two theories, no discriminating evidence,
  ~3 windows without progress), propose escalating to a spitball (once the
  driver exists — M4).
