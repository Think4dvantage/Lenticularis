# Architecture Reference

## SQLite Tables

| Table | Key columns |
|---|---|
| `organizations` | `id`, `slug` (unique), `name`, `description`, `created_at` |
| `users` | `id`, `username`, `email`, `hashed_password`, `role`, `org_id` FK → organizations, `created_at` |
| `weather_stations` | `station_id`, `name`, `network`, `latitude`, `longitude`, `elevation`, `canton`, `active` |
| `launch_sites` | `id`, `name`, `latitude`, `longitude`, `owner_id` FK → users |
| `rulesets` | `id`, `name`, `description`, `launch_site_id`, `owner_id`, `org_id` FK → organizations, `site_type` (launch/landing/opportunity), `combination_logic`, `is_public`, `is_preset`, `clone_count`, `cloned_from_id`, `notify_on` (nullable CSV of colours e.g. `"green,orange"`), `last_notified_decision` (nullable), `created_at`, `updated_at` |
| `rule_conditions` | `id`, `ruleset_id`, `group_id` (nullable), `station_id`, `station_b_id` (nullable), `field`, `operator`, `value_a`, `value_b` (nullable), `result_colour`, `sort_order` |
| `condition_groups` | `id`, `ruleset_id`, `parent_group_id` (nullable), `logic` (AND/OR), `sort_order` |
| `ruleset_webcams` | `id`, `ruleset_id`, `url`, `label`, `sort_order` |
| `notification_configs` | `id`, `user_id`, `launch_site_id`, `channel`, `config_json`, `on_transitions_json` |
| `station_dedup_overrides` | `id`, `station_id_a`, `station_id_b`, `note`, `created_at` — manually-defined co-location pairs; pre-seeded with Lehn pair (holfuy-1850 ↔ windline-6116) |
| `user_foehn_configs` | `user_id` PK FK → users, `config_json` (full föhn config blob), `updated_at` — per-user föhn config overrides |

---

## InfluxDB Measurements

### `weather_data`
- **Tags**: `station_id`, `network`, `canton`
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qfe`, `pressure_qff`, `precipitation`, `snow_depth`; virtual foehn stations also have `foehn_active` (1.0=active, 0.5=partial, 0.0=inactive, −1.0=no_data)

### `weather_forecast`
- **Tags**: `station_id`, `network`, `model` (`icon-ch1`/`icon-ch2`/`open-meteo`/`icon-ch`), `source` (`swissmeteo` or `open-meteo`), `init_date` (YYYY-MM-DDTHH — one series per model-run hour)
- **Timestamp**: `valid_time` (the future moment the forecast is valid for)
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qff`, `precipitation`; SwissMeteo also writes `_min`/`_max` variants for all fields (ensemble spread); `init_time` (ISO string for Python-side dedup)
- **Primary source**: `swissmeteo` (lsmfapi ICON-CH1/CH2 ensemble). `open-meteo` is fallback.
- **Source preference** (`query_forecast_for_stations`): prefer `swissmeteo` when its `init_time` is ≤24h old; if stale, latest-init-date wins across sources.
- **Replay query pattern**: two-step — (1) fast `_latest_forecast_init_dates()` scan (`range(-12h)`, one field, normal 10s client) → (2) main pivot filtered to exact latest `init_date` per source (slow 60s client). Avoids scanning 72+ model runs.

