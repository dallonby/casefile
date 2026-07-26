<!-- >>> casefile (managed by `casefile hooks install codex`) >>> -->
## casefile

This project keeps its investigation state in an append-only casefile log.

- **REQUIRED every session:** `export CASEFILE_AUTHOR=<your-id>` then
  `python3 casefile.py boot` (or `casefile boot`). Pick a durable id for
  *this* agent (e.g. `claude`, `codex`, `grok45`, `fable`→claude). If
  `whoami` shows author `agent` / `from default`, stop and export first
  (boot exit 40). Never file as anonymous `agent`.
- Handoff via the log: `packet --to <peer>`, `inbox --for <you>`, `next`.
- Checkpoint abstracts: `checkpoint` then `recall` for compost search.
- File hypotheses, decisions, observations, and questions as you work —
  the conventions in `.claude/skills/casefile/SKILL.md` apply to any agent.
  Never edit `.casefile/log.jsonl` by hand.
<!-- <<< casefile <<< -->
