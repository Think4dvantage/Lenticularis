# Replay Playback — Architecture & Known Issues

## How the replay works

The time navigation bar has day buttons (offset -3 to +5) and an hour row (07:00–19:00 or 08/11/14/17).
When a day is selected, `tnSelectDay(offset)` loads data via `_mapReplay.load(params)` which hits
`GET /api/stations/replay?start=...&end=...&forecast_hours=N`. The engine caches results for 10 min.

Playback (`▶ Play day`) is driven by `tnStartPlay()` in `static/index.html`, which iterates a list of
local hours and calls `tnSelectHour(h)` → `tnSeekToHour(h)` → `_mapReplay.seekTo(bestIdx)` per frame.
`tnSeekToHour` converts local hour to UTC, computes the target epoch for the selected day, then finds
the nearest frame index in `_mapReplay._timestamps`.

## Data resolution — ICON-CH model tiers

| Source | Horizon | Native resolution |
|--------|---------|-------------------|
| ICON-CH1 (lsmfapi) | 0–30 h | **hourly** |
| ICON-CH2 (lsmfapi) | 30–120 h | **3-hourly** (06, 09, 12, 15, 18 UTC = 08, 11, 14, 17 CEST) |

CH1 collection was broken for a period — only CH2 data was available. When CH1 is restored,
today and tomorrow will have genuine hourly variation again.

lsmfapi currently returns hourly `valid_time` timestamps even for CH2 data, but with
**forward-filled values** (intermediate hours carry the same values as the 3-hourly boundary).
The Lenticularis collector (`forecast_swissmeteo.py`) is fully transparent — it stores whatever
`valid_time`/value pairs lsmfapi returns. No resampling or rounding on the Lenticularis side.

## Frontend adaptations (implemented)

**`tnPlayHours()` — `static/index.html`**
Returns the hour list for the current `_tnOffset`:
- offset ≥ 2: `[8, 11, 14, 17]` (CH2 change points only)
- otherwise: `[7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]`

**`tnUpdateHourButtons()`**
Hides/shows `.tn-hour-btn` elements to match `tnPlayHours()`. Called on every day selection
(`tnSelectDay`) and when returning to live mode (`tnGoLive`).

**`tnStartPlay()` — speed**
- 4 frames (3-hourly days): 1000 ms/frame
- 13 frames (hourly days): 600 ms/frame

## Future work

When CH1 collection is restored in lsmfapi:
- Today and tomorrow will automatically get genuine hourly values — no Lenticularis changes needed.
- Consider adding lsmfapi-side linear interpolation for CH2 (30–120 h) to smooth far-range days.
  Wind direction requires circular (shortest-arc) interpolation; scalars are plain linear.
- The `tnPlayHours()` split at offset ≥ 2 will remain correct: CH1 covers ~30 h which always
  includes today and tomorrow; CH2 takes over from +30 h onward.

## Replay backend — key files

- `static/replay.js` — `ReplayEngine` class: load, cache, seekTo, stepForward, _buildSnapshot
- `static/index.html` — `tnStartPlay`, `tnSelectDay`, `tnSeekToHour`, `tnPlayHours`, `tnUpdateHourButtons`
- `src/lenticularis/api/routers/stations.py` — `GET /replay`, `_build_replay_payload`, server-side cache
- `src/lenticularis/database/influx.py` — `query_forecast_replay` (latest init_date dedup, swissmeteo preferred)
- `src/lenticularis/collectors/forecast_swissmeteo.py` — calls `lsmfapi /api/forecast/station`
