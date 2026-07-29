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
  `--ref`/`--reject`/`--supersede` flags. Use `--json` receipts when another
  process must parse the id. A constraint correction can supersede an older
  constraint only with the same authority and `--rationale`; decisions still
  use revoke/replace.
- In multi-model work, never directly promote a recommendation: file
  `--kind candidate`, have a different author review that exact id, then use
  `finalize-digest`. Reference the frozen casefile requirement ids with
  repeatable `--ref` so later replacements mark the judgment stale. A model
  recommendation, cross-model consensus, stale judgment, and user decision
  are distinct.

## Recognizing casefile-directed speech

| user says | you do |
|---|---|
| "where are we on X?" | `boot` (or `resume-context`) → prose summary sized to the question |
| "don't touch X" | `add -t constraint -a user` |
| "I'm not convinced by X" | `dispute -a user` |
| "why did we rule out X?" | `dig "<query>"` (searches superseded history; expands digests) |
| "have we seen this before?" | `recall "<query>"` (searches past-case abstracts) |
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
casefile cheatsheet [-h]
casefile init [-h]
casefile open [-h] [--goal GOAL] title
casefile add [-h] -t {constraint,decision,hypothesis,note,observation,question} -a AUTHOR [--body-stdin] [--case CASE] [--refs [REFS ...]] [--ref REF] [--rationale RATIONALE] [--rejected [OPTION:REASON ...]] [--reject OPTION:REASON] [--source SOURCE] [--source-uri SOURCE_URI] [--source-type SOURCE_TYPE] [--published-at PUBLISHED_AT] [--accessed-at ACCESSED_AT] [--effective-at EFFECTIVE_AT] [--expires-at EXPIRES_AT] [--locator LOCATOR] [--jurisdiction JURISDICTION] [--check CHECK] [--claim-mode {association,causal-inference,diagnosis,forecast,mechanistic,normative-premise,recommendation}] [--mechanism MECHANISM] [--comparator COMPARATOR] [--analysis-layer ANALYSIS_LAYER] [--falsifier FALSIFIER] [--counterfactual COUNTERFACTUAL] [--horizon HORIZON] [--testability {external-now,longitudinal,not-empirical,within-session}] [--supersedes [SUPERSEDES ...]] [--supersede SUPERSEDE] [--to TO] [--json] [body]
casefile endorse [-h] -a AUTHOR [--comment COMMENT] entry
casefile dispute [-h] -a AUTHOR --reason REASON entry
casefile revoke [-h] -a AUTHOR --reason REASON entry
casefile resolve [-h] -a AUTHOR --outcome {upheld,withdrawn,answered,fulfilled} --reason REASON entry
casefile verify [-h] -a AUTHOR [--comment COMMENT] entry observation
casefile digest [-h] [--body-stdin] -a AUTHOR --kind {abstract,candidate,judgment,mechanical} [--supersedes [SUPERSEDES ...]] [--supersede SUPERSEDE] [--refs [REFS ...]] [--ref REF] [--case CASE] [--json] [body]
casefile finalize-digest [-h] candidate
casefile show [-h] [--case CASE] [--observations OBSERVATIONS]
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
casefile checkpoint [-h] [-a AUTHOR] [--case CASE] [body]
```
