# Replay Playback — Architecture & Known Issues

## How the replay works

The time navigation bar has day buttons (offset -3 to +5) and an hour row (07:00–19:00).
When a day is selected, `tnSelectDay(offset)` loads data via `_mapReplay.load(params)` which hits
`GET /api/stations/replay?start=...&end=...&forecast_hours=N`. The engine caches results for 10 min.

Playback (`▶ Play day`) is driven by `tnStartPlay()` in `static/index.html`, which iterates all
13 local hours (07–19) and calls `tnSelectHour(h)` → `tnSeekToHour(h)` → `_mapReplay.seekTo(bestIdx)`
per frame. `tnSeekToHour` converts local hour to UTC, computes the target epoch for the selected day,
then finds the nearest frame index in `_mapReplay._timestamps`.

## Data resolution — lsmfapi (swissmeteo)

| Source | Horizon | Resolution |
|--------|---------|------------|
| lsmfapi (ICON-CH1 + CH2 blended) | 0–120 h | **hourly** |

lsmfapi delivers a single blended hourly time series for the full 0–120 h window.
No frontend distinction between CH1 and CH2 tiers is needed or applied.

The Lenticularis collector (`forecast_swissmeteo.py`) stores whatever `valid_time`/value
pairs lsmfapi returns. No resampling on the Lenticularis side.

## Collector scheduling

Both the swissmeteo station collector and the wind forecast grid collector run every 60 minutes.
lsmfapi updates ~4×/day (~04Z, 10Z, 16Z, 22Z); most hourly runs are no-ops but that's fine —
lsmfapi is co-located and the cost is negligible.

Open-Meteo collectors are disabled. They can be re-enabled manually as a temporary fallback
if lsmfapi is unavailable for an extended period.

## Frontend — hour navigation

`tnPlayHours()` always returns all 13 hours `[7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]`
for every day offset. `tnStartPlay()` runs at 600 ms/frame for all days.

## Replay backend — key files

- `static/replay.js` — `ReplayEngine` class: load, cache, seekTo, stepForward, _buildSnapshot
- `static/index.html` — `tnStartPlay`, `tnSelectDay`, `tnSeekToHour`, `tnPlayHours`, `tnUpdateHourButtons`
- `src/lenticularis/api/routers/stations.py` — `GET /replay`, `_build_replay_payload`, server-side cache
- `src/lenticularis/database/influx.py` — `query_forecast_replay` (latest init_date dedup, swissmeteo preferred)
- `src/lenticularis/collectors/forecast_swissmeteo.py` — calls `lsmfapi /api/forecast/station`
