# casefile

This repo dogfoods casefile on its own development (SPEC §17).

- At session start run `python3 casefile.py resume-context`, then
  `python3 casefile.py status`, and act on what they say.
- File hypotheses, decisions, observations, and questions as you work — see
  `.claude/skills/casefile/SKILL.md` for types, authors, and echo-back
  conventions. Never edit `.casefile/log.jsonl` by hand.
- `SPEC.md` is authoritative. `casefile.py` is the M1 plumbing;
  `statelog.py` is the frozen v0 prototype (reference only).
