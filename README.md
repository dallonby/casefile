# casefile

**An append-only, epistemically-graded record for AI-assisted investigations
and deliberations.**

AI coding sessions lose their minds between context windows. Hypotheses get
re-proposed after being ruled out, decisions get relitigated, and "we tested
that yesterday" evaporates. casefile is a tiny, stdlib-only tool that gives
an investigation a durable, structured memory — one that survives context
resets, session crashes, and model swaps.

<p align="center">
  <img
    src="demo/casefile-continuity.gif"
    alt="Codex investigates a free-shipping bug and files findings to casefile; after a context reset, Grok boots and already knows the verified root cause"
    width="860"
  />
  <br />
  <sub>
    Real agents, same repo: <b>codex</b> files a verified root cause → context reset →
    <b>grok</b> already knows via <code>casefile boot</code> (user never types casefile).
    <a href="demo/"><code>demo/</code></a> ·
    <a href="demo/casefile-continuity.cast">cast</a>
  </sub>
</p>

## Installation

casefile requires **Git** and **Python 3.10 or newer**. Core is stdlib-only.
Optional shared **Postgres** multi-writer needs `psycopg2`; `casefile init`
and `casefile upgrade` install `psycopg2-binary` automatically when missing.
Clone the CLI once somewhere permanent, then run `init` from every project
that should keep a casefile.

### Identity (no `export` required)

Put the agent/human id in the project `.env` (loaded automatically):

```bash
CASEFILE_AUTHOR=codex
```

Or a one-line gitignored `.casefile/author`. Process env still wins if set.
`-a` on write commands remains available as an override.

### Persistence (local default, optional Postgres)

Default is **local** `.casefile/log.jsonl` (rides in git). For multi-user shared
history on a private network:

**Interactive (recommended):**

```bash
cd /path/to/project-with-.casefile
casefile persistence enable
# prompts for URL, validates format + connection, writes .env, reconciles
```

Or non-interactive:

```bash
casefile persistence enable \
  --url 'postgres://USER:PASSWORD@db.example.internal/casefile'
```

Use a dedicated **`casefile`** database (not an application DB). URL shape:
`postgres://USER:PASSWORD@HOST[:PORT]/DATABASE` (or `postgresql://…`).
The command prints format hints on bad input.

Namespace defaults to the **store folder name** (e.g. `my-project`).
Override with `CASEFILE_PG_NAMESPACE` only if needed.

First enable runs a reconcile (local → PG, dedupe by id). Local JSONL stays
mirrored for git/offline.

```bash
casefile persistence              # status
casefile persistence reconcile    # sync again
casefile persistence disable      # back to local-only (keeps URL in .env)
```

### macOS

In Terminal:

```bash
mkdir -p "$HOME/.local/share"
git clone https://github.com/dallonby/casefile.git \
  "$HOME/.local/share/casefile"
cd /path/to/your-project
python3 "$HOME/.local/share/casefile/casefile.py" init
```

### Linux

In a shell:

```bash
mkdir -p "$HOME/.local/share"
git clone https://github.com/dallonby/casefile.git \
  "$HOME/.local/share/casefile"
cd /path/to/your-project
python3 "$HOME/.local/share/casefile/casefile.py" init
```

### Windows

Use **WSL** for the complete CLI and hook integration. If WSL is not
installed, run `wsl --install` once from an Administrator PowerShell,
restart if prompted, then run these commands inside the WSL terminal:

```bash
sudo apt update
sudo apt install -y git python3
mkdir -p "$HOME/.local/share"
git clone https://github.com/dallonby/casefile.git \
  "$HOME/.local/share/casefile"
cd /mnt/c/path/to/your-project
python3 "$HOME/.local/share/casefile/casefile.py" init
```

Native PowerShell can run the core Python CLI, but WSL is currently required
for the generated Claude Code, Codex, and Grok-compatible shell hooks.

`init` creates `.casefile/` (log + meta **tracked in git** for cross-machine
continuity; only derived state is ignored via `.casefile/.gitignore`), opens
a default case, installs agent instructions and hooks, and creates a
`casefile` launcher in `~/.local/bin`. Restart the agent after initialization
so it loads the new hooks. Codex asks you to trust them through `/hooks`;
Grok uses `/hooks-trust`.

**Core vs spitball.** The log half (`boot`, grades, recheck, recall, lint,
packet) is durable and vendor-neutral. **Spitball** (multi-model
deliberation driver) is an optional companion module in `spitball.py` —
vendor CLI transports (Claude, Codex, Grok) that break on CLI upgrades.
Core does not import spitball except when you invoke `spitball` /
`spitball-recover` / `talk`.

