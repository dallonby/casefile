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
  `python3 casefile.py dig "<topic>"` then `show <id>` on a hit (and
  `recall` for past-case abstracts) and cite what you find in `--refs`.
  Do not grep log.jsonl or a sidecar chat transcript. Decisions carry
  `--rationale` and `--rejected` for losing options.
- **Before a consequential spitball**, sweep the current conversation into
  the log and freeze a manifest of verbatim requirements, criteria/weights,
  alternatives, evidence domains, analysis layers, and open questions.
  Prefer `--manifest-mode enforce`; use `warn` only when an exploratory run
  may proceed without manufacturing a final judgment.
- **Echo every entry you file** as one line in your visible reply —
  `recorded: decision "…" (user)` — your own filings included.
- File hypotheses, decisions, observations, and questions as you work —
  the conventions in `.claude/skills/casefile/SKILL.md` apply to any agent.
  Never edit `.casefile/log.jsonl` by hand.
<!-- <<< casefile <<< -->
