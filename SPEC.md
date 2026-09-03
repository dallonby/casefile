# CASEFILE — Product Specification v1.1

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

**P1. The log is the epistemic product; operational state is journaled.**
Drivers, concierges, UIs, and indexes may be killed without losing a filed
claim, decision, constraint, or observation. A local atomic run journal may
retain transport state (inputs, replies, vendor session ids, pending calls)
so an interrupted deliberation is diagnosable/replayable; it can never promote
model output to evidence. Test: `kill -9` any process mid-session; the log is
intact and the journal identifies the exact durable/pending boundary.

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
process (driver, concierge) holds no task opinion. It routes and records
transparent operational state; epistemic gates are deterministic functions
of the manifest and log, never the coordinator's prose judgment.

**P9. Human effort is optional everywhere.** Nudges may be ignored; silence
merely lowers a grade (e.g. `inferred-resolved` vs `user-confirmed`), never
blocks progress. Nothing requires ceremony from the user.

**P10. Vendor-neutral by construction.** The interface any model needs is
"can run a CLI and produce text." Vendor-specific integration (hooks, skills)
lives in thin adapters at the edges.

**P11. Model-authored text from the world is untrusted.** Observations can
contain adversarial strings (a malicious file quoted in a test failure).
Compiled views must fence observation bodies as data-not-instructions.

**P12. Coverage precedes convergence.** A debate cannot call itself concluded
while a frozen requirement, criterion, evidence domain, analysis layer, or
alternative×criterion cell is undispositioned. Symmetric coverage is not
agreement; it prevents omission and package caricature.

