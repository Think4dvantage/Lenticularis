# T07 — Stop blocking the event loop in async Influx handlers

**Severity:** High · **Phase:** 2 · **Model tier:** Moderate

## Ground Rules
- Read `.ai/instructions/02-backend-conventions.md` ("async/await for all I/O"), `08-operability.md`.
- LF line endings only. Add logging to handlers you touch that lack it. Exactly this task.

## Problem
`influxdb-client` and SQLAlchemy `Session` are **synchronous**. FastAPI runs plain `def` handlers in
a threadpool (safe) but runs `async def` handlers on the event loop. Several `async def` handlers
call blocking Influx queries directly, which freezes *all* concurrent requests for the query
duration. The `/replay` and `/forecast-accuracy-ranking` handlers already offload correctly via
`run_in_executor`; the rest do not.

Confirmed blocking handlers:
- `src/lenticularis/api/routers/wind_forecast.py` → `get_wind_forecast_grid` (worst: pivots ~38k rows)
- `src/lenticularis/api/routers/stations.py` → `list_stations`, `get_data_bounds`, `get_station`,
  `get_latest`, `get_history`, `get_station_forecast`, `get_forecast_accuracy`
- `src/lenticularis/api/routers/foehn.py` → all handlers that call `influx.*` and/or the DB session

## Fix — prefer the simplest correct option
**Option A (recommended): drop `async`.** Change each listed handler from `async def` to plain `def`.
FastAPI then runs it in its threadpool, matching the already-safe `stats.py`/`org.py`/`rulesets.py`
handlers. Do **not** keep any `await` in a handler you de-`async`; these handlers' `await`s are only
on the `run_in_executor` wrappers (which you remove) — the underlying Influx calls are sync. Verify
each converted handler has no remaining `await`.

Do **not** convert `/replay` or `/forecast-accuracy-ranking` — they are already correct.

For `foehn.py`, check each handler: if it `await`s nothing except removable executor wrappers,
convert to `def`. If a handler genuinely awaits async work (e.g. an `httpx` call), keep it `async`
and wrap the blocking Influx/DB call in `await asyncio.get_event_loop().run_in_executor(None, fn, ...)`
instead (Option B).

> Rationale for Option A: the rest of the codebase already relies on FastAPI's threadpool for sync
> handlers; making these consistent is lower-risk than scattering `run_in_executor` calls.

## Acceptance criteria
- The listed handlers are plain `def` (or, where async is required, their Influx/DB calls run via
  `run_in_executor`). No converted handler contains a leftover `await`.
- Under two concurrent slow requests (e.g. two `/api/wind-forecast/grid` calls), a third quick
  request (`/api/health`) still returns promptly — it is no longer serialized behind the grid query.
- All affected endpoints return the same payloads as before.
