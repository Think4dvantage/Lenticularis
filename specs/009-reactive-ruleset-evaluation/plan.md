# Implementation Plan: Reactive Ruleset Evaluation

**Phase**: 2 — Plan · **Date**: 2026-08-03
**Spec**: [spec.md](./spec.md) · **Target version**: v1.22.6 → **v1.23.0** (behavioural change, not a
patch — see §9)
**Audience**: written to be implemented by an agent that has not seen this planning session.
Read `.ai/instructions/` first anyway.

---

## 1. Why this exists

Today, `CollectorScheduler._run_ruleset_evaluator` (`scheduler.py:485`) re-evaluates **every**
ruleset with conditions on a fixed `IntervalTrigger(minutes=10)`. Two problems, both raised live
by the owner while debugging the v1.22.4-v1.22.6 incidents:

1. Holfuy delivers new station data every 5 minutes; a decision can be up to 10 minutes stale
   even though fresher data already sits in InfluxDB.
2. Forecast decisions are **never** cached — `run_forecast_evaluation_at` re-queries InfluxDB
   live on every single API call, including once per replay frame during Play (~600ms cadence).
   v1.22.6 fixed that query from 9.3s to 69ms, but the architecture is still "recompute on every
   read." The owner's own words: *"if i look at a ruleset and its future in forecast I already
   see the results of that evaluation — it shouldn't need to evaluate live."*

This plan replaces the fixed-interval poll with event-driven evaluation triggered by the
collector/forecast-collector run-completion signal that already exists (`on_collector_run` /
`on_forecast_run`), and adds a precomputed forecast-horizon cache so replay reads instead of
recomputes.

---

## 2. Verified current architecture (read before changing anything)

### 2.1 The hook mechanism already exists — it's single-slot, not multi-listener

`main.py:177-180`:
```python
scheduler.on_collector_run = _make_registry_updater(...)
scheduler.on_forecast_run  = _make_forecast_rewarmer(influx, app.state.display_registry)
```
Each is a **single callable assignment**, not a list. `scheduler.py` calls them at the end of
`_run_collector` (`on_collector_run`, after an observation collector finishes) and
`_run_forecast_collector` (`on_forecast_run`, after a per-station forecast collector finishes —
**not** the grid collector, and **not** the thermal collector, neither of which invoke this hook).

⚠️ **This plan must compose, not replace, the existing hooks.** `_make_registry_updater` already
does real work (rebuilds `display_registry`/`virtual_members`). The new reactive-evaluator logic
must be wired as an *additional* step inside a combined callback, assigned once in `main.py`'s
lifespan — never a second `scheduler.on_collector_run = ...` line, which would silently discard
the registry updater.

### 2.2 Station → ruleset is not indexed today, but is trivial to query

`RuleCondition` (`database/models.py:281-`): `ruleset_id` (indexed FK), `group_id`, `station_id`
(plain `String`, **not indexed**), `station_b_id`. A reverse lookup is a single query:
```sql
SELECT DISTINCT ruleset_id FROM rule_conditions
WHERE station_id IN (:ids) OR station_b_id IN (:ids)
```
No new table needed. `station_id` has no index — acceptable at today's scale (a handful of
pilots, dozens of rulesets); add `Index("ix_rule_conditions_station_id", "station_id")` only if
this is ever measured to matter. Don't build it pre-emptively (`04-constraints.md`).

### 2.3 `RuleCondition.station_id` is always a canonical/display id, never a hidden member id

