# T01 — Harden Flux queries against injection + validate IDs

**Severity:** Critical · **Phase:** 1 · **Model tier:** Moderate

## Ground Rules (read before editing)
- Read `.ai/instructions/02-backend-conventions.md`, `04-constraints.md`, `08-operability.md`.
- LF line endings only. No new dependencies. No `print()` (use `logging`). No `os.environ` (use `get_config()`).
- Add logging to any function you touch that lacks it. Implement exactly this task — no extra refactors.
- Verify with the Acceptance Criteria before reporting done.

## Problem
Every Flux query in `src/lenticularis/database/influx.py` interpolates caller-supplied values
(`station_id`, `ruleset_id`, `level_hpa`, `init_date`, station-id arrays) directly into the query
string with f-strings, e.g. `influx.py:131`:
```python
|> filter(fn: (r) => r.station_id == "{station_id}")
```
The station read endpoints in `src/lenticularis/api/routers/stations.py` have **no auth** and pass
the raw `station_id` path param straight into these queries. A value containing `"` breaks out of
the Flux string literal → unauthenticated Flux injection (cross-measurement/bucket read, DoS).

## Fix — two layers

### Layer 1: Escape all interpolated values (defense in depth)
In `src/lenticularis/database/influx.py`, add a module-level helper near the top (after the
`MEASUREMENT_*` constants):
```python
def _flux_str(value) -> str:
    """Escape a value for safe interpolation inside a Flux double-quoted string literal."""
    s = "" if value is None else str(value)
    # Order matters: escape backslash first, then the quote; drop CR/LF entirely.
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")
```
Then wrap **every** interpolated tag value used inside a Flux string literal. Find them with a
search for these patterns and fix each occurrence:
- `== "{station_id}"`  → `== "{_flux_str(station_id)}"`
- `== "{ruleset_id}"`  → `== "{_flux_str(ruleset_id)}"`
- `== "{level_hpa}"`   → `== "{_flux_str(level_hpa)}"`
- the per-id array builders that produce `ids_literal` (search `for sid in station_ids` and
  `contains(value: r.station_id`): escape each `sid` with `_flux_str(sid)` when building the literal.
- any `init_date`, `source`, `model`, `network` value interpolated into a string literal.

Known functions that interpolate (verify the full list by grepping `"{` inside `flux = f"""` blocks):
`query_latest`, `query_history`, `query_latest_for_stations`, `query_history_all_stations`,
`query_forecast_for_stations`, `query_forecast_accuracy`, `query_forecast_replay`,
`query_decision_history`, `query_forecast_grid`, `query_forecast_snapshot_for_stations`,
`query_observation_snapshot_for_stations`, `has_measure`.

Numeric params already bounded by FastAPI `Query(ge=, le=)` (e.g. `hours`) do not need escaping,
but escaping a stringified number is harmless — when unsure, escape.

### Layer 2: Reject unknown station IDs at the router
In `src/lenticularis/api/routers/stations.py`, add a guard helper and call it at the start of the
four station-scoped endpoints (`get_station`, `get_latest`, `get_history`, `get_station_forecast`,
`get_forecast_accuracy`). `get_station` already 404s on unknown IDs via the registry — extend the
same check to the others:
```python
def _require_known_station(request: Request, station_id: str) -> None:
    reg = _get_display_registry(request)
    members = _get_virtual_members(request)
    known = station_id in reg or any(station_id in m for m in members.values())
    if not known:
        logger.warning("[Lenti:stations] rejected unknown station_id=%r", station_id)
        raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")
```
An injected payload is never a registered station id, so this also blocks injection at the edge.

For `ruleset_id` interpolated in `query_decision_history`: validate it is a UUID before querying
(in the calling router `rulesets.py`), e.g. `uuid.UUID(ruleset_id)` in a try/except → 404 on failure.

## Out of scope (note for the human, do not implement here)
- Adding authentication to the public station endpoints (product decision — may break the public map).
- Scoping the InfluxDB token to read-only (operational; see the task-pack README manual actions).

## Acceptance criteria
- `curl '/api/stations/x%22%20foo/history'` (a `station_id` containing a quote/space) returns
  **404**, not a 500/Flux error and not data.
- Valid requests (`/api/stations/<real-id>/history`, `/latest`, `/forecast`) still return data.
- No raw, unescaped `"{station_id}"` / `"{ruleset_id}"` remain in `influx.py` (grep is clean).
- Service starts; logs show the rejection `WARNING` line on a bad id.