## What a boot looks like

```
$ casefile boot
=== WHERE ===
store: /path/to/project
active case: payment-service — intermittent 502s
...
=== YOU ARE ===
author: claude (from env)
...
=== BRIEF ===
rolling abstract:
PROBLEM: payment-service — find the cause
STATUS: leading theory is connection-pool exhaustion (verified against ground truth)
constraints (newest first):
- `2f9c1e4a` (the user decided) do not restart the load balancer mid-flight
...
=== SINCE ===
since your last entry `a1b2c3d4` (2026-08-30T09:12:00+00:00, 2 d ago): 3 substantive of 41 new entries
- `e5f6a7b8` decision (codex) switch the pool to lazy reconnect
...
=== NEXT ===
1. casefile packet --to codex -a claude
```

The whole briefing is budgeted (`--budget`, default ~2000 tokens): every
section keeps its newest items as one headline line per entry with the id
and prints "… N more" instead of vanishing, so a 20,000-entry store boots
in a few thousand tokens and `casefile show <id>` reaches any full body.
SINCE is derived from the log (entries after your own last one), so it is
right across machines.

## How it works

Every entry in the log is **typed** and **attributed**:

| type | what it records |
|---|---|
| `hypothesis` | a falsifiable claim — optionally with a `--check` shell recipe |
| `observation` | ground truth: test output, command results, log lines |
| `decision` | a choice made, with rationale and rejected alternatives |
| `constraint` | a boundary ("don't touch the sniffer") |
| `question` | something only a human can answer (routed to a mailbox) |
| `dispute` / `verify` / `endorse` | how claims get contested and settled |

Grades are **computed, never stored**: a hypothesis linked to a real
observation is `verified`; one that models merely agree on is only
`consensus` — model agreement is never verification. The log is
append-only; corrections are new entries, so the epistemic history is
tamper-evident by construction.

## The parts

- **`casefile boot`** — single cold-start ritual for any model: store
  discovery (`CASEFILE_ROOT` / walk-up / `.casefile-pointer`), author
  identity (`CASEFILE_AUTHOR`), startup recheck, and a structured brief
  (WHERE / YOU ARE / WORLD vs LOG / BRIEF / SINCE / DO NOT / NEXT / CARD),
  budgeted as a whole and newest-first in every section. Exit codes for
  orchestrators: 0 ok, 10 mailbox, 20 drift, 30 abstract stale.
  `casefile since` prints the SINCE delta on its own.
- **`casefile packet` / `inbox` / `next`** — log-only multi-agent handoff.
  One author emits a peer packet; the peer lists inbox items and concrete
  next CLI actions without a shared chat transcript.
- **`casefile checkpoint`** — refresh the rolling abstract and rebuild the
  FTS compost index so `recall` works after context resets.
- **`casefile resume-context`** — compact briefing (also embedded in boot).
  Sections are budgeted individually and keep their newest items; nothing
  is evicted whole, so a fresh agent always sees the latest constraints,
  decisions and open questions.
- **`casefile recheck`** — re-runs every recorded check recipe and reports
  *drift*: which claims still hold versus held-three-days-ago. Timeouts
  record `UNKNOWN`, never false failure. `--startup` keeps session start
  fast by skipping known-slow checks.
- **`casefile lint`** — flags epistemic smells: laundering (an unverified
  claim cited like fact), contradictions (verified then disputed), stale
  disputes, orphan decisions, expired sources, and incomplete claim cards
  once a claim becomes ranking-driving.
- **Hooks** — a Stop-hook "secretary sweep" diffs each AI session against
  the log and files what the conversation decided but never recorded (it
  only asks when something worth sweeping was filed since the last sweep;
  quiet turns end silently, and a "nothing unrecorded" sweep is recorded as
  a state stamp rather than a log entry); a one-line liveness pulse shows
  what changed since you last looked. Mechanical compaction collapses
  steady-state hook, recheck and journal rows into digests (`dig` still
  expands them); nothing a person or model filed is ever touched.
