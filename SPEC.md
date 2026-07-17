# CASEFILE — Product Specification v1.0

*Working name: **casefile** (final name TBC by David; the v0 prototype is called
`statelog` and ships alongside this spec as `statelog.py`). This document is
the authoritative handoff for building the product. Where it conflicts with
the v0 code, the spec wins; the code is a proven starting point, not a
constraint.*

---

## 1. What this is

Casefile is an append-only, epistemically-graded record of an investigation —
a bug hunt, a diagnosis, a design deliberation — that outlives any single
model context window and any single model vendor. It gives:

- **Continuity**: a fresh model instance (or human) can resume a
  twenty-window task from a compact, honest briefing.
- **Epistemic hygiene**: hypotheses, decisions, and ground truth are
  structurally distinguished; claims cannot silently upgrade themselves.
- **Multi-model deliberation**: two or more models (e.g. Claude Code and
  Codex) argue over a shared record as peers, with disputes and endorsements
  as first-class entries.
- **Cross-investigation memory**: dormant cases distil into a searchable
  compost of abstracts, surfaced automatically when a new problem resembles
  an old one.

The human-facing interface is **conversation**, not flags. The CLI exists and
is complete, but it is plumbing operated by models on the user's behalf.

## 2. Founding principles

These are load-bearing. Any implementation decision that violates one of
these is wrong even if it is convenient.

**P1. The log is the product; everything else is disposable.** Drivers,
concierges, UIs, and indexes may be killed, rewritten, or lost with zero loss
of consequence. Test: `kill -9` any process mid-session; on restart, nothing
of consequence is gone. If something was lost, that component held state it
should not have.

**P2. Append-only; corrections are new entries.** No entry is ever edited or
deleted. Supersession (see digests) hides entries from compiled views but
never removes them from the log.

**P3. Grades are computed, never stored.** A claim's epistemic grade is
derived from the entry stream at read time. Storing a grade lets it drift
from its evidence.

**P4. Verification requires ground truth.** Only an `observation` entry
(test output, log lines, command results) can make a hypothesis `verified`.
No amount of model agreement can; agreement produces only `consensus`, which
is explicitly weaker — models share blind spots.

