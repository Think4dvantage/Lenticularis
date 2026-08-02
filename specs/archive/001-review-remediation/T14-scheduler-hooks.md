# T14 — Replace scheduler monkey-patching with real post-run hooks

**Severity:** Medium · **Phase:** 3 · **Model tier:** Moderate · **Do after/with T13**

## Ground Rules
- Read `.ai/instructions/02-backend-conventions.md`, `08-operability.md`. LF line endings only.
- Exactly this task — behavior must stay identical; only the wiring mechanism changes.

## Problem
`src/lenticularis/api/main.py` reaches into the scheduler at startup and **reassigns its private
methods** (`_patch_scheduler_registry` → `scheduler._run_collector = patched`;
`_patch_scheduler_forecast` → `scheduler._run_forecast_collector = patched`). This couples `main.py`
to the scheduler's private signatures and internal state (`_collector_health`,
`last_measurement_count`) — any refactor of those privates silently breaks the wrap.

## Fix — first-class callbacks on `CollectorScheduler`
In `src/lenticularis/scheduler.py`:
1. Add two optional callback attributes, defaulting to no-ops, set in `__init__`:
   ```python
   self.on_collector_run = None        # called: await cb(collector) after each observation collect
   self.on_forecast_run = None         # called: await cb(collector, horizon_hours, health) after each forecast collect
   ```
2. At the **end** of `_run_collector(self, collector)`, after the existing work, invoke the hook:
   ```python
   if self.on_collector_run is not None:
       try:
           await self.on_collector_run(collector)
       except Exception:
           logger.exception("on_collector_run hook failed")   # log, never swallow silently
   ```
3. At the end of `_run_forecast_collector(self, collector, horizon_hours)`, after `health` is
   finalized, invoke:
   ```python
   if self.on_forecast_run is not None:
       try:
           await self.on_forecast_run(collector, horizon_hours, health)
       except Exception:
           logger.exception("on_forecast_run hook failed")
   ```

In `src/lenticularis/api/main.py`:
- Delete `_patch_scheduler_registry` and `_patch_scheduler_forecast`.
- Define two real async callbacks with the same bodies (registry rebuild; forecast cache
  invalidate + re-warm) and assign them:
  ```python
  scheduler.on_collector_run = _make_registry_updater(app.state.station_registry,
                                                       app.state.display_registry,
                                                       app.state.virtual_members, dedup_distance_m)
  scheduler.on_forecast_run = _make_forecast_rewarmer(influx, app.state.display_registry)
  ```
  where the factory functions return the async callback closures. The forecast callback receives
  `health` directly (no more reaching into `scheduler._collector_health`).
- **Important:** the registry hook must no longer call `original(collector)` (the scheduler now calls
  the hook itself after doing its own work). Just do the registry update.
- Replace the previous `except Exception: pass` in the registry update with a logged warning
  (this also satisfies T18 for that site).

## Acceptance criteria
- `main.py` contains no `scheduler._run_* = ...` reassignment; the scheduler exposes
  `on_collector_run` / `on_forecast_run`.
- After an observation collector runs and returns a new station, the display registry rebuilds
  (same behavior as before).
- After a successful forecast run, the forecast replay cache is invalidated and re-warmed (watch for
  the existing "invalidated N replay cache entries, re-warming" log line).
- A hook exception is logged, not swallowed silently, and does not crash the job.