`GET /api/stations` (the ruleset-editor's station picker, `ruleset-editor.html:743`) reads
`_get_display_registry` (`stations.py:275-276`), the **deduplicated** registry. A pilot can only
ever pick a canonical station id when building a condition. Consequence for FR-003: when a
physical member station updates, the ruleset that references its **canonical** id must still be
found. `app.state.virtual_members` is `{canonical_id: [member_ids, canonical first]}`
(`services/dedup.py:99`) — invert it once per event to map `member_id → canonical_id`.

### 2.4 The exact precedent for forecast-horizon storage already exists

`rules/evaluator.py:937` `write_decisions_batch(ruleset, results, influx)` already writes a
**batch** of `(timestamp_iso, decision, condition_results)` tuples to `rule_decisions`, one point
per timestamp, tagged `ruleset_id`/`owner_id`/`site_type` — built for `run_history_backfill`
(`evaluator.py:836`), which computes a whole time range in one query and stores it wholesale.
**This is the same shape §5 needs for the forecast horizon** — do not invent a second pattern.

`database/influx.py:699` `query_decision_history(ruleset_id, hours)` and `:779`
`query_decision_history_multi(ruleset_ids, hours)` are the read-side precedent: plain
`r.ruleset_id == "..."` / OR-chain-of-`==` filters — **never** `contains()` (the exact class of
bug fixed in v1.22.6). Mirror this, not the old `query_forecast_snapshot_for_stations` style.

### 2.5 Why forecast data must NOT share `rule_decisions` with live history

`rule_decisions` powers the org-dashboard/ruleset-analysis **observed** history strip. A
forecast horizon recomputed wholesale on every model run would, at the same `_time` a live
decision is later recorded for that same hour, collide in exactly the way `weather_forecast` vs
`weather_forecast_thermal` was kept separate in `specs/006` §3.1 — two series at one timestamp,
ambiguous which one the history strip should read. **Decision: new measurement,
`rule_decisions_forecast`**, keyed the same way but distinct.

---

## 3. Architecture

```
                    ┌───────────────────────────────────────────┐
                    │  Observation collector run completes       │
                    │  (on_collector_run hook, existing)          │
                    └───────────────────┬─────────────────────────┘
                                        │ station_ids = {s.station_id for s in collector.get_stations()}
                    ┌───────────────────▼─────────────────────────┐
                    │  _reactive_live_evaluator(station_ids)       │  NEW
                    │  · expand via virtual_members (member→canon) │
                    │  · SELECT DISTINCT ruleset_id WHERE station  │
                    │    IN (canonical_ids)                        │
                    │  · per-ruleset in-flight guard (FR-007)      │
                    └───────────────────┬─────────────────────────┘
                                        │ for each affected ruleset (existing, unchanged):
                                        ▼
                              run_evaluation() → write_decision() → _maybe_notify()

                    ┌───────────────────────────────────────────┐
                    │  Forecast collector run completes           │
                    │  (on_forecast_run hook, existing)            │
                    └───────────────────┬─────────────────────────┘
                                        │ station_ids = the collector's station set
                    ┌───────────────────▼─────────────────────────┐
                    │  _reactive_forecast_evaluator(station_ids)   │  NEW
                    │  · same reverse-lookup as above              │
                    └───────────────────┬─────────────────────────┘
                                        │ for each affected ruleset:
                                        ▼
              run_forecast_evaluation(ruleset, influx, horizon_hours=120)   (existing, unchanged)
                                        │ → list[[valid_time, decision, condition_results]]
                                        ▼
              write_decisions_batch(ruleset, steps, influx, measurement="rule_decisions_forecast")
                                        │
                    ┌───────────────────▼─────────────────────────┐
                    │  GET /api/rulesets/{id}/evaluate?at_time&    │  MODIFIED
                    │  forecast=true                                │
                    │  · read query_forecast_decisions_for_ruleset  │
                    │  · cache miss → fall back to today's live     │
                    │    run_forecast_evaluation_at (unchanged)     │
                    └───────────────────────────────────────────────┘
```

`_run_ruleset_evaluator` and its `IntervalTrigger(minutes=10)` job are **removed** (FR-005). One
evaluation pass over every ruleset with conditions still runs at startup (FR-008) — same
function, called once from the lifespan instead of on a recurring trigger.

---

## 4. Data model

### 4.1 New InfluxDB measurement: `rule_decisions_forecast`

Mirrors `rule_decisions` exactly, so `write_decisions_batch` needs only a `measurement: str =
"rule_decisions"` parameter added (defaulting to the existing behaviour — the history-backfill
caller passes nothing and is unaffected):

| | |
|---|---|
| **Tags** | `ruleset_id`, `owner_id`, `site_type` (identical to `rule_decisions`) |
| **Time** | `valid_time` (the forecast hour this decision applies to — **not** "when computed") |
| **Fields** | `decision` (str), `condition_results` (JSON string, same as today) |

No `init_time` field is stored here deliberately — a full-horizon rewrite replaces the *entire*
existing set of points for that ruleset's future window each time (see §4.2), so which model run
produced a given point is not something a reader needs to disambiguate; the freshest write always
wins by construction.

### 4.2 Overwrite semantics — a full-horizon rewrite, not an append

Unlike `rule_decisions` (append-only observed history), `rule_decisions_forecast` represents "our
current best forecast decision for each future hour" — it must be **replaced wholesale** on every
forecast update, not appended to, or stale points from a superseded model run would linger
alongside fresh ones at *different* future valid_times that the new run didn't happen to
recompute (impossible here since `run_forecast_evaluation` always computes the full requested
horizon, but worth stating as an invariant). Simplest correct approach, consistent with
`weather_forecast`'s own "old model runs age out" pattern: **delete-then-write** is not available
via the InfluxDB 2.x client used here without a separate delete API call; instead, rely on the
fact that every point's `_time` is the same for the same `valid_time` across model runs, so a
newer write with the same tags+time simply **overwrites the old field values in place** — this is
exactly how `weather_forecast`'s own hourly writes already behave when the same station reports
the same valid_time again. No new delete logic is needed; **just always write the full horizon
every time**, and let InfluxDB's last-write-wins-per-series-point behaviour do the rest.

⚠️ One real gap this leaves: if a *later* model run's horizon is **shorter** than an earlier one's
(e.g. lsmfapi's h+8–h+33 null hole from `specs/006` §3.2 — a frame that used to have data now
doesn't), the old points for those now-missing hours are never overwritten and would linger,
stale. Acceptable for v1 (mirrors how `weather_forecast` already has this exact gap — see
`specs/006` §3.2's own null-hole discussion) — not solved here, flagged as a known limitation.

### 4.3 New Influx methods — `database/influx.py`

```python
def write_decisions_batch(  # EXTENDED — evaluator.py, one new optional param
    ruleset, results, influx, measurement: str = "rule_decisions",
) -> None: ...

def query_forecast_decisions_for_ruleset(
    self, ruleset_id: str, start: datetime, end: datetime,
) -> list[dict]:
    """Mirrors query_decision_history, targeting rule_decisions_forecast.
    Plain r.ruleset_id == "..." filter — never contains()."""
```

### 4.4 No SQLite schema change

The reverse lookup (§2.2) is a plain `SELECT DISTINCT ruleset_id FROM rule_conditions WHERE ...`
against the existing table. No new table, no new column, no Alembic (`04-constraints.md`).

---

## 5. File-by-file changes

| # | File | Change |
|---|---|---|
| T01 | `src/lenticularis/rules/reactive.py` | **NEW.** `affected_ruleset_ids(db, station_ids, virtual_members) -> set[str]` (the §2.2/§2.3 reverse lookup) + a per-ruleset-id in-flight guard (FR-007) |
| T02 | `src/lenticularis/rules/evaluator.py` | `write_decisions_batch` gains `measurement: str = "rule_decisions"` param (§4.1) |
| T03 | `src/lenticularis/database/influx.py` | `query_forecast_decisions_for_ruleset()` (§4.3) |
| T04 | `src/lenticularis/api/main.py` | Compose `on_collector_run` / `on_forecast_run` to call the new reactive evaluators *in addition to* the existing registry-updater/rewarmer (§2.1). Run one full evaluation pass at startup (FR-008) |
| T05 | `src/lenticularis/scheduler.py` | **Remove** `_run_ruleset_evaluator` and its `IntervalTrigger(minutes=10)` registration (FR-005). Health key repurposed — see §6 |
| T06 | `src/lenticularis/api/routers/rulesets.py` | `_evaluate_at()` (forecast branch) reads `query_forecast_decisions_for_ruleset` first; falls back to today's live `run_forecast_evaluation_at` on a cache miss (§3 diagram, §7.2) |
| T07 | `tests/backend/test_reactive_evaluation.py` | **NEW** — reverse lookup incl. virtual-station expansion; in-flight coalescing; startup pass; forecast-horizon write + read-back; cache-miss fallback |
| T08 | `tests/backend/conftest.py` | `FakeInflux` gains stubs for the new methods (`06-testing-conventions.md` warning — missing stubs surface as `AttributeError`, not a clean failure) |

---

## 6. Observability (`08-operability.md`)

Removing a named scheduler job removes a health-dashboard row. Replace it with an equivalent
signal so a broken reactive path doesn't fail silently:

- Keep a `"ruleset_reactive"` health entry (`type: "derived"`), updated by the composed hook:
  `last_triggered_at`, `last_affected_count`, `last_error`. Surfaced the same way
  `get_collector_health()` already exposes every other job.
- Log at INFO once per triggered batch: `[Lenti:reactive-eval] collector=%s stations=%d
  affected_rulesets=%d` — mirrors the thermal collector's coverage-logging precedent
  (`specs/006` §7.1 note 6): a silently-idle reactive path must be visible, not just "no error".

---

## 7. Constitution check (`00-ai-usage.md`) / constraint compliance (`04-constraints.md`)

| Item | Status |
|---|---|
| Batch, never loop (T09) | Reverse lookup is one SQL query per event; each affected ruleset still gets exactly one Influx query, same as today's poller — never per-station |
| No per-station Influx loop | `run_evaluation`/`run_forecast_evaluation` are unchanged — still batch internally |
| No new SQLite table/migration | Confirmed §4.4 — reuses `rule_conditions` as-is |
| `contains()` ban (learned the hard way, v1.22.6) | New read query (§4.3) uses plain `==`, per §2.4 |
| Swallowed exceptions | The composed hook wraps each reactive-evaluator call in try/`logger.exception`, matching `_make_registry_updater`'s existing "best-effort" pattern **but must still log at ERROR, never silently pass** |
| Blocking the event loop | Reverse-lookup DB query and Influx writes go through `asyncio.to_thread`, same discipline as every existing scheduler job |
| Never monkey-patch scheduler attributes | Confirmed — composition happens once in `main.py`, not a second assignment (§2.1 warning) |

### 7.1 In-flight coalescing (FR-007) — exact mechanism

A module-level `set[str]` of ruleset ids currently being evaluated, guarded by a lock (same
shape as the bounded-cache pattern in `04-constraints.md`, minus the bound — this set is
self-draining, never grows unbounded):
```python
_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()

def try_claim(ruleset_id: str) -> bool:
    with _in_flight_lock:
        if ruleset_id in _in_flight:
            return False
        _in_flight.add(ruleset_id)
        return True

def release(ruleset_id: str) -> None:
    with _in_flight_lock:
        _in_flight.discard(ruleset_id)
```
If two collector runs complete within the same second and both touch a shared ruleset, the
second's attempt to claim that ruleset id is skipped — not queued, not retried. This is safe: the
ruleset will be re-evaluated on the *next* event for either network anyway, and per FR-004/NFR-002
skipping a redundant concurrent evaluation is exactly the point, not a missed update.

### 7.2 Cache-miss fallback (T06) — do not remove the live path

`run_forecast_evaluation_at` (`evaluator.py:625`) and `query_forecast_snapshot_for_stations`
(fixed in v1.22.6) are **not deleted**. A ruleset created between two forecast-collector runs, or
whose horizon-cache write failed, must still resolve on demand — same graceful-degradation
reasoning as FR-008's boot pass. The router tries the cache first; only on an empty/missing
result does it fall through to the existing live call.

---

## 8. Testing (`06-testing-conventions.md`)

- **T07 reverse lookup**: a ruleset referencing a canonical station id is found when a *member*
  of that station's virtual cluster is the one reported as updated (FR-003) — this is the single
  highest-value test, since it's the one subtlety easy to get wrong.
- **In-flight guard**: two concurrent `try_claim()` calls for the same ruleset id — second
  returns `False`; after `release()`, a third call succeeds again.
- **Startup pass**: every ruleset with conditions gets exactly one evaluation at boot, before any
  event fires.
- **Forecast horizon write + read-back**: `write_decisions_batch(..., measurement=
  "rule_decisions_forecast")` then `query_forecast_decisions_for_ruleset` returns the same steps.
- **Cache-miss fallback**: `query_forecast_decisions_for_ruleset` returning `[]` still produces a
  correct decision via the existing live path — a regression guard proving T06 didn't remove the
  fallback.
- **Regression guard**: `_run_ruleset_evaluator` and its `IntervalTrigger` are gone from
  `scheduler.py` — assert the job id is absent from `scheduler._scheduler.get_jobs()`.

---

## 9. Version & deploy note

Behavioural change (evaluation trigger, new measurement, a removed scheduler job) — not a bugfix
patch. Bump to **v1.23.0** per this project's convention that a fixed patch series (v1.22.x) is
for the "one specific defect" fixes just shipped; a feature-shaped change gets a minor bump.
`pyproject.toml`, `README.md` milestone table, `.ai/context/features.md` all need the usual sync
(T-numbers to be assigned in `tasks.md`).

---

## 10. Explicitly out of scope (per spec.md)

- Thermal-forecast rules integration (`specs/006` Phase 2) — reactive evaluation will cover it
  once thermal conditions exist, via the same `on_forecast_run`-style hook once the thermal
  collector grows one; not wired here.
- Any change to condition/group/combination evaluation logic itself.
- Any UI change — the map/editor/analysis pages are unaffected; only *when* a decision is computed
  changes, never its shape.
- An index on `rule_conditions.station_id` — noted in §2.2, deferred until measured necessary.
