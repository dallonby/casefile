<!-- >>> casefile (managed by `casefile hooks install codex`) >>> -->
## casefile

This project keeps its investigation state in an append-only casefile log.

- At session start run `python3 casefile.py boot` (sets identity, startup
  recheck, structured brief). Export `CASEFILE_AUTHOR` first.
- Handoff via the log: `packet --to <peer>`, `inbox --for <you>`, `next`.
- Checkpoint abstracts: `checkpoint` then `recall` for compost search.
- File hypotheses, decisions, observations, and questions as you work —
  the conventions in `.claude/skills/casefile/SKILL.md` apply to any agent,
  not just Claude. Never edit `.casefile/log.jsonl` by hand.
<!-- <<< casefile <<< -->