- **`casefile spitball`** — a two-model deliberation driver (proposer vs
  critic) that ferries turns between live CLIs (**Claude Code, Codex, Grok**);
  both models file claims and disputes into the same log, and convergence is
  detected from the log itself, not from the transcript. A frozen manifest
  makes requirements, criteria, evidence domains, alternatives, and the
  alternative×criterion symmetry grid explicit. Every input/reply and vendor
  session id is written atomically to `run.json` before it is ferried.
  Receipt-only/progress-only output is rejected and retried.
  Token telemetry separates uncached input/output from cache reads so a long
  resumed context does not masquerade as fresh token spend.
  Transcript, manifest, and journal files are local/gitignored and created
  private to the user because debates may contain proprietary code or strategy.
  Complete independent round-by-round synopses (and any reconciled variants)
  are echoed at completion as well as retained in the private journal.
  Raw filing receipts remain auditable there, while model-to-model transport
  compacts them into one structured turn delta to avoid context churn.
- **Guarded conclusions** — the proposer can only create an inert
  `candidate` digest. The critic reviews that exact id; only a foreign
  endorsement mechanically promotes it to a system-authored judgment.
  Candidate/final digests reference the frozen casefile requirements they
  relied on, so replacing or revoking one marks the old judgment stale.
  Recommendations, cross-model consensus, stale conclusions, and user
  decisions stay distinct.
- **`casefile spitball-recover <session>`** — reconstructs each model's private
  conversational view from an interrupted run journal and continues in fresh
  vendor sessions. Within a live run, adapters use continuous stream/session
  resume; tmux is only an optional viewport, never the memory mechanism.
- **`dig` and `recall`** — full history search (superseded entries
  included) and cross-case compost: "have we seen this before?"

For a consequential debate, freeze the contract before the first argument:

```bash
casefile spitball \
  --topic "choose the production design" \
  --models codex,grok \
  --requirement "preserve correctness under reorgs" \
  --criterion "measured failure rate" \
  --criterion "p99 latency" \
  --weighting "failure rate 2x latency" \
  --alternative "optimistic cache" \
  --alternative "canonical reads" \
  --evidence-domain "replay benchmarks" \
  --manifest-mode enforce
```

Use a JSON `--manifest` when criteria need explicit weights or
confirmed/inferred provenance. `warn` mode still runs an exploratory debate
but blocks judgment while the manifest is incomplete; `off` is an explicit
escape hatch.

## High-integrity filing

Multiline model output no longer has to fight variadic shell flags:

```bash
printf '%s\n' "$BODY" | casefile add -t hypothesis -a codex \
  --body-stdin --claim-mode causal-inference \
  --mechanism "…" --comparator "…" --analysis-layer "transaction execution" \
  --falsifier "…" --counterfactual "…" --horizon "30d" \
  --testability within-session --json

casefile add -t observation -a codex "measured inclusion latency" \
  --source benchmark --source-type test --locator "run 184 / p99" \
  --accessed-at 2026-07-26T12:00:00Z --expires-at 2026-08-02T12:00:00Z
```

`add` also does its hygiene at write time, while the context is still in
hand: a hypothesis/decision/constraint/question that near-duplicates a
recent entry of the same type by the same author class is refused (exit 3)
with the earlier id — cite it with `--ref`, replace it with `--supersede`,
or file anyway with `--force`; and 8-hex ids cited in the body are
harvested into `refs` automatically, with a warning for ids that do not
exist (the entry is still filed).

Singular `--ref`, `--reject`, and `--supersede` flags are repeatable and avoid
the positional swallowing ambiguity of their legacy variadic counterparts.
Constraints can be corrected by the same authority with `--supersede` plus a
reason; decisions still use explicit revoke/replace semantics.

## Upgrade and maintenance

Run this later from any initialized project:

```bash
casefile upgrade
```

**`casefile upgrade`** (run from a project with `.casefile/`, or set
`CASEFILE_ROOT`) is the cross-machine / launcher command:

1. `git pull --ff-only` of the casefile checkout (optional `--no-pull`)
2. Install/refresh a `casefile` launcher on PATH (`--bin-dir`, `$CASEFILE_BIN_DIR`)
3. Rewrite project `SKILL.md`, hooks, and `AGENTS.md` from **this** CLI

Put `casefile upgrade` in agent session launch so porcelain never drifts.

Open named cases: `casefile open "intermittent 502s" --goal "find the cause"`.

## Dogfooded

casefile is developed using casefile ([SPEC §17](SPEC.md)): every
hypothesis, wrong turn, two-model deliberation, and external code review
that produced this codebase went through its own log. `SPEC.md` is the
authoritative design document.

## Status

Working **core** (log + grades, boot, whoami, packet/inbox/next, checkpoint,
recall, recheck, lint, hooks, import). Optional **spitball** companion
(multi-model deliberation over Claude/Codex/Grok CLIs). Roadmap: config.toml,
stronger verification binding. Expect sharp edges.

## License

[MIT](LICENSE).
