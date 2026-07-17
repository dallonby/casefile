# casefile

**An append-only, epistemically-graded investigation log for AI-assisted debugging.**

AI coding sessions lose their minds between context windows. Hypotheses get
re-proposed after being ruled out, decisions get relitigated, and "we tested
that yesterday" evaporates. casefile is a tiny, stdlib-only tool that gives
an investigation a durable, structured memory — one that survives context
resets, session crashes, and model swaps.

```
$ casefile resume-context
You are resuming an in-progress task. Trust ground truth over these notes...

TASK: payment-service intermittent 502s
STATUS: leading theory is connection-pool exhaustion (verified against
observation 8f31c2aa); TLS-renegotiation theory ruled out — do not re-propose.
```

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

- **`casefile resume-context`** — one command that tells a fresh session
  (human or model) exactly where the investigation stands: rolling
  abstract, live decisions, ruled-out theories, open questions.
- **`casefile recheck`** — re-runs every recorded check recipe and reports
  *drift*: which claims still hold versus held-three-days-ago. Timeouts
  record `UNKNOWN`, never false failure. `--startup` keeps session start
  fast by skipping known-slow checks.
- **`casefile lint`** — flags epistemic smells: laundering (an unverified
  claim cited like fact), contradictions (verified then disputed), stale
  disputes, orphan decisions.
- **Hooks** — a Stop-hook "secretary sweep" diffs each AI session against
  the log and files what the conversation decided but never recorded; a
  one-line liveness pulse shows what changed since you last looked.
- **`casefile spitball`** — a two-model deliberation driver (proposer vs
  critic) that ferries turns between CLIs (Claude Code, Codex); both
  models file claims and disputes into the same log, and convergence is
  detected from the log itself, not from the transcript.
- **`dig` and `recall`** — full history search (superseded entries
  included) and cross-case compost: "have we seen this before?"

## Install

Python ≥ 3.10, zero dependencies.

```
git clone https://github.com/dallonby/casefile
cd your-project
python3 /path/to/casefile/casefile.py init
python3 casefile.py open "intermittent 502s" --goal "find the cause"
```

`casefile hooks install claude-code` wires the sweep/observe/liveness hooks
into a Claude Code project.

## Dogfooded

casefile is developed using casefile ([SPEC §17](SPEC.md)): its own
`.casefile/log.jsonl` — every hypothesis, wrong turn, deliberation, and
external code review that produced this codebase — ships in this repo as
a live example. `SPEC.md` is the authoritative design document.

## Status

Working core (M1–M6): log + grades, resume-context, recheck, hooks,
import, spitball driver, tmux viewport. Roadmap: config.toml, Codex-side
hooks, mid-turn interjection routing. Expect sharp edges.
