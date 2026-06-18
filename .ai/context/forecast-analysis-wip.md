# Forecast Analysis Page — Work In Progress

## Status: Functionally complete; awaiting first successful data load after bug fixes

---

## What Was Built (v1.18)

A new cross-station forecast accuracy analysis page at `/forecast-analysis`.

### New files
- `static/forecast-analysis.html` — page with lead-time toggle (D+1/D+2/D+3), per-field ranked tables
- `static/forecast-analysis.js` — fetch, render, colour-coded MAE cells, correction hints, bucket switching

### Modified files
- `src/lenticularis/database/influx.py`
  - Added `query_forecast_accuracy_ranking(days, top_n)` method
  - Added `_ranking_client` / `_ranking_query_api` with 300 s timeout (separate from `_slow_query_api` 60 s)
  - `close()` now closes all three clients
- `src/lenticularis/config.py`
  - `InfluxDBConfig` gains `ranking_query_timeout: int = 300000`
- `src/lenticularis/api/routers/stations.py`
  - `GET /api/stations/forecast-accuracy-ranking` endpoint (declared before `/{station_id}` routes)
  - `force_refresh: bool` query param — bypasses the 30-min cache when `true`
  - `warm_accuracy_ranking_cache(influx, registry)` async function
- `src/lenticularis/api/main.py`
  - Page route `/forecast-analysis` → `forecast-analysis.html`
  - `warm_accuracy_ranking_cache` imported and called as startup task
- `src/lenticularis/scheduler.py`
  - 24h `IntervalTrigger` job `forecast_accuracy_ranking` → `_run_accuracy_ranking_warmer()`
- All 13 HTML files — added Forecast Analysis nav link
- `static/i18n/en.json` + `de.json` + `fr.json` + `it.json` — added `forecast_analysis.*` block

---

## Bug Fix History (all in this session)

### Bug 1: `timeSrc: "_start"` silently unsupported
- **Symptom:** API returned 7 fields, all with empty station lists.
- **Root cause:** `aggregateWindow(every: 1h, fn: last, timeSrc: "_start")` — `timeSrc` not supported in deployed InfluxDB version. Timestamps stayed at end-of-window (11:00 for 10:00–11:00 window), never matching forecast valid_times at exact hours (10:00).
- **Fix:** Removed `timeSrc: "_start"`; compensated in Python: `hour_key = (ts - timedelta(hours=1)).replace(...)`.

### Bug 2: Stale cache pre-loaded before Bug 1 fix was deployed
- **Symptom:** After fix deployed, page still showed no data (19ms cache hit).
- **Fix:** Added `force_refresh: bool` query param to the API endpoint + JS passes `&force_refresh=true` when Refresh button clicked.

### Bug 3: Actuals query silently timing out at 60 s
- **Symptom:** After force_refresh, still no data; total elapsed ~104 s.
- **Root cause:** `query_forecast_accuracy_ranking` used `_slow_query_api` (60 s timeout). Query A (actuals, 90 days, all stations, aggregateWindow) took >60 s → timed out silently → `actual = {}` → no matches with forecast data → all lists empty. Query B (~44 s) ran but matched nothing.
- **Fix:** Added `_ranking_client` with `ranking_query_timeout: int = 300000` (5 min). Both ranking queries now use `_ranking_query_api`.

---

## Next Step After Sleep

1. **User syncs files to dev server and restarts container.**
2. Navigate to `/forecast-analysis` — page will auto-load (warm-up task). Wait ~2-3 min for fresh computation.
3. If still empty, click **↻ Refresh** (force_refresh=true) and wait ~2 min.
4. Check server logs for: `actuals loaded — N stations` and `error matrix built — N stations matched`.
   - If "actuals loaded — 0 stations": Query A still failing — check InfluxDB logs for error.
   - If "actuals loaded — N stations" but "error matrix built — 0 stations": timestamp join still misaligned.
5. If data appears: commit as v1.18.

---

## API Contract

```
GET /api/stations/forecast-accuracy-ranking?days=90&top_n=10[&force_refresh=true]

{
  "days": 90,
  "top_n": 10,
  "computed_at": "2026-05-15T21:09:42.630250+00:00",
  "ranking": {
    "wind_speed": {
      "D+1": [ {"station_id": "X", "name": "Y", "network": "meteoswiss", "canton": "VS", "mae": 2.3, "bias": 0.8, "n": 142}, ... ],
      "D+2": [...],
      "D+3": [...]
    },
    "wind_gust": { ... },
    ... (7 fields total)
  }
}
```

Cache TTL: 30 min server-side. Pre-warmed at startup + every 24h via scheduler.

---

## Key Implementation Details

### Timestamp alignment (actuals)
Weather observations are not written at exact hours. `aggregateWindow(every: 1h, fn: last)` produces end-of-window timestamps (11:00 for 10:00–11:00 window). Python compensates:
```python
hour_key = (ts - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0).isoformat()
```

### Forecast valid_time join key
```python
hour_key = valid_time.replace(minute=0, second=0, microsecond=0).isoformat()
```
Forecasts are written at exact hour boundaries, so no adjustment needed.

### Lead-time bucketing
```python
lead_h = (valid_time - init_dt).total_seconds() / 3600.0
bucket = "D+1" if lead_h <= 24 else ("D+2" if lead_h <= 48 else "D+3")
# lead_h <= 0 or > 72 → skipped
```

### MAE thresholds for colour coding (JS, `forecast-analysis.js`)
| Field | High (red) | Medium (orange) |
|---|---|---|
| wind_speed | ≥ 5.0 | ≥ 2.5 |
| wind_gust | ≥ 8.0 | ≥ 4.0 |
| wind_direction | ≥ 30° | ≥ 15° |
| temperature | ≥ 3.0 | ≥ 1.5 |
| humidity | ≥ 15% | ≥ 8% |
| pressure_qff | ≥ 5.0 | ≥ 2.5 |
| precipitation | ≥ 2.0 | ≥ 0.8 |

### Correction hint logic
`|bias| < 0.1 × mae` → neutral; `bias > 0` → "Forecast runs high by X"; `bias < 0` → "Forecast runs low by X".
