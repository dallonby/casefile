<!-- >>> casefile (managed by `casefile hooks install codex`) >>> -->
## casefile

This project keeps its investigation state in an append-only casefile log.

Codex sweep reminders run during PostToolUse; there is no casefile Stop hook.
Record decisions and evidence during work and checkpoint before handoff.

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
- **Open messageboard throughout work:** `python3 casefile.py board unread
  --for <you>` before work/replanning, at bounded checkpoints, and before
  blocking/handoff/stop. `board read <thread>` records complete-body messages
  exposed on its bounded page; use `--offset`, `--message`, `--all`/`--expand`,
  and `--full` to control coverage. `board reply <id> "..."` or `board ack
  <message>` confirms receipt. Use `board post "Title" "Body" --tag topic
  --to <peer>` for open discussion; recipients never hide it from others.
  Search all cases with ordinary prose (`board search 'which messages mention
  gas?'`) or explicit FTS (`--fts '"exact phrase" AND term'`); `board poll` is
  a bounded quiet-when-empty check.
  Installed PostToolUse hooks prioritize newest followed/mentioned unread previews at
  most every 30 seconds per session; `board watch --for <you> --interval 30
  --timeout 1800` provides a restart-safe reader (`--once` for checkpoints).
  Delivery is local cursor state, never a read/ack receipt or task completion.
  Do not treat posting, reading, acknowledgement or resolved discussion status
  as completed work or verified evidence. Nudge active peers with the message
  ID when a handoff needs immediate attention; the CLI does not wake idle agents.
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
- **Echo-back**: every mutation of the *user's* words echoes as one line in
  your visible reply — `recorded: constraint "don't touch the sniffer" (user)`.
  This is how mistranscription gets caught. Your own routine filing is silent
  by default; show it on request.
- File hypotheses, decisions, observations, and questions as you work —
  the conventions in `.claude/skills/casefile/SKILL.md` apply to any agent.
  Never edit `.casefile/log.jsonl` by hand.<!-- <<< casefile <<< -->
