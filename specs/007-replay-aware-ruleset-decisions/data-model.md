# Data Model: Replay-Aware Ruleset Decisions

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md)
**Phase**: 1 — Design

---

## Summary

**No SQLite table changes. No InfluxDB measurement, tag, or field changes.** (NFR-004). This feature
adds one request parameter to an existing endpoint, one new evaluator function that returns the
already-established evaluation-result shape, and frontend-only state for coalescing replay-driven
marker refreshes. Nothing here is persisted.

---

## Extended request: `GET /api/rulesets/{ruleset_id}/evaluate`

| Param | Type | Default | Meaning |
|---|---|---|---|
| `at_time` | `Optional[datetime]` | `None` | **Unchanged.** Absent = live. Present = evaluate at this moment instead of now |
| `forecast` | `bool` | `False` | **NEW.** Only meaningful when `at_time` is set. `False` (default) = observed-data path (`run_evaluation_at`, unchanged, FR-002). `True` = forecast path (`run_forecast_evaluation_at`, new, FR-003) |

Existing callers that never pass `forecast` are unaffected — the default preserves exactly today's
`at_time` behaviour (research R3).

### Response — `EvaluationResult` (`models/rules.py:255-263`)

**Unchanged.** Same fields regardless of which of the three evaluation paths (live / observed-at_time
/ forecast-at_time) produced the result:

```
{
  "decision":              "green" | "orange" | "red",
  "evaluated_at":           ISO8601 string,
  "condition_results":      [ConditionResult, ...],
  "no_data_stations":       [station_id, ...],
  "landing_decisions":      [LandingDecision, ...],   // launch sites with links only
  "best_landing_decision":  "green" | "orange" | "red" | null
}
```

`landing_decisions`/`best_landing_decision` now reflect the **same** `at_time`/`forecast` mode as the
primary decision (FR-004, research R4) instead of always being live.

---

## New evaluator function: `run_forecast_evaluation_at()`

`rules/evaluator.py`, alongside `run_evaluation_at` (line 472) and `run_forecast_evaluation`
(line 625).

```python
def run_forecast_evaluation_at(
    ruleset: RuleSet,
    influx: InfluxClient,
    valid_time: datetime,
) -> dict:
    """
    Evaluate all conditions in *ruleset* against forecast data for a single *valid_time*.

    Uses InfluxClient.query_forecast_snapshot_for_stations (±30 min window around
    valid_time in weather_forecast) instead of the full multi-hour series
    run_forecast_evaluation returns. Does NOT write to InfluxDB — ephemeral, same
    as run_evaluation_at. Returns the same dict shape as run_evaluation_at.
    """
```

**Body shape** (research R1, R2): collect `station_id`/`station_b_id` from `ruleset.conditions` (same
pattern as `run_evaluation_at:487-494`, minus virtual-member expansion — research R9) → one call to
`influx.query_forecast_snapshot_for_stations(station_ids, valid_time)` → build `station_data` +
`no_data_stations` from the result → **call `_evaluate_from_station_data(ruleset, station_data)`**
(the shared core, `evaluator.py:207-303`) rather than duplicating the standalone/group/combination
block inline a fifth time.

### Return shape — identical to `run_evaluation_at`'s

```
{
  "decision":          "green" | "orange" | "red",
  "evaluated_at":       valid_time.isoformat(),
  "condition_results":  [...],            # from _evaluate_from_station_data
  "no_data_stations":   [station_id, ...]  # built by the caller, same as run_evaluation_at
}
```

---

## Router change: `evaluate_ruleset` (`api/routers/rulesets.py:205-271`)

Extract the existing `if at_time is not None: ... else: ...` branch (lines 237-248) into a helper
used for **both** the primary ruleset and any linked landing rulesets (lines 251-269):

```python
def _evaluate_at(rs, influx, at_time, forecast, virtual_members):
    if at_time is None:
        from lenticularis.rules.evaluator import run_evaluation, write_decision
        result = run_evaluation(rs, influx, virtual_members)
        write_decision(rs, result, influx)
        return result
    if forecast:
        from lenticularis.rules.evaluator import run_forecast_evaluation_at
        return run_forecast_evaluation_at(rs, influx, at_time)
    from lenticularis.rules.evaluator import run_evaluation_at
    return run_evaluation_at(rs, influx, at_time, virtual_members)
```

The landing-links loop (currently unconditional `run_evaluation(landing_rs, ...)` at line 258) calls
`_evaluate_at(landing_rs, influx, at_time, forecast, virtual_members)` instead — closing FR-004.
`write_decision` (InfluxDB write) still only happens on the live path, for both the primary ruleset
and any landing rulesets, exactly as today (NFR-004 — no change to what gets written).

---

## Frontend state (`static/index.html`) — nothing persisted, page-lifetime only

| Name | Type | Purpose |
|---|---|---|
| `_rulesetMarkerGen` | `number` | Incremented at the start of every `loadRulesetMarkers` batch. A batch discards its results instead of touching `rulesetLayer` if a newer generation has started by the time it resolves (NFR-003) |
| `_rulesetRefreshPending` | `{ts, isForecast} \| null` | The most recent replay frame's target, recorded when a refresh is already in flight. Consumed (and cleared) when the in-flight batch resolves (research R5) |
| `_rulesetRefreshBusy` | `boolean` | True while a `loadRulesetMarkers` batch is in flight — gates whether a new `onFrame` call starts a batch or just updates `_rulesetRefreshPending` |

### `loadRulesetMarkers(atTime?, isForecast?)` — extended signature

- No args (or `_tnLive` true): today's live behaviour, unchanged — `GET /evaluate` with no query
  params, `setInterval` continues to call it this way (research R6).
- `atTime` set: each per-ruleset fetch becomes
  `GET /api/rulesets/{id}/evaluate?at_time=<atTime>&forecast=<isForecast>`.
- Opportunity shown/hidden (FR-006) and the launch halo colour need no code change beyond this —
  both already re-derive from whatever `dec` the fetch returned (`index.html:788`, `795`); once `dec`
  reflects the replayed timestamp, both already "just work" (mirrors spec 003's R2 style free-FR
  reasoning).

---

## What is NOT changing

- No SQLite schema change.
- No InfluxDB measurement/tag/field change. `rule_decisions` is still only written on the live path.
- `run_evaluation`, `run_evaluation_at`, `run_forecast_evaluation`, `_evaluate_from_station_data` —
  decision logic in all four is untouched (FR-007).
- `/api/rulesets/{id}/forecast` (`get_forecast`, the ruleset-analysis chart's full-series endpoint) —
  untouched, unrelated to this feature (spec Out of Scope).
- `EvaluationResult`'s response schema — unchanged (NFR-004); only a new optional request parameter.