**P5. Provenance travels in words.** Any compiled view handed to a model
spells out epistemic status in prose ("an unverified hypothesis", "the user
decided", "cross-model consensus — NOT independently verified") so claims
arrive sounding like what they are.

**P6. Ground truth beats the notes.** Every resume briefing opens by saying
so. Where log and world conflict, the world wins; the log records the
discrepancy as a new observation.

**P7. Compaction is an epistemic act.** Digests have authors, can be
disputed, and are subject to lint. A careless summary is the stealthiest
laundering vector in the system.

**P8. The coordinator is a switchboard, not a brain.** Any orchestrating
process (driver, concierge) holds no plan, no opinions about the task, and no
memory beyond the log. It routes; it never gates the hot path.

**P9. Human effort is optional everywhere.** Nudges may be ignored; silence
merely lowers a grade (e.g. `inferred-resolved` vs `user-confirmed`), never
blocks progress. Nothing requires ceremony from the user.

**P10. Vendor-neutral by construction.** The interface any model needs is
"can run a CLI and produce text." Vendor-specific integration (hooks, skills)
lives in thin adapters at the edges.

**P11. Model-authored text from the world is untrusted.** Observations can
contain adversarial strings (a malicious file quoted in a test failure).
Compiled views must fence observation bodies as data-not-instructions.

## 3. Terminology

| term | meaning |
|---|---|
| **log** | `.casefile/log.jsonl` — append-only source of truth, per repo |
| **case** | one investigation; entries carry a `case` field; a repo holds many |
| **entry** | one JSON line in the log |
| **grade** | computed epistemic status of a claim |
| **digest** | an entry summarizing and superseding a span of prior entries |
| **abstract** | the rolling per-case digest: problem, status, differential, ruled-out list |
| **differential** | the set of live hypotheses for a case, grouped by grade |
| **compost** | dormant cases' abstracts, indexed for recall |
| **plumbing** | the `casefile` CLI — complete, flag-dense, model-operated |
| **porcelain** | the conversational layer humans actually use |
| **concierge** | the model session behind the conversation pane; a stateless switchboard |
| **driver** | the process running a multi-model spitball session |
| **hot path** | mechanical, model-free routing of `@addressed` user messages |
| **secretary sweep** | end-of-session diff of conversation vs log to catch unrecorded decisions |

## 4. Architecture

```
┌─ humans ────────────────────────────────────────────────┐
│  conversation (porcelain)          tmux UI (viewport)   │
└───────────────┬─────────────────────────┬───────────────┘
                │ natural language        │ read-only tail
┌───────────────▼─────────────┐  ┌────────▼───────────────┐
│ concierge / working session │  │ transcripts, state view│
│ (model + casefile skill)    │  └────────▲───────────────┘
└───────────────┬─────────────┘           │
                │ CLI calls        ┌──────┴───────┐
┌───────────────▼─────────────┐   │ driver       │← model adapters
│ plumbing: casefile CLI      │◄──┤ (spitball)   │  (claude, codex, …)
└───────────────┬─────────────┘   └──────────────┘
                │ append / read
┌───────────────▼─────────────────────────────────────────┐
│ .casefile/log.jsonl  (truth)                            │
│ .casefile/index.db   (FTS cache — destroyable)          │
│ .casefile/meta.json, transcripts/, config               │
└─────────────────────────────────────────────────────────┘
```

Language: **Python ≥3.10, stdlib only** for plumbing and driver (`subprocess`,
`json`, `argparse`, `sqlite3`). Layout scripts may be thin bash. No runtime
dependencies is a feature: the tool must run anywhere a model can shell out.

Repo discovery: git-style — walk upward from cwd for `.casefile/`.
Everything under `.casefile/` except `index.db` and `transcripts/` belongs in
git (provide a starter `.gitignore`).

## 5. Data model

### 5.1 Storage

- `log.jsonl` — one JSON object per line, UTF-8, append + fsync. Never
  rewritten. Concurrent writers: acquire `log.lock` (O_CREAT|O_EXCL lockfile
  with stale-lock timeout) around append; reads are lock-free.
- `meta.json` — repo-level metadata and the case registry:
  `{schema, created, cases: {case_id: {title, goal, created}}}`.
- `index.db` — SQLite FTS5 recall index. A cache: rebuildable at any time
  via `casefile reindex`; corruption or deletion is a non-event (P1).
- `transcripts/<session>/<model>.log` — raw spitball transcripts. Ephemeral,
  gitignored; the log is the distillate.
- `config.toml` — models, adapters, budgets, echo volume, hook verbosity.

### 5.2 Entry envelope (all types)

```json
{"id":"8-char-hash", "ts":"ISO-8601 UTC", "case":"case_id",
 "type":"…", "author":"user|claude|codex|system|…",
 "body":"the claim/decision/observation itself", "refs":["id", "…"]}
```

- `id`: 8 hex chars, unique within the log; derived from time+nonce+body.
- `refs` must reference existing ids (validated on append) — a sound graph
  is what makes lint meaningful.
- `case`: every entry belongs to exactly one case. The CLI resolves the
  active case automatically (last touched, per config); `--case` exists as
  an explicit override but the porcelain never requires the user to know it.

### 5.3 Entry types

| type | purpose | extra fields |
|---|---|---|
| `hypothesis` | falsifiable claim by a model | `check` (optional shell recipe, §8) |
| `decision` | a choice constraining future work | `rationale`, `rejected` (list of `{option, reason}` — the losing alternatives, so they aren't re-proposed) |
| `observation` | ground truth from the world | `source` (`pytest`, `git`, `hook:*`, `manual`, …) |
| `constraint` | invariant that must hold | `check` (optional) |
| `question` | open unknown; user-authored questions form the **mailbox** | `to` (optional: `user`, `any`) |
| `endorsement` | author X supports another author's entry | `comment` |
| `dispute` | author X challenges entry; **blocks promotion** while open | `reason` in body |
| `resolution` | closes a dispute or question; marks a decision fulfilled | `outcome`: `upheld`/`withdrawn`/`answered`; `fulfilled` (decisions only) |
| `verification` | links hypothesis → observation(s) | refs must include ≥1 observation |
| `digest` | summarizes and hides a span | `supersedes` (list of ids), `kind`: `mechanical`/`judgment`/`abstract` |
| `revocation` | explicitly retires a constraint or decision | refs the retired entry |
| `note` | anything else; zero epistemic weight | — |

Rules enforced at append time:
- self-endorsement rejected (endorser ≠ target author);
- `verification` requires ≥1 `observation` ref and ≥1 `hypothesis` ref;
- `digest.supersedes` may NOT include: unrevoked constraints, undismissed
  decisions, open disputes/questions, or observations referenced by any
  verification (**the evidence-chain invariant** — see also lint §7).
  A decision is *dismissed* by revocation (retracted) **or** by a
  `resolution` with `outcome: fulfilled` (the work it mandated shipped and
  was observed — distinct from retraction; the digest that supersedes it
  must carry the residue). Revoke ≠ fulfil: the record must not read a
  completed plan as a reversed one;
- refs must exist and belong to the same case (digests exempted for
  cross-case abstracts only).

### 5.4 Epistemic grades (computed per P3)

For a `hypothesis`, first match wins:

1. `disputed` — has ≥1 open dispute (dispute with no resolution).
2. `verified` — referenced by a verification whose refs include ≥1 observation.
3. `consensus` — endorsed by ≥1 author ≠ its own author.
4. `hypothesis` — default.

Other grades: `observation` → `ground-truth`; user-authored
`decision`/`constraint` → `stated`; model-authored `decision`/`constraint` →
`asserted` (rendered as "asserted, not user-confirmed"). A revoked
constraint/decision is grade `revoked` and drops from compiled views (but
its revocation is shown in `dig`).

Provenance phrases (P5) for compiled views:

```
stated      → "the user decided"
ground-truth→ "[<source>] observation"
verified    → "verified against ground truth"
consensus   → "cross-model consensus — NOT independently verified"
disputed    → "UNDER ACTIVE DISPUTE"
hypothesis  → "an unverified hypothesis"
asserted    → "asserted, not user-confirmed"
```

## 6. Distillation

The log grows unbounded (P2); the **working set** does not. Distillation is
non-destructive: digests hide entries from compiled views; `dig` can always
expand them.

### 6.1 Mechanical compaction (no model judgment)

Runs on hook batches or pre-commit. Targets hook-sourced observations only:

- keep the latest observation per `source`;
- keep every **transition** (pass→fail, fail→pass, new error signature —
  signature = normalized first line of the body);
- digest steady-state runs into one line: `"tests green for 47 consecutive
  runs over 6h (pytest)"` with `kind: mechanical`, `author: system`.

Never touches anything protected by the evidence-chain invariant (§5.3).

### 6.2 Judgment digests (model-authored)

Triggered at natural checkpoints: end of a spitball session, closure of a
differential branch, or when `resume-context` exceeds its token budget (the
honest trigger — the budget is why distillation exists). A model writes the
digest; because compaction is an epistemic act (P7):

- the digest carries its author;
- in multi-model settings, a **second model reviews the digest against the
  raw span** with a narrow adversarial brief — "find anything dropped or
  upgraded" — and endorses or disputes it;
- refuted hypotheses compress to one dense line each — conclusion +
  evidence pointer ("ruled out gas theory: revert strings were
  nonce-too-low (679a46cb)") — and join the case's permanent **ruled-out
  list**. Dead ends are among the most valuable artifacts in the log; the
  reasoning compresses away, the conclusion and its evidence pointer never do.

### 6.3 The rolling abstract

Each case maintains exactly one live `digest` with `kind: abstract`: problem
statement, current status *with grade in words*, leading theory, ruled-out
list, key decisions, open items. Updated (as a new abstract entry
superseding the old one) at the same checkpoints as judgment digests. The
abstract is what the recall index consumes and what dormancy files. There is
deliberately **no separate closing ceremony**: whenever a case goes quiet,
the last abstract simply is the record.

## 7. Lint (drift detection)

`casefile lint` exits 1 on findings; the concierge surfaces findings
conversationally (§11.4) — lint is a smoke alarm, not a report.

- **LAUNDERING**: hypothesis referenced by ≥N later non-meta entries
  (default 3) while still `hypothesis`/`consensus`.
- **CONSENSUS**: hypothesis at `consensus` while observations exist in the
  case that could plausibly have checked it.
- **STALE**: dispute open for ≥N entries (default 10).
- **ORPHAN**: decision with no refs and no rationale.
- **CONTRADICTION**: verified hypothesis later referenced by a dispute —
  flag for human review; the tool never adjudicates.
- **DIGEST-VIOLATION**: any digest whose supersedes list breaches the
  evidence-chain invariant (belt-and-braces with append-time checks).
- **UNSWEPT**: a session ended without a secretary sweep entry (requires
  hooks; see §13).

## 8. Recheck recipes

`hypothesis` and `constraint` entries may carry `check`: a shell command
whose exit 0 means "still holds". `casefile recheck [--case X]` runs every
recipe, appends fresh observations (`source: recheck:<id>`), and reports
drift. **A resuming instance's first act is one command that tells it which
claims still hold versus held-three-days-ago** — this turns verification
from a historical event into a reproducible property. Recipes run in the
repo root with a timeout (config, default 60s); failures are observations,
never crashes. A timed-out or broken recipe records `[UNKNOWN]` — it does
not falsify the claim, never counts as drift, and preserves the last
conclusive result as the drift baseline. Because the first command of a
resuming session must be cheap, `recheck --startup` skips recipes whose
last recorded wall-time exceeded ~5s and reports their last conclusive
result instead; the bare `recheck` remains the exhaustive pass. Per-recipe
durations live in `.casefile/state/` (derived state, not ground truth).

## 9. Case lifecycle (no explicit ending — humans don't announce "solved")

States are **computed, never stored** (same reasoning as grades):

- **active** — entries within the activity window (default 48h).
- **quiet** — no entries for the window, but resolution signals absent.
- **dormant** — quiet AND filed (auto or user-confirmed). Out of active
  surfaces; fully diggable; reactivates silently on any new entry.

Resolution signals (a cluster, not a proof): leading hypothesis `verified`;
a decision implemented (its refs show follow-up observations); hook
observations flipped green and stayed green; entry velocity ≈ 0; commits
mention other things; a new case opened.

When quiet + green signals, the concierge issues **one ignorable nudge**:
"importer case has been green for a week; anything left, or shall I file
it?" Silence files it anyway after a grace period (default 7 days). The
abstract records the terminal state honestly (P9):

- `user-confirmed resolved` — user answered the nudge;
- `inferred-resolved` — signals green, user silent;
- `stalled` — went quiet with open disputes / no discriminating evidence.
  Stalled compost is arguably the *more* valuable kind: it is the
  investigation you will otherwise re-live.

Dormancy never asserts "solved" on its own authority — that would be the
system laundering its own conclusion.

## 10. Recall index (the compost)

- SQLite **FTS5** over abstracts + judgment digests (BM25 ranking). Fields:
  case, title, status, body, ruled_out, ts.
- **The index is a cache; the log is the truth.** `casefile reindex`
  rebuilds it from scratch; it is gitignored and never backed up.
- `casefile recall "<query>"` — plumbing beneath the porcelain question
  "have we seen this before?".
- **Open-time auto-search**: when a case opens, the skill searches the
  compost with the problem statement and surfaces strong hits *before the
  first hypothesis is filed* ("this resembles the March importer case —
  encoding-sniffer theory was ruled out there, evidence attached").
  Spitball drivers seed both models' opening context with strong matches.
- Embeddings may layer on later; not in scope for v1. Well-written
  abstracts make FTS surprisingly strong — they are dense with searchable
  nouns.

## 11. Interfaces

### 11.1 Plumbing CLI (model-facing; complete; stable)

`init`, `open <title>` (creates or switches case; first mention creates —
no ceremony), `add`, `endorse`, `dispute`, `resolve`, `verify`, `revoke`,
`digest`, `show`, `resume-context [--blind]`, `recheck`, `recall`, `dig
<query>` (search superseded/raw history; expand digests), `lint`, `log`,
`reindex`, `hooks install <vendor>`, `ui`, `spitball`, `status` (JSON:
active case, mailbox count, lint count, dormancy candidates, spend).

Conventions: mutating commands print the new entry id on stdout, exit 0;
all errors to stderr, exit ≠0; `--json` on read commands for structured
output. Exit codes are API — models script against them.

`resume-context` composition, in fixed priority order with a token budget
(config, default 2000): constraints → open disputes → decisions (with
rationale + rejected alternatives) → ruled-out list → live differential
(grades in words) → open questions/mailbox → last-N observations. Eviction
pressure applies only from the bottom. Opens with the P6 sentence. `--blind`
omits the differential and ruled-out list — used for independent
replication when the recorded differential itself may be the problem
(fresh model forms its own theory; diff against the record).

### 11.2 Porcelain (human-facing; conversational)

Humans direct casefile **by talking**, inside any working session or via
`casefile talk` (a REPL wrapping a headless session with the skill). The
skill (§13) teaches sessions to recognize casefile-directed speech:

| user says | plumbing performed |
|---|---|
| "where are we on the importer thing?" | resume-context → prose summary sized to the question |
| "don't touch the encoding sniffer" | add constraint, author user |
| "I'm not convinced by the nonce theory" | dispute, author user |
| "get a second opinion, fresh eyes" | spitball with --blind seeding |
| "why did we rule out the gas theory?" | dig |
| "have we seen this before?" | recall |
| "rule that out" / "let's go with X" | resolution / decision — **with confirm** |

Trust conventions:
- **Echo-back**: every mutation of the *user's* words echoes in one line —
  `recorded: dispute against "nonce race" — 'not convinced' (user)`. This
  is how mistranscription gets caught without reading the log.
- **Confirm destructive-ish acts** (resolve, digest, revoke): one word.
  Reads never confirm.
- **Echo volume** (config): user-authored mutations echo always; the
  session's own filing is silent by default, visible on request.

Human-typed commands that must survive (the git-survivable-subset): bare
`casefile` (plain-English status), `casefile open "<problem>"`,
`casefile talk`. Everything else is reachable by talking.

### 11.3 Session-proposed opening (the beginning)

Problems start mid-conversation, not with a ritual. The skill instructs
sessions: when a debugging/diagnosis conversation shows multi-window shape
(reproduction attempts, competing theories, >1 hour of context), **propose**
opening a case and, on "yes", backfill the opening entries from the
conversation so far. The user's first interaction with the product should
be saying "yes". Also required: `casefile import` — bootstrap a case from
an existing CLAUDE.md / notes file / pasted scrollback (model-assisted
extraction into typed entries, each echoed for confirmation in bulk).

### 11.4 Push surfaces (the product may speak first — sparingly)

- **Mailbox**: open `question` entries with `to: user` surface at the start
  of the first casefile-aware session of the day ("two things waiting on
  you") and as a status-bar count. Symmetrically, sessions (including cron)
  must address open user questions before proceeding.
- **Lint, conversationally**: the concierge reads lint output and acts —
  "we've leaned on the nonce claim for three decisions and never verified
  it; shall I?" — never dumps raw findings.
- **Dormancy nudge** (§9): once, ignorable.
- **Spend**: running session cost in the status bar (headless APIs return
  per-call cost); budget breach forces digest-and-halt.

## 12. Multi-model deliberation (spitball)

### 12.1 Driver

`casefile spitball --topic "…" [--models claude,codex] [--config …]` — a
**disposable** (P1, P8) turn-ferrying loop:

1. Open one headless session per model via its adapter; seed each with
   `resume-context` (or `--blind` variants per role) + role brief + strong
   recall matches.
2. Ferry turns: model A's visible message → model B, and vice versa. Each
   model keeps its **own continuous session** (its private view of the
   argument) — never a shared scraped transcript.
3. Every turn: append transcript file; tee to viewport; models file entries
   via the CLI as they argue (the skill/role brief mandates it).
4. On session end: secretary sweep per model, judgment digest proposed by
   one model, adversarially reviewed by the other (§6.2).

Roles: default proposer/critic; models are systematically better critics of
theories they didn't generate, so each attacks the *other's* leading
hypothesis. Role briefs are prompt files in the repo (`.casefile/roles/`),
user-editable.

Stop conditions (agreeable models chat forever): **converge** (no open
disputes; leading hypothesis endorsed or verified) → digest and halt;
**turn budget** or **spend budget** → halt with the differential as-is;
**stalemate** → halt; an open dispute is a valid, valuable output.

End-of-session independent summaries: each model writes what it believes
was decided *without seeing the other's*; the driver diffs them; divergence
is a miscommunication detector; only the reconciled version is digested.

### 12.2 Model adapters

Interface per adapter: `start(context) -> handle`, `send(handle, msg) ->
reply`, `interject(handle, msg)` (mid-request if supported), `cost(handle)`,
`stop(handle)`.

- **claude adapter**: Claude Code headless. v1: `claude -p --resume
  <session_id> --output-format json` per turn (session_id captured from the
  first call's JSON). v2: long-lived `--input-format stream-json` process —
  supports injecting user messages mid-request (the hot-path property) —
  **measure interjection latency before committing the UX to it**.
- **codex adapter**: `codex exec` with session resume. Exact flags/output
  schema to be verified against the installed CLI at build time — do not
  hard-code from memory; the adapter boundary exists precisely so this is a
  30-line file.
- Adding a vendor = adding an adapter file. Nothing above the adapter knows
  which CLI it is driving (P10).

### 12.3 Directing a live session

- **Hot path** (mechanical, model-free, milliseconds): messages beginning
  `@<model>` or `@all` are injected verbatim by the driver into the target
  session(s) and logged as user turns in passing. The concierge may
  annotate after the fact; it never gates (P8).
- **Warm path**: everything else goes to the concierge for interpretation
  (spawn a blind reviewer, record a constraint, answer "where are we").
- Runtime controls: pause / resume / kill per model; killing a model
  mid-session must lose nothing of consequence (P1).

## 13. Vendor integration: hooks & skill

`casefile hooks install claude-code` writes:

- **Hook config** mapping Claude Code tool events → `casefile add -t
  observation --source hook:<event>` (test runs, failing commands, commits).
  Volume governed by config; mechanical compaction (§6.1) keeps noise down.
- **Stop hook → secretary sweep**: on session end, prompt the session to
  diff its conversation against the log — "anything decided, constrained,
  or ruled out here that isn't recorded?" — and file the gaps. Closes the
  biggest leak: the decision made conversationally in window 4 that nobody
  wrote down. The sweep files a `note` marker; lint's UNSWEPT rule keys off
  it.
- **The skill file** (`SKILL.md` dropped where Claude Code discovers it),
  teaching every session in the repo: read resume-context (and run
  `recheck`) on start; address the mailbox; file hypotheses/decisions as it
  works with correct types and authors; echo-back conventions and confirm
  rules; recognize casefile-directed speech (§11.2 table); propose case
  opening (§11.3); propose escalation to spitball when the differential
  stalls (two theories, no discriminating evidence, ~3 windows without
  progress); never edit the log by hand.

Codex-side integration mirrors this with whatever configuration/skill
mechanism Codex exposes (verify at build time).

## 14. tmux UI

`casefile ui` builds a **new window in the user's existing tmux session**
(never a nested session; user runs tmux over ssh in iTerm2 — `-CC` must
survive):

```
┌───────────────────────┬──────────────────────────────────┐
│ conversation           │ viewport (~50%)                  │
│ (you ↔ concierge;      │ tail -F .casefile/ui/active.log  │
│  action echoes inline) │ channels: state view (default),  │
│                        │ one per model transcript         │
├───────────────────────┴──────────────────────────────────┤
│ status bar: case · models running · turns · spend ·      │
│             mailbox n · lint n                            │
└───────────────────────────────────────────────────────────┘
```

- **No orchestrator pane.** The concierge's actions appear as one-line
  echoes in the conversation pane; its inner monologue is noise (P8).
- Viewport = `tail -F` on a symlink; channel switching = the concierge
  running `ln -sfn <target>.log active.log` (instant; tail follows the
  name). The **state view** channel is a rendered `casefile show` refreshed
  on log change — the live differential re-grading is the product's soul;
  it is the default channel.
- Panes are non-interactive by construction: they run `tail`/render loops
  with no meaningful stdin. Stray keystrokes land harmlessly.
- Conversation pane input: lines starting `@` take the hot path; all else
  the warm path.

## 15. Security

- **Fenced observations** (P11): compiled views render observation bodies
  inside explicit data fences with an instruction that fenced content is
  world-data, never instructions. Threat model: a malicious input file
  quoted in a failing-test message, replayed into every future session via
  resume-context. Build this into the resume compiler from day one.
- **Recheck/check recipes are arbitrary shell**: they run only from the
  repo's own committed log — same trust boundary as the repo's Makefile —
  but the skill must never *author* a check containing data taken from an
  observation body, and `recheck` runs with a timeout and no network by
  default (config to loosen).
- **No secrets in the log**: the log rides in git. Hook adapters must
  redact obvious token/key patterns from observation bodies before append
  (best-effort regex set, config-extendable).
- Lockfile hygiene: stale locks (age > 60s) are broken with a logged note.

## 16. Non-goals (v1)

- No hierarchical task orchestration; no coordinator with a plan (P8 — the
  user explicitly vetoed this).
- No web UI, no server, no daemon. Everything is process-per-invocation
  plus the driver during spitballs.
- No embeddings; FTS only.
- No cross-repo federation of composts (interesting later; out of scope).
- No editing/rebasing of the log, ever.

## 17. Build order

Each milestone ends with the dogfood test: use casefile on casefile.

- **M1 — plumbing v1**: port statelog.py → casefile: case scoping +
  auto-active-case, `open`, `revoke`, `rejected` on decisions, `digest` +
  supersession + evidence-chain invariant, mailbox (`question --to user`),
  lockfile, `--json`, `status`. Extend lint (DIGEST-VIOLATION,
  CONTRADICTION). *Pure code; the v0 file is ~60% of it.*
- **M2 — distillation & memory**: mechanical compactor; rolling abstract
  conventions; dormancy computation + nudge plumbing; FTS index +
  `recall` + `reindex`; `dig`; `recheck`.
- **M3 — vendor integration**: `hooks install claude-code` (hook config +
  secretary sweep + SKILL.md with the full porcelain behavior spec);
  fenced-observation rendering in resume-context; `import`.
- **M4 — deliberation**: adapters (claude resume-mode first; verify codex
  flags); driver with roles, stop conditions, independent-summary diff,
  digest-with-adversarial-review; spend tracking.
- **M5 — UI**: `casefile ui` layout script; symlink viewport + state-view
  renderer; hot/warm path input handling; status bar; `talk` REPL.
- **M6 — stream-json transport**: long-lived sessions; measure
  interjection latency; promote to default only if it beats resume-mode
  meaningfully.

## 18. Testing

- Unit: grading rules (every precedence branch), append validation,
  evidence-chain invariant, lint rules, mechanical compactor transitions,
  dormancy signal clusters, FTS round-trip, budget eviction order in
  resume-context.
- Property: replaying any log prefix yields consistent grades (grades are
  pure functions of the log); reindex is idempotent; digest+dig round-trips
  content.
- **The kill test** (P1): `kill -9` driver/concierge mid-session; restart;
  assert no consequential loss. Run in CI with a scripted fake adapter.
- Adversarial fixtures: observation bodies containing prompt-injection
  strings — assert resume-context fences them; digest attempts that drop a
  constraint — assert append-time rejection + lint.
- A scripted two-fake-model spitball (deterministic adapters) exercising
  dispute → observation → verify → digest → review end-to-end.

## 19. Open questions (decide during build, with David)

1. Final name (casefile vs logbook vs statelog).
2. Threaded disputes (dispute → counter → counter) — v0 is flat; expected
   to be the first casualty of real use. Schema reserves `refs` chains for
   it; decide after first real spitball.
3. Dormancy windows and nudge grace defaults (48h / 7d are guesses).
4. Whether the state-view renderer is `watch`-based polling or inotify.
5. Codex adapter specifics (verify against installed CLI).
6. Echo-volume defaults per entry type.

---
*Companion artifacts: `statelog.py` (v0 plumbing prototype — grading,
lint, resume-context, show are proven and tested), `SCHEMA.md` (v0 schema
rationale; superseded by §5 where they differ), `README.md` (v0 usage).*
