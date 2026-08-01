# Implementation Plan: Replay-Aware Ruleset Decisions

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md) · **Contracts**: [contracts/evaluate-at-time.yaml](./contracts/evaluate-at-time.yaml)
**Phase**: 2 — Plan · **Date**: 2026-08-01
**Next step**: `tasks.md`

---

## Technical Context

| Concern | Choice |
|---|---|
| New backend logic | One new evaluator function, `run_forecast_evaluation_at()`, built on an **existing** InfluxDB query (`query_forecast_snapshot_for_stations`, already used by `foehn.py`) and the **existing** shared decision core (`_evaluate_from_station_data`) |
| API | No new routes. One existing route (`GET /evaluate`) gains one optional query param (`forecast`). Response shape unchanged |
| Persistence | None. No SQLite change, no InfluxDB schema change. `rule_decisions` writes are unaffected — still live-path only |
| Frontend | Vanilla JS in `static/index.html`. No build step. New page-lifetime JS state only (no persistence) |
| Tests | pytest — `SimpleNamespace` duck-typing for the new evaluator function (mirrors `test_rules_evaluator.py`); `FakeInflux` extended with the one new stub method; API test for the new query param and the landing-halo fix |

**Architecture approach**: this is almost entirely composition of things that already exist —
`query_forecast_snapshot_for_stations` (query layer), `_evaluate_from_station_data` (decision core),
`isForecast` (replay engine) — wired together at three seams: one new evaluator function, one new
router query param, one new frontend hook off `onFrame`. The only genuinely new logic is the
frontend's coalescing-queue throttle (research R5) and the `_tnLive` gate on the existing 60 s poll
(research R6).

**Key dependencies**: none new.

---

## Constitution Check

Per `.ai/instructions/00-ai-usage.md`.

| # | Principle | Status | Notes |
|---|---|---|---|
| 1 | Read before acting | ✅ Pass | Every design claim cites file:line; `query_forecast_snapshot_for_stations` was found already existing and already production-used by `foehn.py`, changing the shape of this plan from "build a new query" to "reuse two things and wire a third" |
| 2 | Plan before building | ✅ Pass | This document. No code written |
| 3 | Minimal scope | ✅ Pass | New evaluator function calls the shared core instead of duplicating it a fifth time (research R2) — less code than the "consistent with the other three" alternative, not more |
| 4 | Tool-agnostic instructions | ✅ Pass | Nothing outside `.ai/` |
| 5 | Keep docs in sync | ✅ Pass | Phase 4. `architecture.md`'s "Rules Engine Design" section needs a line for the new function; `features.md` gains a milestone |
| 6 | No secrets committed | ✅ Pass | None involved |
| 7 | Prod is off-limits | ✅ Pass | No migration, no deployment change |

**No blocking violations.**

### Additional constraint compliance (`04-constraints.md`)

| Constraint | How this plan complies |
|---|---|
| No Alembic / no `.sql` | N/A — no schema change at all |
| T09 — no per-station Influx loop | `query_forecast_snapshot_for_stations` already batches all station IDs into one Flux query; `run_forecast_evaluation_at` calls it once, not per-station |
| T12 — error envelope | No new error paths beyond what `evaluate_ruleset` already raises (404/403) |
| T18 — no swallowed exceptions | Frontend refresh failures still go through the existing `catch (err) { console.error(...) }` in `loadRulesetMarkers` — unchanged |
| Static assets → version bump | Phase 3 touches `static/index.html` → **`pyproject.toml` must be bumped** |
| i18n | **No new user-visible strings.** NFR-001's "brief updating state" is implemented as a CSS opacity change on the ruleset marker layer, not new text — sidesteps a 4-locale i18n update for a purely transient visual cue. If a future revision wants text, add i18n keys then |

---

## Data Model Summary

No storage changes anywhere (see [data-model.md](./data-model.md) in full). Three seams:

1. `GET /api/rulesets/{id}/evaluate` gains one optional query param, `forecast: bool = False`.
2. New `run_forecast_evaluation_at(ruleset, influx, valid_time) -> dict` in `rules/evaluator.py`,
   returning the same dict shape `run_evaluation_at` already returns.
3. `evaluate_ruleset`'s three-way branch (live / observed-at_time / forecast-at_time) is extracted
   into one helper, used for both the primary ruleset and any linked landing rulesets — closing FR-004.

---

## File Structure

**Create**

