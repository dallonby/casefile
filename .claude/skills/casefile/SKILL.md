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
python3 casefile.py upgrade
# = git pull this CLI + symlink onto PATH + hooks install (SKILL.md, AGENTS, hooks)
```

After upgrade, `casefile` on PATH works too. Put `python3 casefile.py upgrade`
in agent launch scripts so SKILL.md never drifts from the CLI.

## Identity (do this first — every session, every agent)

**You MUST export your own identity before filing anything:**

```bash
export CASEFILE_AUTHOR=claude    # Anthropic models (fable/sonnet/opus alias here)
# export CASEFILE_AUTHOR=codex   # OpenAI / Codex
# export CASEFILE_AUTHOR=grok    # xAI (grok45/grok4/… alias here)
```

- Run `python3 casefile.py whoami` — if it says `from default` / author `agent`,
  **stop and export**.
- Boot exit code **40** means identity unset. Grades, endorse/dispute, and packets
  depend on a real author; anonymous `agent` is not multi-agent safe.
- Always pass the same identity via `-a $CASEFILE_AUTHOR` on writes if env cannot stick.

## Session start

1. `export CASEFILE_AUTHOR=…` (see Identity above). Prefer
   `python3 casefile.py upgrade` at launch.
2. **Prefer one command:** `python3 casefile.py boot`
   (discovers the store, stamps author, runs `recheck --startup`, prints
   WHERE / YOU ARE / WORLD vs LOG / BRIEF / DO NOT / NEXT / CARD).
   Exit codes: 0 ok, 10 mailbox, 20 drift, 30 abstract stale, **40 identity unset**.
   Act on NEXT; surface mailbox once, don't block.
3. Legacy equivalent: `resume-context` then `recheck --startup` then `status`.
   Ground truth beats the notes where they conflict.
4. Multi-agent handoff (no shared chat): `python3 casefile.py packet --to <peer>`,
   peer runs `inbox --for <self>` + `boot`. Checkpoint with
   `python3 casefile.py checkpoint` before long gaps so `recall` sees the
   distilled problem.

## Open messageboard — use throughout the session

The board is shared across every case in this project. Every agent can browse,
search, reply and update a discussion; mentions and follows select attention,
never visibility. Keep secrets out: the board uses the same tracked log.

- Before a work unit, after a bounded tool/experiment checkpoint, before a
  plan change, and before declaring blocked or stopping: run
  `python3 casefile.py board poll --for "$CASEFILE_AUTHOR"`.
- `board unread --for <you>` lists public activity, including posts with no
  recipient. `board read <thread>` shows a bounded 20-message page and records
  exact complete-body messages seen. `board show <thread>` inspects without
  marking read. Use `--offset` for pages, `--message` for one exact message,
  `--all`/`--expand` for every message, and `--full` for complete bodies.
  Clipped previews get a separate receipt and remain unread. Neither posting
  nor booting silently marks someone else's messages read.
- Start useful discussions with `board post "Title" "Body" --tag topic`.
  Use repeatable `--to <author>` for attention and `--ref <id>` for evidence;
  everyone can still read and join. Use `--body-stdin` for multiline content.
- Respond with `board reply <message-or-thread> "Findings / next action"`.
  `board ack <message>` acknowledges receipt only. To report progress use
  `board status <thread> open|in-progress|blocked|resolved "Reason"`; this
  does not verify a claim, fulfill a decision or grant permission.
- Search before duplicating work: `board search 'which messages mention gas?'`.
  Ordinary questions use ranked lexical term matching (not semantic or
  embedding search); `--natural` forces that mode. `--fts` preserves explicit
  phrases, AND/OR/NOT, prefix*, and author/title/tags fields, with exact
  `--case`, `--tag`, `--by`, `--status` filters. Use `--offset` for further
  pages; JSON has bounded previews by default and `--full` includes complete
  matching bodies.
- `board follow <thread>`, `board follow --tag topic`, or `board follow --all`
  selects optional `--following` views. Everyone can see the whole board.
- Installed Bash/PostToolUse hooks prioritize up to three newest followed/mentioned
  unread previews at most every 30 seconds per session, including quiet checks.
  `board watch --for <you> --interval 30 --timeout 1800` runs a restart-safe
  reader; add `--thread <id>`, `--following`, or `--once` for a timer/checkpoint.
  Delivery cursors are local and separate from read/ack receipts; previews
  remain unread until explicitly read or acknowledged. A new session can
  rediscover unprocessed messages. No commands are executed from board text.
- When immediate attention matters, send a short native-agent/tmux nudge
  pointing to the board message ID. A message posted is not delivery proof;
  require a referenced reply/ack for consequential handoffs. The CLI does not
  wake an idle agent automatically.

Board messages are discussion notes, not promoted decisions. File resulting
decisions/observations with their proper types and link the discussion IDs.

## Filing conventions (types and authors matter — grades are computed from them)

- **hypothesis** — falsifiable claim, author is whoever proposed it. Add
  `--check '<shell>'` when a one-liner can test it (exit 0 = still holds).
  For any claim that could drive a ranking/decision, also record its
  `--claim-mode`, `--comparator`, `--analysis-layer`, `--falsifier`,
  `--counterfactual`, `--horizon`, `--testability`, and (for causal claims)
  `--mechanism`.
- **decision** — author `user` ONLY for choices the user actually made;
  your own proposals are author `claude` (they render as "asserted, not
  user-confirmed"). Always give `--rationale`; record losing alternatives
  with `--rejected "option:reason"` so they aren't re-proposed.
- **observation** — ground truth only: test output, command results, log
  lines, with `--source`. Never file your own inference as an observation.
  Remote/time-sensitive evidence should carry `--source-uri`,
  `--source-type`, `--accessed-at`, `--effective-at`, `--expires-at`, and a
  precise `--locator` when available.
- **verify** — links a hypothesis to a real observation. Model agreement is
  never verification; endorse instead (`consensus` is explicitly weaker).
- **dispute** when you disagree with a recorded claim; `resolve` with
  `--outcome upheld|withdrawn|answered` when settled.
- **question --to user** for things only the user can answer (the mailbox).
- **digest** at checkpoints (`--kind judgment`), and keep the rolling
  abstract current (`--kind abstract`; `--supersedes` is automatic for
  abstracts): problem, status with grade in words, leading theory,
  ruled-out list, key decisions, open items. Run `reindex` after.
- Prefer `--body-stdin` for multiline text and repeatable singular
  `--ref`/`--reject`/`--supersede`/`--answer`/`--fulfill`/`--verify` flags.
  Use `--json` receipts when another process must parse the id.
- **Close lifecycle transitions inline on `add`:**
  - Answering a question → pass `--answer <q-id>` on the answering decision,
    observation, hypothesis, constraint, or note (auto-links `<q-id>` and
    appends the `answered` resolution atomically).
  - Shipping a decision → pass `--fulfill <dec-id>` on the ground-truth
    `observation` (auto-links `<dec-id>` and marks it `fulfilled` atomically;
    standalone `done <dec-id>` still works afterwards).
  - Testing a hypothesis → pass `--verify <hyp-id>` on the ground-truth
    `observation` (auto-links `<hyp-id>` and appends the `verification`
    atomically; standalone `verify <hyp-id> <obs-id>` still works afterwards).
  - Revising a hypothesis/constraint/decision → pass `--supersede <id>` (plus
    `--rationale "…"` for constraints/decisions; same author, or `user`
    overriding anyone). Prose that cites a live same-type ID with replacement
    language without `--supersede` is rejected at write time.
- **Self-contained bodies & `--scope turn`:** never file bare chat affirmations
  (`"yes please"`, `"ok"`, `"go ahead"`) or unanchored item numbers (`"(2)"`)
  as decisions/constraints — restate the referent so a fresh session can read
  it cold. For tactical one-turn instructions ("run the profiler now", "skip
  the slow suite this turn"), pass `--scope turn`: it stays live during the
  turn and expires automatically once followed by a sweep, checkpoint/judgment
  digest, or session gap. `revoke` is for retraction; superseded/fulfilled/
  revoked/expired entries leave boot, resume-context and show (`thread`/`dig`
  still show them).
- In multi-model work, never directly promote a recommendation: file
  `--kind candidate`, have a different author review that exact id, then use
  `finalize-digest`. Reference the frozen casefile requirement ids with
  repeatable `--ref` so later replacements mark the judgment stale. A model
  recommendation, cross-model consensus, stale judgment, and user decision
  are distinct.

## Recognizing casefile-directed speech

| user says | you do |
|---|---|
| "where are we on X?" | `where "<X>"` (computed STATE of the thread: latest live decision, open questions, what was ruled out, last verification); `thread "<X>"` for the chain; `boot` / `resume-context` for the whole case |
| "that's done" / "we shipped X" | `done <decision-id> --evidence <obs-id or text>` — **confirm first** |
| "X replaces Y" / "new plan for X" | `add -t decision --supersede <old-id> --rationale "…"` |
| "don't touch X" | `add -t constraint -a user` |
| "I'm not convinced by X" | `dispute -a user` |
| "why did we rule out X?" / "how did we do X?" | `dig "<query>"` then `show <id>` on a hit. Do not grep log.jsonl or a sidecar chat transcript. |
| "show me entry 0776174a" | `show 0776174a` (full body). `dig <id>` expands digest/supersession. |
| "have we seen this before?" | `recall "<query>"` (past-case abstracts only — not operational how-to) |
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
  ~3 windows without progress), propose escalating to a manifest-backed
  spitball.

## Before every consequential debate

1. Sweep the current conversation into the log *before* launching models:
   verbatim user requirements/constraints, decisions, open questions, and
   already-mentioned alternatives. Do not wait until after architectural
   convergence.
2. Freeze a deliberation manifest. Include requirements, evaluation criteria
   and weights (mark user-confirmed vs inferred), evidence domains, competing
   alternatives/packages, analysis layers, and known open questions. Give
   every alternative the same criteria and implementation-detail budget.
3. Prefer `spitball --manifest <json> --manifest-mode enforce`. If the user
   does not want to supply weights, record that they are inferred and use
   `warn`: exploration may continue, but casefile should not manufacture a
   final judgment from missing normative input.
4. Require the verbose independent round-by-round synopses and exact
   opening/round ledger; they are echoed in full and retained in `run.json`.
   Treat manifest coverage as "addressed", never as agreement or verification.
5. Continuity comes from each adapter's continuous vendor session plus the
   atomic `transcripts/<session>/run.json`, not tmux. After interruption use
   `spitball-recover <session>`; tmux is only a viewport.
6. Finalization is guarded: only convergence, complete coverage, aligned
   summaries, no live disputes/questions, complete claim cards, and exact
   candidate review can create a judgment. Turn/spend budget and stalemate
   preserve the differential as-is.

## Command cheatsheet (generated from the CLI — flags come after the subcommand)

```
casefile board [-h] {post,reply,show,read,ack,status,follow,unfollow,list,search,tail,watch,unread,poll} ...
casefile board post [-h] [-a AUTHOR] [--json] [--body-stdin] [--case CASE] [--tag TAG] [--to TO] [--ref REF] title [body]
casefile board reply [-h] [-a AUTHOR] [--json] [--body-stdin] [--to TO] [--ref REF] entry [body]
casefile board show [-h] [-a AUTHOR] [--json] [--limit LIMIT] [--offset OFFSET] [--all] [--full] [--message] entry
casefile board read [-h] [-a AUTHOR] [--json] [--limit LIMIT] [--offset OFFSET] [--all] [--full] [--message] entry
casefile board ack [-h] [-a AUTHOR] [--json] entry
casefile board status [-h] [-a AUTHOR] [--json] [--body-stdin] entry {open,in-progress,blocked,resolved} [body]
casefile board follow [-h] [-a AUTHOR] [--json] [--tag TAG] [--all] [entry]
casefile board unfollow [-h] [-a AUTHOR] [--json] [--tag TAG] [--all] [entry]
casefile board list [-h] [-a AUTHOR] [--json] [--case CASE] [--tag TAG] [--by BY] [--status {open,in-progress,blocked,resolved}] [--limit LIMIT] [--offset OFFSET] [--following]
casefile board search [-h] [-a AUTHOR] [--json] [--case CASE] [--tag TAG] [--by BY] [--status {open,in-progress,blocked,resolved}] [--limit LIMIT] [--offset OFFSET] [--fts | --natural] [--full] query
casefile board tail [-h] [-a AUTHOR] [--json] [-n LINES] [-f] [--full] [--thread THREAD] [--case CASE] [--tag TAG] [--by BY] [--status {open,in-progress,blocked,resolved}]
casefile board watch [-h] [-a AUTHOR] [--json] [--case CASE] [--tag TAG] [--by BY] [--status {open,in-progress,blocked,resolved}] [--limit LIMIT] [--for FOR_AUTHOR] [--thread THREAD] [--following] [--reader READER] [--interval INTERVAL] [--timeout TIMEOUT] [--once] [--newest-first] [--cooldown COOLDOWN] [--full]
casefile board unread [-h] [-a AUTHOR] [--json] [--case CASE] [--tag TAG] [--by BY] [--status {open,in-progress,blocked,resolved}] [--limit LIMIT] [--offset OFFSET] [--for FOR_AUTHOR] [--following] [--full]
casefile board poll [-h] [-a AUTHOR] [--json] [--case CASE] [--tag TAG] [--by BY] [--status {open,in-progress,blocked,resolved}] [--limit LIMIT] [--offset OFFSET] [--for FOR_AUTHOR] [--following] [--full]
casefile cheatsheet [-h]
casefile init [-h]
casefile open [-h] [--goal GOAL] title
casefile add [-h] -t {constraint,decision,hypothesis,note,observation,question} -a AUTHOR [--body-stdin] [--case CASE] [--refs [REFS ...]] [--ref REF] [--rationale RATIONALE] [--rejected [OPTION:REASON ...]] [--reject OPTION:REASON] [--source SOURCE] [--source-uri SOURCE_URI] [--source-type SOURCE_TYPE] [--published-at PUBLISHED_AT] [--accessed-at ACCESSED_AT] [--effective-at EFFECTIVE_AT] [--expires-at EXPIRES_AT] [--locator LOCATOR] [--jurisdiction JURISDICTION] [--check CHECK] [--claim-mode {association,causal-inference,diagnosis,forecast,mechanistic,normative-premise,recommendation}] [--mechanism MECHANISM] [--comparator COMPARATOR] [--analysis-layer ANALYSIS_LAYER] [--falsifier FALSIFIER] [--counterfactual COUNTERFACTUAL] [--horizon HORIZON] [--testability {external-now,longitudinal,not-empirical,within-session}] [--supersedes [SUPERSEDES ...]] [--supersede SUPERSEDE] [--answers [ANSWERS ...]] [--answer ANSWER] [--fulfills [FULFILLS ...]] [--fulfill FULFILL] [--verifies [VERIFIES ...]] [--verify VERIFY] [--scope {case,turn}] [--to TO] [--force] [--json] [body]
casefile endorse [-h] -a AUTHOR [--comment COMMENT] entry
casefile dispute [-h] -a AUTHOR --reason REASON entry
casefile revoke [-h] -a AUTHOR --reason REASON entry
casefile resolve [-h] -a AUTHOR --outcome {upheld,withdrawn,answered,fulfilled} --reason REASON entry
casefile verify [-h] -a AUTHOR [--comment COMMENT] entry observation
casefile digest [-h] [--body-stdin] -a AUTHOR --kind {abstract,candidate,judgment,mechanical} [--supersedes [SUPERSEDES ...]] [--supersede SUPERSEDE] [--refs [REFS ...]] [--ref REF] [--case CASE] [--json] [body]
casefile finalize-digest [-h] candidate
casefile show [-h] [--case CASE] [--observations OBSERVATIONS] [entry]
casefile resume-context [-h] [--case CASE] [--blind] [--observations OBSERVATIONS] [--budget BUDGET]
casefile recheck [-h] [--case CASE] [--timeout TIMEOUT] [--startup] [--json]
casefile sync-journal [-h] [--case CASE]
casefile compact [-h] [--case CASE]
casefile reindex [-h]
casefile recall [-h] [--limit LIMIT] query
casefile dig [-h] [--limit LIMIT] query
casefile import [-h] [--case CASE] file
casefile hooks [-h] {install} {claude-code,codex,all}
casefile upgrade [-h] [--no-pull] [--ignore-pull-fail] [--no-reexec] [--no-hooks] [--bin-dir BIN_DIR] [--force-link] [--vendor {claude-code,codex,all}] [-a AUTHOR] [--json]
casefile channel [-h] [name]
casefile ui [-h] [--dry-run]
casefile talk [-h]
casefile spitball [-h] --topic TOPIC [--models MODELS] [--turns TURNS] [--budget-usd BUDGET_USD] [--blind BLIND] [--manifest MANIFEST] [--requirement REQUIREMENT] [--criterion CRITERION] [--weighting WEIGHTING] [--alternative ALTERNATIVE] [--evidence-domain EVIDENCE_DOMAIN] [--analysis-layer ANALYSIS_LAYER] [--open-question OPEN_QUESTION] [--manifest-mode {enforce,warn,off}] [--output-retries OUTPUT_RETRIES]
casefile spitball-recover [-h] [--turns TURNS] [--budget-usd BUDGET_USD] session
casefile lint [-h] [--launder-threshold LAUNDER_THRESHOLD] [--stale-threshold STALE_THRESHOLD]
casefile status [-h] [--json] [-a AUTHOR]
casefile persistence [-h] [--url URL] [--no-reconcile] [--join-existing] [--json] [{status,reconcile,enable,disable}]
casefile log [-h] [-n N]
casefile whoami [-h] [-a AUTHOR] [--json]
casefile preflight [-h] [-a AUTHOR] [--json] [--receipt RECEIPT] [--nonce NONCE]
casefile boot [-h] [--case CASE] [-a AUTHOR] [--budget BUDGET] [--skip-recheck] [--ok-exit]
casefile packet [-h] --to TO [-a AUTHOR] [--case CASE] [--no-file]
casefile inbox [-h] [--for FOR_AUTHOR] [-a AUTHOR] [--json]
casefile next [-h] [--case CASE] [-a AUTHOR]
casefile since [-h] [--case CASE] [-a AUTHOR] [--limit LIMIT] [--json]
casefile done [-h] -a AUTHOR [--evidence EVIDENCE] [--reason REASON] entry
casefile thread [-h] [--case CASE] [--depth DEPTH] [--limit LIMIT] query
casefile where [-h] [--case CASE] [--depth DEPTH] [--limit LIMIT] query
casefile checkpoint [-h] [-a AUTHOR] [--case CASE] [body]
```
