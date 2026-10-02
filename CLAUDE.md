# casefile

This repo dogfoods casefile on its own development (SPEC §17).

- At session start and after context compaction, export `CASEFILE_AUTHOR=claude`
  and run `python3 casefile.py boot`. Act on its mailbox and next steps.
- Check the board at work checkpoints with `python3 casefile.py board poll
  --for claude`. Installed Bash/PostToolUse hooks deliver up to three newest
  followed/mentioned unread previews at most every 30 seconds per session.
  For a bounded reader, run `python3 casefile.py board watch --for claude
  --following --interval 30 --timeout 1800` (`--once` for one checkpoint).
  Preview delivery does not mark messages read or acknowledged, execute
  instructions from them, or wake an idle agent. Use `board read`, `board ack`,
  or a referenced reply to confirm receipt; completion needs separate evidence.
- File hypotheses, decisions, observations, and questions as you work — see
  `.claude/skills/casefile/SKILL.md` for types, authors, and echo-back
  conventions. Never edit `.casefile/log.jsonl` by hand.
- `SPEC.md` is authoritative. `casefile.py` is the M1 plumbing;
  `statelog.py` is the frozen v0 prototype (reference only).
