# statelog schema — v0.1

Design goal: a vendor-neutral, append-only record of task state that survives
context windows, supports multi-model deliberation, and keeps epistemic grades
honest. The JSONL log is the source of truth; markdown views are compiled.

## Storage

- `.statelog/log.jsonl` — append-only, one JSON object per line. Never edited
  in place; corrections are new entries referencing old ones.
- `.statelog/meta.json` — task metadata (title, goal, created).
- Both are plain text and belong in git. The log merges cleanly because
  entries are independent lines with unique IDs.

## Entry envelope (all types)

| field    | type   | notes                                             |
|----------|--------|---------------------------------------------------|
| id       | string | 8-char hash, unique within log                    |
| ts       | string | ISO-8601 UTC                                      |
| type     | enum   | see entry types                                   |
| author   | string | `user`, `claude`, `codex`, `system`, ...          |
| body     | string | the claim / decision / observation itself         |
| refs     | [id]   | entries this one is about or depends on           |

## Entry types

- **hypothesis** — a falsifiable claim by a model. Starts at grade `hypothesis`.
- **decision** — a choice that constrains future work. Records `rationale`.
  Decisions authored by `user` are ground truth about intent by definition.
- **observation** — ground truth from the world: test output, log lines,
  command results. Records `source` (e.g. `pytest`, `git`, `manual`).
  Observations are the only entries that can *verify* a hypothesis.
- **constraint** — an invariant that must hold (from user or discovered).
- **question** — an open unknown. Closed by a `resolution` referencing it.
- **endorsement** — author X, having examined hypothesis H (X ≠ H.author),
  supports it. Optional `comment`.
- **dispute** — author X challenges entry E. Records `reason`. An open
  dispute **blocks promotion** of E.
- **resolution** — closes a dispute or question. Records `outcome`
  (`upheld` / `withdrawn` / `answered`) and `reason`.
- **verification** — links a hypothesis to one or more observation entries
  that confirm it. This, not argument, is what earns grade `verified`.
- **note** — anything else worth persisting. Carries no epistemic weight.

## Epistemic grades (computed, never stored)

A hypothesis's grade is **derived from the log at read time** — storing it
would let it drift from the evidence. Rules, in order:

1. `disputed` — has an open (unresolved) dispute.
2. `verified` — has a verification entry referencing ≥1 observation.
3. `consensus` — endorsed by ≥1 author other than its own. Explicitly weaker
   than verified: models can share blind spots. `statelog lint` flags
   load-bearing consensus entries once ground truth could have checked them.
4. `hypothesis` — the default. A claim someone made.

User-authored decisions and constraints are grade `stated` (ground truth
about intent, not about the world).

## Lint rules (drift detection, first cut)

- **laundering**: a hypothesis referenced by ≥N later entries (default 3)
  that is still only `hypothesis` or `consensus` grade.
- **stale-dispute**: dispute open for > N entries with no resolution.
- **orphan-decision**: a decision whose rationale references no entries.
- **contradiction candidates**: verified hypotheses later referenced by a
  dispute (flag for human review; the tool doesn't adjudicate).

## Views

- `statelog show` — compiled markdown: goal, constraints, decisions,
  differential (hypotheses grouped by grade), open questions/disputes,
  recent observations.
- `statelog resume-context` — compact plain-text injection for a fresh
  model instance: goal, constraints, decisions with rationale, current
  differential with grades and *provenance spelled out in words* (so a
  hypothesis arrives sounding like one), open items, last-N observations.
