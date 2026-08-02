# Resume Notes — 2026-08-03

## In Progress

Two open threads, neither has code written against it yet:

### 1. `specs/006-thermal-forecast` — Phase 1 shipped (v1.22.3), gate passed, Phase 2 not started
Ingestion (collector, InfluxDB measurement, derived metrics) is live in prod. The Phase 1 exit
gate (coverage of local flyable hours, not just row count) was checked against `sdh` over ~9 hours
and **passed** — see `tasks.md`'s gate section for the full readout. Phase 2 (rules engine
integration + station-detail thermal panel) can start whenever picked back up; no re-verification
needed unless the collector's coverage log regresses.

### 2. `specs/009-reactive-ruleset-evaluation` — spec + plan written, nothing else
Event-driven ruleset evaluation (triggered by station data arriving, not a fixed 10-min poll) plus
a forecast-horizon precompute/cache that retires the actual root cause behind the v1.22.6 replay
fix. `spec.md` (all 4 clarifications resolved) and `plan.md` are both written and self-contained.
**No `tasks.md` yet, no code touched.**

## Next Step

Pick whichever thread the user wants first. For #2, the very next action is: write
`specs/009-reactive-ruleset-evaluation/tasks.md` from `plan.md` §5 (file-by-file table), following
the same ordered-task-list format as `specs/006-thermal-forecast/tasks.md`. Do **not** start
implementing until the user explicitly says so — per `.ai/instructions/00-ai-usage.md` planning
mode, a plan/tasks list is not itself the green light to build.

## Open Questions

None outstanding for either spec — all clarification rounds are closed and recorded in each
spec's own `## Clarifications` section.

One small known gap, not blocking: `specs/006-thermal-forecast/tasks.md`'s §10.1 coverage-gate
metric has a documented imprecision (an evening reading undercounts "today" because a fresh
model run's horizon legitimately doesn't cover already-elapsed local hours — a false-alarm-only
artifact, never masks a real gap). Not fixed, not urgent.

## Context — what shipped this session (2026-08-02 → 2026-08-03)

Five prod releases, in order:
- **v1.22.3** — `specs/006-thermal-forecast` Phase 1 (thermal-grid ingestion, backend only)
- **v1.22.4** — fix: unmet GREEN requirement overrode a legitimately matched other group in the
  rules evaluator (real prod bug, reported live: a launch ruleset with direction-arc groups always
  read red for non-ideal directions even when a matching orange arc's own thresholds were met).
  Tag pushed but its own Docker build failed (next bullet) — the fix itself is fully in `main`.
- **v1.22.5** — fix: ARM64 Docker build failure (`pip install --upgrade pip setuptools` before
  Poetry) — pure infra flake, base-image/PyPI drift, unrelated to any app code change.
- **v1.22.6** — fix: `contains(value:, set:)` measured 135× slower (9.3s vs 69ms) than an OR-chain
  of `==` against `weather_forecast`/`weather_forecast_thermal` (per-hour `init_date` tag
  fragments them into huge series counts that `contains()` can't index-skip). This was the actual
  cause of replay markers lagging behind Play's animation — not a rendering bug.
- Two new specs written: `specs/006` gate-checked and documented; `specs/009` spec+plan written
  (see above).

All of the above is already reflected in `.ai/context/features.md`, `.ai/context/architecture.md`,
`README.md`'s milestone table, and `.ai/instructions/01-project-overview.md` /
`06-testing-conventions.md`. This file is a pointer, not a duplicate — read those for detail.