**P13. Conclusion authority is explicit.** A model recommendation,
cross-model consensus, and user decision are different states. No model or
driver may silently upgrade one into another.

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
| **manifest** | frozen per-run contract: requirements, criteria/weights, alternatives, evidence domains, analysis layers, open questions, coverage grid |
| **run journal** | atomic local record of prompts/replies/call state/vendor ids; operational, never epistemic |
| **candidate digest** | inert model recommendation awaiting review of that exact id |

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
│ .casefile/meta.json, transcripts/{manifest,run,…}       │
└─────────────────────────────────────────────────────────┘
```

Language: **Python ≥3.10, stdlib only** for plumbing and driver (`subprocess`,
`json`, `argparse`, `sqlite3`). Layout scripts may be thin bash. No runtime
dependencies is a feature: the tool must run anywhere a model can shell out.

Repo discovery: git-style — walk upward from cwd for `.casefile/`.
**The investigation log rides in git** (cross-machine continuity). Everything
under `.casefile/` except derived/local state (`index.db`, `transcripts/`,
`log.lock`, `ui/`, `active`, `state/`, `cli`, `journals`) belongs in git —
starter ignore list lives in `.casefile/.gitignore`, **not** a blanket
`.casefile/` entry in the project root `.gitignore`.

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
- `transcripts/<session>/manifest.json` — frozen deliberation coverage
  contract.
- `transcripts/<session>/run.json` — atomically replaced operational journal.
  Each call is persisted `pending` before invocation and `completed` with its
  reply and serializable vendor handle before ferrying.
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
  The active-case pointer is untracked local state; when it is absent (a
  fresh clone of the store) the CLI falls back to the case of the last entry
  in the log, so a cold checkout boots into the right case.

### 5.3 Entry types

| type | purpose | extra fields |
|---|---|---|
| `hypothesis` | falsifiable claim by a model | `check`; like-for-like `supersedes`; claim card: `claim_mode`, `mechanism`, `comparator`, `analysis_layer`, `falsifier`, `counterfactual`, `horizon`, `testability` |
| `decision` | a choice constraining future work | `rationale`, `rejected` (list of `{option, reason}` — the losing alternatives, so they aren't re-proposed); plan revision via `supersedes` + `supersession_reason` |
| `observation` | ground truth from the world | `source`; optional `source_uri`, `source_type`, `published_at`, `accessed_at`, `effective_at`, `expires_at`, `locator`, `jurisdiction` |
| `constraint` | invariant that must hold | `check`; same-author correction via `supersedes` + `supersession_reason` |
| `question` | open unknown; user-authored questions form the **mailbox** | `to` (optional: `user`, `any`) |
| `endorsement` | author X supports another author's entry | `comment` |
| `dispute` | author X challenges entry; **blocks promotion** while open | `reason` in body |
| `resolution` | closes a dispute or question; marks a decision fulfilled | `outcome`: `upheld`/`withdrawn`/`answered`; `fulfilled` (decisions only) |
| `verification` | links hypothesis → observation(s) | refs must include ≥1 observation |
| `digest` | summarizes a span | `supersedes`; `kind`: `mechanical`/`candidate`/`judgment`/`abstract`; candidate is inert; judgment carries explicit `conclusion_class` |
| `revocation` | explicitly retires a constraint or decision | refs the retired entry |
| `note` | anything else; zero epistemic weight | — |

Rules enforced at append time:
- self-endorsement rejected (endorser ≠ target author);
- `verification` requires ≥1 `observation` ref and ≥1 `hypothesis` ref;
- `digest.supersedes` may NOT include: unrevoked constraints, undismissed
  decisions, open disputes/questions, or observations referenced by any
  verification (**the evidence-chain invariant** — see also lint §7).
  A decision is *dismissed* by revocation (retracted), by a
  `resolution` with `outcome: fulfilled` (the work it mandated shipped and
  was observed — `casefile done <id> [--evidence <obs-id|text>]` is the
  sugar; distinct from retraction; the digest that supersedes it must carry
  the residue), **or** by a later decision that `supersedes` it (a plan
  revision: `add -t decision --supersede <id> --rationale "…"`, same author
  or the user overriding anyone; constraints follow the same rule). Revoke
  ≠ fulfil ≠ supersede: the record must not read a completed or revised
  plan as a reversed one. Supersession is recorded on the replacing entry
  only — threads are computed from the refs/supersedes graph, never stored;
- refs must exist and belong to the same case (digests exempted for
  cross-case abstracts only).

Write-time hygiene in `add` (the cheapest moment to fix a filing, while
the context is still in hand):
- **Near-duplicate refusal.** A hypothesis/decision/constraint/question/note
  whose digit-masked token set overlaps a recent (30 d) live entry of the
  same case, type and author class (user vs model) at Jaccard ≥ 0.7 is
  refused with exit **3**, naming the earlier id: cite it (`--ref`),
  replace it (`--supersede`), or file anyway (`--force`). Lower overlap
  (≥ 0.5) is mentioned on stderr, never blocked. Observations, system
  rows, packets and sweep markers are exempt. Candidates come from the
  history FTS index; a stale index falls back to a bounded scan, never a
  rebuild on the write path.
- **Ref harvesting.** 8-hex tokens cited in the body (at least one digit
  and one letter) that resolve to entries of the same case are appended to
  `refs`, so prose citations become graph edges lint can see. Tokens that
  resolve nowhere, or to another case, produce a warning (exit 0) — the
  entry is still filed, the phantom id is caught at the source.
- **Quiet sweep markers.** A sweep note whose body says
  `secretary sweep: nothing …` is recorded as a state stamp
  (`.casefile/state/sweep-stamp.json`), not a log entry (§13).

### 5.4 Epistemic grades (computed per P3)

For a `hypothesis`, first match wins:

1. `refuted` — a dispute against it was resolved with `outcome: upheld`
   (ruled out; appears on the ruled-out list, not the live differential).
2. `disputed` — has ≥1 open dispute (dispute with no resolution).
3. `verified` — referenced by a verification whose refs include ≥1 observation.
4. `consensus` — endorsed by ≥1 author ≠ its own author.
5. `hypothesis` — default.

Other grades: `observation` → `ground-truth`; user-authored
`decision`/`constraint` → `stated`; model-authored `decision`/`constraint` →
`asserted` (rendered as "asserted, not user-confirmed"). A revoked
constraint/decision is grade `revoked` and drops from compiled views (but
its revocation is shown in `dig`). A decision closed by
`resolution --outcome fulfilled` is grade `fulfilled` (work shipped;
digestible under the evidence-chain invariant). A constraint or decision
replaced by a later one is grade `superseded` (precedence: revoked >
superseded > fulfilled > stated/asserted); it leaves the live views and
counts, and `thread`/`dig` show it with what replaced it.

Provenance phrases (P5) for compiled views:

```
stated      → "the user decided"
ground-truth→ "[<source>] observation"
verified    → "verified against ground truth"
consensus   → "cross-model consensus — NOT independently verified"
disputed    → "UNDER ACTIVE DISPUTE"
refuted     → "refuted"
hypothesis  → "an unverified hypothesis"
asserted    → "asserted, not user-confirmed"
fulfilled   → "fulfilled — shipped and observed; digestible"
superseded  → "superseded by a later entry"
```

## 6. Distillation

The log grows unbounded (P2); the **working set** does not. Distillation is
non-destructive: digests hide entries from compiled views; `dig` can always
expand them.

### 6.1 Mechanical compaction (no model judgment)

Runs on hook batches or pre-commit. Targets system-authored machine rows
only — `hook:*`, `recheck:*` and `journal:*` observations; nothing a
person or model filed is ever collapsed:

- keep the latest observation per `source`;
- keep every **transition** (pass→fail, fail→pass, new error signature —
  signature = normalized first line of the body);
- digest steady-state repeats into one line: `"tests green for 47 runs
  over 6h (pytest)"` with `kind: mechanical`, `author: system`. Repeats
  group by (source, signature, outcome) across the whole case, not by
  adjacency — interactive sessions interleave commands, so the same check
  rarely lands back-to-back. The first and latest of each group survive;
  transitions survive because a changed outcome or signature is by
  definition a different group. Journal lines are free text, so their
  signature is the UTC day (first and last line of each day survive). A
  mechanical digest supersedes at most 500 ids so its line stays parseable.