### `wind_forecast_grid`
- **Tags**: `grid_id` (e.g. `"47.9000_5.9000"` — 4 decimal places for lsmfapi's ICON-CH1 ~10 km grid; Open-Meteo fallback uses `"46.00_7.00"` 2 decimal places), `level_hpa` (950/900/850/800/750/700/600/500), `init_date` (YYYY-MM-DDTHH)
- **Timestamp**: `valid_time` (UTC)
- **Fields**: `wind_speed` (km/h), `wind_direction` (degrees int), `humidity` (% RH), `lat`, `lon`
- **Primary source**: `ForecastGridSwissMeteoCollector` — lsmfapi `/api/forecast/grid?level_m=X` (8 levels in parallel, 1272 grid pts, ICON-CH1). Falls back to `ForecastGridCollector` (Open-Meteo, 171 pts, 0.25° grid) if lsmfapi returns 0 wind points.
- Altitude → hPa: 500→950, 1000→900, 1500→850, 2000→800, 2500→750, 3000→700, 4000→600, 5000→500

### `rule_decisions`
- **Tags**: `launch_site_id`, `ruleset_id`, `owner_id`, `site_type`
- **Fields**: `decision` (green/orange/red), `condition_results` (JSON array), `blocking_conditions` (JSON array of condition IDs)

**Removed**: `station_wind_profile` measurement was removed (v1.16). Altitude wind data is served by the wind forecast grid map, not per-station.

---

## Forecast Collectors

| Collector | File | Endpoint | Notes |
|---|---|---|---|
| SwissMeteo surface | `forecast_swissmeteo.py` | lsmfapi `/api/forecast/station?station_id=X` | Primary; all stations; flat fields + `_min`/`_max`; `init_time` + `forecast[]` schema |
| Open-Meteo surface | `forecast_openmeteo.py` | Open-Meteo API | Fallback; 60-min interval; serial (concurrency=1); 429 retry |
| SwissMeteo grid | `forecast_grid_swissmeteo.py` | lsmfapi `/api/forecast/grid?level_m=X` | Primary; 8 levels in parallel; 1272 pts; `ws`/`wd`/`rh` arrays |
| Open-Meteo grid | `forecast_grid.py` | Open-Meteo API | Fallback; 4 batches of 50 pts; runs every 3h |

**lsmfapi** (`lsmfapi-dev.lg4.ch`) is user-owned, same Docker network, no rate limiting. Serves ALL station networks (not just FGA). Response schema: `init_time`, `forecast[]` (surface) or `grid` + `frames[]` (grid). No per-station altitude wind endpoint — altitude data comes from the grid only.

**Scheduler status**: `ok_no_data` only when there were 0 eligible stations. When all stations fail with errors, status is `error`.

---

## InfluxDB Client

Two clients in `InfluxClient.__init__()`:
- `_query_api` — `timeout` from config (default 10s) — used by all standard queries
- `_slow_query_api` — `slow_query_timeout` from config (default 60s) — used only by `query_forecast_replay`

Config keys: `influxdb.timeout` (ms, default 10000), `influxdb.slow_query_timeout` (ms, default 60000).

---

## API Contracts

### Stations
- `GET /api/stations` — list all active stations (`?network=&canton=`)
- `GET /api/stations/{station_id}` — station metadata
- `GET /api/stations/{station_id}/latest` — most recent measurement
- `GET /api/stations/{station_id}/history` — `?from=&to=&fields=`
- `GET /api/stations/replay` — `?start=&end=&forecast_hours=&include_forecast=` — all stations over a time window for map replay; **server-side in-memory cache (5 min TTL)** keyed by query params; Flux query uses `aggregateWindow(30m, last)` before pivot; forecast data merged via two-step `query_forecast_replay`
- `GET /api/stations/{station_id}/forecast` — `?hours=N` — forecast points; response includes `forecast_source` and `forecast_model` from first data row
- `GET /api/stations/{id}/forecast-accuracy` — `?from=&to=`

### Wind Forecast Grid
- `GET /api/wind-forecast/grid?date=YYYY-MM-DD&level_m=1500` — requires `require_pilot`; maps `level_m` → `level_hpa` via `ALTITUDE_TO_HPA`; returns `{date, level_m, level_hpa, grid:[{lat,lon},...], frames:[{t, ws:[...], wd:[...], rh:[...]},...]}`; **grid is built dynamically from InfluxDB data** (not hardcoded 171-point assumption); `grid_id` tag used as canonical key (avoids float-precision issues); grid sorted lat desc, lon asc; arrows show cloud icon when `rh >= 90`

### Launch Sites, Rule Sets, Org, Gallery, Statistics, Föhn, Admin, AI, System
- (unchanged from previous versions — see `.ai/instructions/07-api-conventions.md`)

---

## Rules Engine Design

`rules/evaluator.py`:
1. Load condition tree from SQLite
2. Fetch latest measurement from InfluxDB per `station_id`
3. Apply operator/value logic → per-condition colour
4. Walk AND/OR group nesting
5. Apply `combination_logic` (`worst_wins` or `majority_vote`)
6. Return `TrafficLightDecision` + write to `rule_decisions` InfluxDB

Forecast evaluation reuses identical logic over hourly `valid_time` steps. Does NOT write to InfluxDB.

---

## Replay Cache Architecture

`api/routers/stations.py` module-level `_replay_cache: dict[str, tuple[Any, float]]` (key → payload, monotonic stored_at). TTL 5 min.

**Warm-up**: background `asyncio.Task` at startup iterating offsets `[1, 0, 2, -1, 3, -2, 4, -3, 5]` sequentially.

**Cache poisoning guard**: skip writing when `fc_frame_count == 0` and `include_forecast` is true — prevents obs-only entries from blocking forecast data.

**Post-forecast invalidation**: `_patch_scheduler_forecast()` in `main.py` monkey-patches `scheduler._run_forecast_collector` to call `invalidate_forecast_replay_cache()` + fire new `warm_replay_cache()` after each successful run (`status == "ok"` and `measurement_count > 0`).

---

## Föhn Detection Design

`foehn_detection.py` — shared between scheduler and API router.

**Delta/trend conditions** (`lookback_h` set): evaluates `current − historical[lookback_h][station][field]` vs threshold. Supports `humidity Δ2h < −10` (föhn arriving) patterns.

**Virtual foehn stations**: `foehn-beo`, `foehn-haslital`, `foehn-wallis`, `foehn-reussthal`, `foehn-rheintal`, `foehn-guggi`, `foehn-overall`. Written to `weather_data` every 10 min by `FoehnCollector`. Field: `foehn_active` (1.0/0.5/0.0/−1.0).

**Config**: `data/foehn_config.json` system-wide default; `user_foehn_configs` SQLite for per-user overrides.

---

## Virtual Station Deduplication

`services/dedup.py` — `build_deduped_registry(raw, distance_m=50.0, manual_pairs=None)`

1. Exclude `foehn` network
2. Union-find over pairs within 50 m (Haversine)
3. Union manual pairs from `station_dedup_overrides`
4. Pick canonical by priority: meteoswiss > slf > metar > holfuy > windline > ecowitt > wunderground
5. Non-canonical stations omitted from `display_registry`

**History**: member must have data in 2 h slice BEFORE window to be included.

---

## Deployment

### Traefik Labels — list format only
```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.myapp.rule=Host(`myapp.lg4.ch`)"
```

### Healthcheck
```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

### Dev Overlay
`docker-compose.dev.yml` extends base with live `src/` and `static/` volume mounts, Traefik labels for `lenti-dev.lg4.ch`, `PYTHONPYCACHEPREFIX=/tmp/pycache`.

### SSH / Deploy
SSH host `xpsex` is for **read-only investigation only** (logs, curl). The user syncs files and restarts containers manually. Never rsync or push files to the server.
