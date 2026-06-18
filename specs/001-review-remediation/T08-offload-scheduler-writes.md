# T08 — Offload synchronous Influx writes off the scheduler's event loop

**Severity:** High · **Phase:** 2 · **Model tier:** Moderate

## Ground Rules
- Read `.ai/instructions/08-operability.md`. LF line endings only. Exactly this task.

## Problem
The scheduler runs on the app's `AsyncIOScheduler` event loop. Two `async def` jobs make synchronous
blocking Influx writes directly on that loop:
- `src/lenticularis/scheduler.py` `_run_forecast_collector`: inside `async for station, pts in
  collector.collect_all_iter(...)` it calls `self._influx.write_forecast(pts)` synchronously
  (around line 659) — many small loop stalls per run.
- `_run_grid_forecast_collector`: writes the full grid (~1.17M points → ~234 chunked writes) via
  `write_forecast_grid` synchronously — a periodic multi-second loop freeze.

The deviation writer in the same file already does this correctly with `run_in_executor` — copy that
pattern.

## Fix
Wrap each blocking write in the running loop's executor. Example for the forecast write:
```python
loop = asyncio.get_event_loop()
async for station, pts in collector.collect_all_iter(...):
    if pts:
        await loop.run_in_executor(None, self._influx.write_forecast, pts)
        total_points += len(pts)
        ...
```
And for the grid write:
```python
await asyncio.get_event_loop().run_in_executor(None, self._influx.write_forecast_grid, <args…>)
```
Match the exact argument list of the existing `write_forecast_grid` call. Ensure `import asyncio`
is present (it is used elsewhere in the file — confirm).

Do not change the chunking inside `write_forecast_grid` itself; only move the call off the loop.

## Acceptance criteria
- Neither `_run_forecast_collector` nor `_run_grid_forecast_collector` calls `self._influx.write_*`
  directly on the loop — both go through `run_in_executor`.
- A forecast/grid collection run still writes the same number of points (check the existing
  `wrote N points` / grid log lines).
- During a grid write, a concurrent API request (e.g. `/api/health`) is not stalled for seconds.
