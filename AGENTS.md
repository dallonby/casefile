<!-- >>> casefile (managed by `casefile hooks install codex`) >>> -->
## casefile

This project keeps its investigation state in an append-only casefile log.

- At session start run `python3 casefile.py resume-context`, then
  `python3 casefile.py recheck --startup`, then `python3 casefile.py status`,
  and act on what they say.
- File hypotheses, decisions, observations, and questions as you work —
  the conventions in `.claude/skills/casefile/SKILL.md` apply to any agent,
  not just Claude. Never edit `.casefile/log.jsonl` by hand.
<!-- <<< casefile <<< -->