| File | Purpose |
|---|---|
| `tests/backend/test_forecast_evaluation_at.py` | `run_forecast_evaluation_at` via `SimpleNamespace` + `FakeInflux`: matches `run_evaluation_at` shape; no-data fail-safe for a GREEN requirement; `_evaluate_from_station_data` is actually the code path exercised (not a re-duplicated block) |

**Modify**

| File | Change |
|---|---|
| `src/lenticularis/rules/evaluator.py` | Add `run_forecast_evaluation_at()` (research R1, R2, R9) |
| `src/lenticularis/api/routers/rulesets.py` | `evaluate_ruleset` gains `forecast: bool = False` param; extract `_evaluate_at()` helper; landing-links loop uses it (FR-004, research R4) |
| `static/index.html` | `loadRulesetMarkers(atTime?, isForecast?)`; `onFrame` callback schedules a replay-aware refresh (research R5); `_tnLive` gate on the 60 s poll (research R6); `tnGoLive()` calls `loadRulesetMarkers()` immediately on return-to-live (FR-005); brief "updating" opacity cue (NFR-001) |
| `tests/backend/conftest.py` | `FakeInflux.query_forecast_snapshot_for_stations` stub (currently missing — `foehn.py`'s tests must already stub it; verify and reuse the same stub for ruleset tests) |
| `pyproject.toml` | **Version bump** |
| `.ai/context/architecture.md`, `.ai/context/features.md` | Doc sync — new evaluator function, new query param, milestone entry |

---

## Implementation Phases

### Phase 1 — Evaluator: single-timestamp forecast lookup (no API change yet)

1. `run_forecast_evaluation_at(ruleset, influx, valid_time)` in `evaluator.py`, next to
   `run_evaluation_at`. Builds `station_data`/`no_data_stations` from
   `query_forecast_snapshot_for_stations`, then calls `_evaluate_from_station_data` (research R2).
2. Tests (`SimpleNamespace` duck-typing, per `06-testing-conventions.md`):
   - Decision matches what the equivalent hand-built `_evaluate_from_station_data` call would produce
     for the same `station_data`.
   - A station absent from the snapshot lands in `no_data_stations`, and a GREEN requirement on that
     station fails safe to red (FR-002/FR-007 parity with `run_evaluation_at`'s existing no-data test).
   - `opportunity` site type: unmet units → red, matching existing opportunity semantics unchanged.

*Verifiable*: pure unit tests, no router/frontend involved yet.

### Phase 2 — Router: `forecast` param + landing-halo fix

1. Extract `_evaluate_at(rs, influx, at_time, forecast, virtual_members)` from the existing
   `if at_time is not None: ... else: ...` block (`rulesets.py:237-248`).
2. Add `forecast: bool = False` to `evaluate_ruleset`'s signature; route through `_evaluate_at`.
3. Landing-links loop (`rulesets.py:251-269`) calls `_evaluate_at` instead of unconditional
   `run_evaluation` — closes FR-004.
4. Tests:
   - `at_time` + `forecast=true` on a ruleset with no forecast data → same no-data fail-safe shape as
     `at_time`-only today (edge case from spec).
   - A launch site with a landing link, evaluated with `at_time` set → `landing_decisions` reflects
     the **same** `at_time` (previously would've been live — this is the regression test for FR-004).
   - Omitting `forecast` entirely still produces byte-identical behaviour to today's `at_time`-only
     call (backward compatibility check for R3's "no default-value migration needed" claim).

*Verifiable*: API tests via the `client` fixture, `FakeInflux.query_forecast_snapshot_for_stations`
stubbed to return canned per-station forecast rows.

### Phase 3 — Frontend: replay-aware marker refresh

1. `loadRulesetMarkers(atTime, isForecast)` — when args are present, append
   `&at_time=<atTime>&forecast=<isForecast>` to each per-ruleset `/evaluate` fetch. No-arg calls
   (the 60 s live poll) are unchanged.
2. `_rulesetMarkerGen` / `_rulesetRefreshPending` / `_rulesetRefreshBusy` — the coalescing queue
   (research R5). A batch increments the generation counter at start and discards its results at the
   end if superseded.
3. Wire into the `onFrame` callback (`index.html:317-336`): after `applyReplaySnapshot(snapshot)`,
   call the new scheduling function with `(ts, isForecast)`, skipped entirely while `_tnLive` — Play,
   day/hour clicks, and custom-date selection all funnel through this one callback already, so no
   separate wiring is needed per time-nav control (P1/P2/P3 all satisfied by the same hook).
4. Gate the existing `setInterval(loadRulesetMarkers, 60_000)` tick behind `if (_tnLive)` (research R6).
5. `tnGoLive()` calls `loadRulesetMarkers()` once immediately (mirroring its existing immediate
   `loadStations()` call) so returning to Now doesn't wait up to 60 s for markers to catch up (FR-005).
6. NFR-001's "updating" cue: toggle a CSS class on `rulesetLayer`'s marker pane
   (`getPane('rulesetPane')`) to a reduced opacity while `_rulesetRefreshBusy`, restored on completion.
   No new i18n strings.
7. `[Lenti:index]` console logging on every new path, matching the existing convention throughout this
   file (`08-operability.md`).
8. **Bump `pyproject.toml`.**

*Verifiable*: manually in-browser — scrub to yesterday, confirm marker colours match
`/evaluate?at_time=` called directly; scrub to tomorrow, confirm markers match a forecast step from
`/forecast`; press Play, confirm markers change in step with wind arrows without visible request
pileup (watch Network tab: at most one batch of `/evaluate` calls in flight at a time); return to Now,
confirm markers resume live within one poll cycle.

### Phase 4 — Docs sync

`architecture.md`'s "Rules Engine Design" section gains `run_forecast_evaluation_at` alongside the
other public entry points, and a line noting it is the one of the five that calls
`_evaluate_from_station_data` rather than duplicating it. `features.md` gains a milestone entry.
Constitution #5.

---

## Dependencies

- **External**: none.
- **Internal**:
  - `InfluxClient.query_forecast_snapshot_for_stations` (`influx.py:280`, read, not changed) —
    already depended on by `foehn.py`; this feature adds a second caller.
  - `_evaluate_from_station_data` (`evaluator.py:207`, read, not changed).
  - `ReplayEngine`'s `onFrame` signature and `isForecastFrame` (`replay.js`, read, not changed).
- **Not dependent on** `specs/006-thermal-forecast` (unrelated forecast data source, per spec Out of
  Scope) or `specs/005-influxdb3-migration` (query layer this feature calls is InfluxDB-client-version
  agnostic at the level this plan touches it).

---

## Risk & Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **Re-duplicating the decision block a fifth time** instead of calling `_evaluate_from_station_data` — the "consistent with the other three" instinct | High | R2: new function calls the shared core from day one. Test asserts its output for a given `station_data` matches a direct `_evaluate_from_station_data` call |
| 2 | **Server infers observed-vs-forecast from `at_time` vs `now()`** instead of trusting the caller's `forecast` flag — reintroduces the exact boundary-disagreement bug this feature exists to fix | High | R3: `forecast` is an explicit, caller-supplied flag. Test: `at_time` a few minutes in the future with `forecast=false` must still take the observed path (server never second-guesses the caller) |
| 3 | **Landing-halo fix is skipped as "just a query param change"** — FR-004 is easy to miss because it isn't in the same code block as the `forecast` param work | High | Phase 2 explicitly includes the landing-links branch; regression test targets exactly this |
| 4 | **A fixed debounce is implemented instead of the coalescing queue**, degrading either responsiveness (long debounce) or request volume (short debounce) under real backend latency | Medium | R5: single in-flight batch + one pending slot, generation-counter discard. Verified manually against Network tab during Play (Phase 3 step 8) |
| 5 | **The 60 s poll is left unconditional**, silently flipping markers back to live every minute while scrubbed away from Now — the most likely "looks like it works in a quick demo, breaks on a longer session" defect in this feature | High | R6: `if (_tnLive)` gate, called out as its own phase-3 step so it isn't lost inside the `onFrame` wiring work |
| 6 | **Stale response race**: a slower earlier request resolves after a newer one and overwrites markers with the wrong timestamp's decision | High — directly named in NFR-003 | Generation counter checked before any batch touches `rulesetLayer` |
| 7 | Static assets change without a version bump | Medium | Phase 3 step 8 bumps `pyproject.toml` |
| 8 | `architecture.md` continues to say only four evaluation entry points exist | Low | Phase 4 is mandatory, not optional |

## Pre-existing behaviour found during planning — flagged, not fixed here

**`query_forecast_snapshot_for_stations`'s source-selection heuristic** (`influx.py:324-326`) picks
whichever record has the most fields in the ±30 min window, rather than the explicit
swissmeteo-preferred-if-≤24h-old rule `query_forecast_for_stations` uses for the full-series path.
Both are in production use today (the snapshot heuristic via `foehn.py`); this feature adds a second
caller (ruleset forecast-at-time evaluation) without changing the heuristic, per research R8 — fixing
it would be a shared-query-behaviour change affecting föhn too, out of scope here (FR-007/NFR-004).
Worth its own follow-up if the two heuristics ever need to agree.
