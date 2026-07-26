<!-- >>> casefile (managed by `casefile hooks install codex`) >>> -->
## casefile

This project keeps its investigation state in an append-only casefile log.

- **Upgrade / keep skill current:** from the project root run
  `python3 casefile.py upgrade` (git-pulls the casefile checkout, installs a
  `casefile` symlink on PATH, rewrites SKILL.md + hooks from that CLI). Put
  this in agent launch scripts so every session starts on current porcelain.
- **REQUIRED every session:** `export CASEFILE_AUTHOR=<your-id>` then
  `python3 casefile.py boot`. Pick a durable id for *this* agent (e.g.
  `claude`, `codex`, `grok`; `fable`→claude, `grok45`→grok). If `whoami` shows author
  `agent` / `from default`, stop and export first (boot exit 40). Never file
  as anonymous `agent`.
- Handoff via the log: `python3 casefile.py packet --to <peer>`,
  `inbox --for <you>`, `next`.
- Checkpoint abstracts: `python3 casefile.py checkpoint` then `recall`.
- **After any context compaction or summarization**, re-run
  `python3 casefile.py boot` (or `resume-context`) before acting. The log
  outranks compacted summary.
- **Before filing a decision or changing an agreed plan**, run
  `python3 casefile.py dig "<topic>"` (and `recall`) and cite what you find
  in `--refs`. Decisions carry `--rationale` and `--rejected` for losing
  options.
- **Echo every entry you file** as one line in your visible reply —
  `recorded: decision "…" (user)` — your own filings included.
- File hypotheses, decisions, observations, and questions as you work —
  the conventions in `.claude/skills/casefile/SKILL.md` apply to any agent.
  Never edit `.casefile/log.jsonl` by hand.
<!-- <<< casefile <<< -->