Never touches anything protected by the evidence-chain invariant (§5.3).

### 6.2 Candidate and judgment digests

Triggered at natural checkpoints: end of a spitball session, closure of a
differential branch, or when `resume-context` exceeds its token budget (the
honest trigger — the budget is why distillation exists). A model writes the
digest; because compaction is an epistemic act (P7):

- the proposer writes a `kind: candidate` digest carrying its author and the
  intended span; candidates are inert and hide nothing;
- a **second model reviews that exact candidate id** against the raw
  session-scoped span and frozen manifest — never "newest judgment" and never
  a fallback id — then endorses or disputes it;
- only an independent endorsement mechanically creates a system-authored
  `kind: judgment` with the candidate body preserved verbatim,
  `conclusion_class: cross-model-consensus`, and explicit proposer/reviewer
  refs. Candidate and final judgment also reference the frozen casefile
  requirements they relied on; replacement/revocation marks the conclusion
  stale. An open or upheld dispute leaves the raw span and candidate visible;
- a direct model-authored judgment is labelled `model-recommendation`; only a
  separate user-authored decision referencing a digest yields `user-decision`;
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

The mechanical default (`checkpoint` without a body, `synthesize_abstract`)
prefers recency: the leading theory is the *newest* hypothesis at the best
grade, and the ruled-out / constraint / key-decision lists are the newest
items, rendered as headlines with ids. `checkpoint` never files an abstract
byte-identical to the live one — it reports "abstract unchanged" and exits
0, so a store never accumulates copies. Freshness (`ABSTRACT_STALE_ENTRIES`,
boot exit 30) counts only substantive entries — not `system` rows, hook /
recheck / journal observations, or sweep markers — so machine noise cannot
make an abstract stale by itself.

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
- **CLAIM-CARD**: a ranking-driving hypothesis lacks its mode, comparator,
  layer, falsifier, counterfactual, horizon, testability, or (for a
  causal/mechanistic claim) mechanism.
- **EXPIRED-SOURCE / PROVENANCE**: a live observation passed its review date
  or a remote source class lacks retrieval provenance.
- **STALE-JUDGMENT**: a judgment references a constraint/decision that has
  since been replaced or revoked.
- **UNSWEPT**: a session ended without a secretary sweep — neither a sweep
  marker note nor the quiet-sweep stamp covers its entries (requires
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
`recheck --json` emits the structured report (per-check status/drift, skipped
recipes, held/total/drifted counts); `boot` consumes that contract rather
than scraping the human-facing output.

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

## 10. Recall index (the compost) and history FTS

- SQLite **FTS5** in `index.db` (gitignored, destroyable). Two virtual tables:
  **compost** — abstracts + judgment digests, for `recall` / open-time
  auto-search; **history** — every entry body, for `dig`. Side tables
  record supersession so `dig` can tag hidden rows without parsing the log.
- **The index is a cache; the log is the truth.** `casefile reindex`
  rebuilds both tables from the JSONL. Appends update history incrementally.
  A digest refreshes compost only (a 10^5-entry history rewrite on every
  abstract would make checkpoints unusable). If history row-count drifts
  from the log, `dig` rebuilds then queries; if FTS5 is missing it scans.
- `casefile recall "<query>"` — "have we seen this before?" (compost only:
  the *live* abstract per case plus judgment digests; superseded abstracts
  are `dig` history, not recall hits). Operational how-to ("how did we
  disable X last week") is `dig`, which must not be a linear JSONL
  substring scan on the hot path. `dig` shows one hit per abstract lineage
  (the live one, with a `+N similar, N [superseded]` count) so a case's
  abstract history cannot crowd out its other memory.
- JSONL text search is not an acceptable lookup engine for later model
  sessions. The SQL cache covers raw history, not only abstracts.
- **Open-time auto-search**: when a new case is created, `open` itself
  mechanically surfaces strong compost hits from other cases *before the
  first hypothesis is filed* ("this resembles the March importer case —
  encoding-sniffer theory was ruled out there, evidence attached"); the
  skill adds conversational framing on top. Spitball drivers seed both
  models' opening context with strong matches.
- Embeddings may layer on later; not in scope for v1. Well-written
  abstracts make FTS surprisingly strong — they are dense with searchable
  nouns.

## 11. Interfaces

### 11.1 Plumbing CLI (model-facing; complete; stable)

`init`, `open <title>` (creates or switches case; first mention creates —
no ceremony), `add`, `endorse`, `dispute`, `resolve`, `verify`, `revoke`,
`digest`, `finalize-digest`, `show [entry]` (full entry by id, or compiled
case view), `resume-context [--blind]`, `recheck`, `recall`, `dig
<query>` (history FTS + IDF/type weight, hook-noise demotion — automatic
`system`/`hook:*` entries rank below what people and models filed, digests
first among ties — relevance order, collapse
near-duplicate observations, expand exact ids; then `show <id>` for the
full body; JSONL scan is the stale-cache fallback), `lint`, `log`,
`reindex`, `hooks install <vendor>`, `ui`, `spitball`,
`spitball-recover`, `preflight`, `status` (JSON:
active case, mailbox count, lint count, dormancy candidates, spend, live
and closed decision/constraint counts), `since`, `done <decision>
[--evidence <obs-id|text>]` (resolution `fulfilled` with the evidence
linked), `thread <id|query>` and `where <id|query>`.

`thread` answers "where are we on X" from the log. Seeded by an entry id
or by the best `dig` hits for a query, it walks the refs and supersedes
graph in both directions (bounded depth, default 4; mechanical and
abstract digests are not traversed), prints the chain in time order —
one line per entry: id, type, author, date, computed state, headline,
refs within the thread — and ends with a computed **STATE** footer: the
latest live decision, live constraints, open questions and disputes,
what was ruled out and how (refuted via dispute, revoked, superseded by
what), the last verification and the last observation. `where` prints
only the footer. Threads are computed, never stored (P3): no thread or
topic field exists, so history needs no re-tagging and the answer cannot
drift from the graph.

The CLI is model-facing memory, not a human log browser. `dig` prints
best-first because host UIs truncate tool output; a later model that only
sees the first two lines must still recover the filed fact. `show <id>` is
the verb models already try after seeing an id — it must not argparse-fail.
Do not teach `log | rg` or a sidecar chat transcript as the retrieval path.

Conventions: mutating commands print the new entry id on stdout, exit 0;
all errors to stderr, exit ≠0 (`add` exits **3** for a near-duplicate
refusal, §5.3). `add`/`digest` accept `--body-stdin`, JSON receipts, and
repeatable singular `--ref`/`--reject`/`--supersede` flags so models do not
lose positional bodies to variadic parsing. Exit codes are API.

`resume-context` composition, in fixed priority order with a token budget
(config, default 2000): abstract → constraints → open disputes → decisions
(with rationale + rejected alternatives) → ruled-out list → live
differential (grades in words) → open questions/mailbox → last-N
observations. **Every section is budgeted, none is evicted whole**: each
section is guaranteed a share of the budget, unused share flows to the
next section in priority order, and within a section items are kept
newest first until the share is spent, followed by an "… N more" line.
Entries render as one headline line each (first line of the body, capped)
with the id kept, so `show <id>` reaches the full body; observations stay
fenced. Opens with the P6 sentence. `--blind` omits the differential and
ruled-out list — used for independent replication when the recorded
differential itself may be the problem (fresh model forms its own theory;
diff against the record).

`boot` applies the same budget to BRIEF + SINCE + DO NOT together (the
structural WHERE / YOU ARE / WORLD vs LOG / NEXT / CARD sections are short
and unbudgeted), so the default briefing stays a few thousand tokens on a
store of any size. BRIEF holds the abstract, then constraints, recent
decisions, differential, questions, disputes and mailbox — each newest
first. SINCE is the "since your last session" delta: substantive entries
filed after this author's last entry in the case, derived from the log so
it is correct across hosts (the per-session pulse cursor is seeded at the
log tip when a session starts, so it cannot answer this). DO NOT lists
refuted hypotheses and the rejected alternatives of decisions filed in the
last 14 days; older ones are counted, not printed. WHERE reports the
substantive entry count and when each author last filed. `casefile since
[-a author] [--limit N] [--json]` prints the same delta standalone.

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

`casefile spitball --topic "…" [--models claude,codex] [--manifest …]` — a
kill-safe (P1, P8) turn-ferrying loop:

1. Freeze `manifest.json`: verbatim requirements with confirmed/inferred
   provenance, criteria and optional weights, evidence domains, alternatives,
   analysis layers, open questions, and every alternative×criterion cell.
   `enforce` refuses an incomplete manifest before paid calls; `warn` permits
   exploration but blocks judgment; `off` is explicit.
2. Run cheap adapter preflight (binary/version/root/writeability/hook-isolation
   declaration), then require the opening model turn to execute
   `casefile preflight` and create a nonce-bound receipt inside the session
   transcript directory. Open one headless session per model with
   `resume-context`, role brief, recall, and manifest.
3. Before every vendor call, atomically journal its input as `pending`; after
   return, journal reply, cost, and vendor session id as `completed` before
   transcript append or ferrying. Reject/retry empty, progress-only,
   receipt-only, malformed-envelope, or unknown-coverage output.
4. Ferry turns: model A's validated visible message → model B, and vice versa. Each
   model keeps its **own continuous session** (its private view of the
   argument) — never a shared scraped transcript. Full replies stay in the
   journal; repetitive filing receipts/protocol lines are reduced to one
   structured turn delta before transport.
5. Every turn: append transcript file; tee to viewport; models file entries
   via the CLI as they argue (the skill/role brief mandates it).
6. Each model independently writes a verbose round-by-round synopsis:
   arguments, evidence, assumptions, concessions, falsifiers, open points,
   and manifest coverage. Its structured envelope must enumerate the exact
   opening/round ledger. Full independent synopses are echoed and retained;
   a bounded reconciliation pass runs on divergence.
7. Both models perform secretary sweeps. Judgment is blocked on an
   unconverged outcome, structural manifest gaps, uncovered rows, summary
   divergence, open questions/disputes, incomplete claim cards, or blocking
   provenance/contradiction lint.
8. If every gate passes, candidate→exact critic review→mechanical judgment
   follows §6.2.

Roles: default proposer/critic; models are systematically better critics of
theories they didn't generate, so each attacks the *other's* leading
hypothesis. Role briefs are prompt files in the repo (`.casefile/roles/`),
user-editable.

Stop conditions (agreeable models chat forever): **converge** (no open
disputes; leading hypothesis endorsed or verified) → attempt guarded
finalization; **turn budget** or **spend budget** → halt with the differential as-is;
**stalemate** → halt; an open dispute is a valid, valuable output.

`casefile spitball-recover <session>` replays each model's private completed
calls and explicit pending boundary from `run.json` into fresh vendor sessions.
Within a live run, adapters use long-lived streams or vendor session resume.
tmux is an optional viewport only; it is never the continuity mechanism.

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
  Hook commands are guarded (`test -f <script> || exit 0; exec python3
  <script>`): the wiring is tracked but the store is not, so a clone without
  `.casefile/` must no-op silently (P9) while a real run preserves the
  script's own exit status (the Stop gate blocks by design). Reinstall keys
  on the script path and upgrades legacy wiring in place — never a
  duplicate entry that fires twice.
- **External journal sync**: agents often keep their own structured logs
  (operations journals, run diaries) and file to them more reliably than to
  the case. `sync-journal` mechanically ingests new lines from journals
  listed in `.casefile/journals` (local-only config, one absolute path per
  line) as observations with `source: journal:<name>`, secret-redacted. A
  journal seen for the first time registers at EOF — only lines written
  after configuration sync, never a historical flood. Rides hook batches
  like compaction.
- **Stop hook → secretary sweep**: on session end, prompt the session to
  diff its conversation against the log — "anything decided, constrained,
  or ruled out here that isn't recorded?" — and file the gaps. Closes the
  biggest leak: the decision made conversationally in window 4 that nobody
  wrote down. A sweep that filed gaps ends with a `note` marker
  (`secretary sweep: <gaps filed>`); a sweep that found nothing says
  `secretary sweep: nothing unrecorded`, which `add` records as the
  quiet-sweep stamp (`.casefile/state/sweep-stamp.json`: time, author and
  the id it was filed after) instead of a log entry — so the log holds
  memory, not hundreds of identical "nothing" notes. Lint's UNSWEPT rule
  and the hook key off the newer of marker and stamp. The hook only
  prompts when the log tail shows something to sweep since the last
  sweep — a non-system decision/constraint/hypothesis/question/verification,
  twenty non-system observations, or any non-system entry once the sweep
  is over thirty minutes old (constants at the top of the installed
  `sweep.py`); a quiet turn ends silently, with no marker required.
  Automatic `system`/`hook:*` entries never trigger a sweep on their own.
- **The skill file** (`SKILL.md` dropped where Claude Code discovers it),
  teaching every session in the repo: read resume-context (and run
  `recheck`) on start; address the mailbox; file hypotheses/decisions as it
  works with correct types and authors; echo-back conventions and confirm
  rules; recognize casefile-directed speech (§11.2 table); propose case
  opening (§11.3); propose escalation to spitball when the differential
  stalls (two theories, no discriminating evidence, ~3 windows without
  progress); never edit the log by hand. A command cheatsheet generated from
  the live argparse tree is appended at install time, so flag signatures in
  the skill can never drift from the CLI and agents stop burning turns on
  per-command `--help`.

**Codex-side integration** (verified live against codex-cli 0.144.5, obs
8c7a9b86): `casefile hooks install codex` appends a marker-delimited
`[hooks]` block to `$CODEX_HOME/config.toml` — Codex reads hook
definitions only from the global config (PascalCase events, Claude-style
groups; no project-level config exists), so each command is a dispatcher
that no-ops unless the cwd contains `.casefile/hooks/<script>`. The stdin
payload and output contract are Claude Code-compatible (`session_id`,
`hook_event_name`, `stop_hook_active`, `tool_name` "Bash", `decision:
block`, `systemMessage`), so the same three hook scripts serve both
vendors; the sweep hook takes the filing author as `argv[1]` (`codex`).
Grok Build loads the same Claude-compatible settings and scripts but
sends a **camelCase** envelope (`stopHookActive`, `sessionId`,
`toolName`=`run_terminal_command`, `toolInput`, `toolResult`). Hooks
must accept both snake_case and camelCase keys; ignoring camelCase
re-blocks the Stop gate forever (up to the vendor continuation cap)
because the re-fire never looks "active".
For author attribution, an explicit hook argv or `CASEFILE_AUTHOR` wins;
otherwise the sweep recognizes Grok's reserved, runner-injected
`GROK_HOOK_EVENT` + `GROK_SESSION_ID` pair before defaulting to Claude.
Conventions land in a managed `AGENTS.md` section (Codex's project
instructions file), pointing at the shared skill. Hook trust is per-hook,
hash-based, and granted once interactively via `/hooks`; headless runs use
`--dangerously-bypass-hook-trust`.

**`casefile init` is the single onboarding step**: it creates `.casefile`,
opens a default case named after the project directory (no title/goal
ceremony), and installs hooks for all supported vendors. Idempotent.

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
- **The kill test** (P1): `kill -9` driver mid-call; assert the append-only
  log remains parseable, `run.json` identifies the exact pending input, and
  `spitball-recover` replays the private context into a new run.
- Adversarial fixtures: observation bodies containing prompt-injection
  strings — assert resume-context fences them; digest attempts that drop a
  constraint — assert append-time rejection + lint.
- Deterministic adapter tests for hook-receipt output replacement, progress
  preambles, malformed/unknown coverage envelopes, cache-token telemetry,
  manifest symmetry grids, incomplete-manifest refusal, and transcript/run
  journaling.
- A scripted two-model spitball exercising convergence → complete claim card
  → candidate digest → exact foreign endorsement → mechanical system judgment;
  dispute/missing review paths must leave the candidate inert.

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
