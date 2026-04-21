# Feature History & Backlog

## Current Version: v1.16 (shipped)

### SwissMeteo lsmfapi Integration — Full Stack Fix

| Change | Detail |
|---|---|
| `collectors/forecast_swissmeteo.py` — parser fix | lsmfapi response schema corrected: `generated_at` → `init_time`, `hours[]` → `forecast[]`, EnsembleValue dicts → flat float fields with `_min`/`_max` suffixes. `_probable()`/`_ens()` helpers removed. Helpers `_f()` / `_i()` read flat fields directly. `collect_altitude_for_station()` method **removed** — lsmfapi has no per-station altitude endpoint. |
| `collectors/forecast_grid_swissmeteo.py` — new | `ForecastGridSwissMeteoCollector`: fetches lsmfapi `/api/forecast/grid?level_m=X` for all 8 altitude levels **in parallel** (no rate limiting — same Docker network). 1272 ICON-CH1 grid points; `grid_id = f"{lat:.4f}_{lon:.4f}"` (4 decimal places for non-round native coordinates). Falls back to Open-Meteo grid only when lsmfapi returns 0 wind points. |
| `scheduler.py` — grid collector | `_run_grid_forecast_collector` tries `ForecastGridSwissMeteoCollector` first; fallback to `ForecastGridCollector` (Open-Meteo). Logs source used (`source: swissmeteo` or `source: open-meteo`). |
| `scheduler.py` — altitude collector removed | `_run_swissmeteo_altitude_collector` and the companion APScheduler job registration removed. lsmfapi has no per-station altitude endpoint; altitude wind data comes from the grid map. |
| `scheduler.py` — status fix | `_run_forecast_collector`: when `total_points == 0` and `errors > 0`, status is `error` (not `ok_no_data`). `ok_no_data` reserved for no eligible stations. |
| `models/weather.py` | `StationWindProfilePoint` class **removed** — measurement no longer written or queried. |
| `database/influx.py` — dead code removed | `MEASUREMENT_WIND_PROFILE` constant and `write_station_wind_profile()` method removed. |
| `database/influx.py` — dual InfluxDB clients | `InfluxClient` now has `_query_api` (10s timeout, standard queries) and `_slow_query_api` (60s timeout, `query_forecast_replay` only). Configured by new `InfluxDBConfig.slow_query_timeout` (default 60000 ms). |
| `database/influx.py` — `query_forecast_replay` two-step | Replaced slow full-scan + Flux `group→sort→limit` with two-step approach: (1) `_latest_forecast_init_dates()` — fast scan of `range(-12h)`, single field, returns `{source: latest_init_date}` in milliseconds; (2) main pivot filtered to exact `init_date` per source — processes only 1–2 model runs instead of 72+. Cuts query time from 62 s timeout to ~1–2 s. |
| `database/influx.py` — `query_forecast_replay` field filter | Excludes `_min`/`_max` and `init_time` fields — map replay only needs central values. Cuts data volume ~3× vs full SwissMeteo schema. |
| `database/influx.py` — `query_forecast_replay` pivot fix | Added `init_date` to pivot `rowKey` (was missing, causing Flux to silently drop rows when multiple model runs had the same station/valid_time). |
| `database/influx.py` — `query_forecast_snapshot_for_stations` | Added `source` and `init_date` to pivot `rowKey` (same structural fix). |
| `api/routers/wind_forecast.py` — dynamic grid | Router no longer imports or uses `GRID_POINTS` from `forecast_grid.py`. Grid is built dynamically from whatever `grid_id` tags are in InfluxDB — works for both Open-Meteo (171 pts, 2 dp) and lsmfapi (1272 pts, 4 dp) without code changes. |
| `config.py` | `InfluxDBConfig` gains `slow_query_timeout: int = 60000`. |

---

## Previous Version: v1.15.1 (shipped)

### Incremental improvements

| Change | Detail |
|---|---|
| Wind forecast grid — humidity | `GridForecastPoint` gains `humidity: Optional[float]`. Collector fetches `relative_humidity_<N>hPa` from Open-Meteo alongside wind vars. InfluxDB `wind_forecast_grid` stores `humidity` field. API frames include `rh` parallel array. Frontend: cloud icon on arrows when `rh ≥ 90`; arrows clickable with popup showing lat/lon, ws, wd, rh. |
| Station-detail tooltip — zone-aware filter | Replaced `mode: 'index'` with `mode: 'x'` in `CHART_DEFAULTS`. `makeForecastFilter` hides obs items in forecast zone and vice versa. |
| Station-detail tooltip — ensemble min/max | `(min)`/`(max)` band anchors filtered from tooltip; values appended inline on probable line. Lookup by timestamp match, not `dataIndex`. |

---

## Previous Version: v1.15 (shipped)

### SwissMeteo Forecast Integration

| Change | Detail |
|---|---|
| `collectors/forecast_swissmeteo.py` | `ForecastSwissMeteoCollector(BaseForecastCollector)`: SOURCE="swissmeteo", MODEL="icon-ch". Calls lsmfapi per station. Full ensemble `_min`/`_max` fields on `ForecastPoint`. |
| `models/weather.py` — ensemble fields | `ForecastPoint` extended with optional `_min`/`_max` for all weather fields. |
| InfluxDB `weather_forecast` — `init_date` | Tag format `YYYY-MM-DDTHH` — one series per model-run hour. |
| Source priority + 24h fallback | `query_forecast_for_stations()`: prefer `swissmeteo` when ≤24h old; else latest-init-date wins. |
| Forecast source badge | `GET /api/stations/{id}/forecast` gains `forecast_source` + `forecast_model`. Station-detail shows pill badge. |
| Ensemble band charts | 3-dataset Chart.js pattern: invisible min anchor → semi-transparent max fill → solid probable line. |

---

## Previous Version: v1.14.1 (shipped)

### Hotfix

| Change | Detail |
|---|---|
| QNH → QFF migration | `pressure_qnh` removed entire stack. All pressure as `pressure_qff`. Affects models, collectors, influx, evaluator, frontend, i18n. |

---

## Previous Version: v1.14.0 (shipped)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| v0.1 | MeteoSwiss collector + InfluxDB write pipeline + station API + station-detail chart page |
| v0.2 | Leaflet.js map with station markers and latest-measurement popups |
| v0.3 | JWT register/login, `get_current_user`/`require_admin`, Google OAuth, SQLite via SQLAlchemy |
| v0.4 | Pilot-owned launch site CRUD; site markers on map |
| v0.5 | `collectors/slf.py` (30 min) and `collectors/metar.py` (15 min); full scheduler |
| v0.6 | Ruleset editor — condition builder, AND/OR nesting, direction compass, pressure-delta two-station mode |
| v0.7 | `rules/evaluator.py` live evaluator + `run_forecast_evaluation`; traffic light badges |
| v0.8 | `collectors/forecast_meteoswiss.py` ICON-CH1/CH2 GRIB2; map time-navigation; forecast colour-strip |
| v0.9 | `stats.html` flyability statistics |
| v0.10 | `collectors/wunderground.py`; virtual föhn stations; `foehn.html` |
| v1.0 | Multilanguage EN/DE/FR/IT + mobile-responsive UI |
| v1.1 | Admin panel; collector control; customer role |
| v1.2 | Webcam links; preset launch sites; decision history + forecast API |
| v1.3 | Forecast accuracy dashboard; `init_date` tag; layered forecast schedule; `collect_all_iter` rate-limit spreading |
| v1.4 | Opportunity site type; AI rule suggestions via Ollama |
| v1.5 | Multi-tenant org system; subdomain routing; org-dashboard |
| v1.6 | Help/FAQ page; AI input normaliser; fuzzy station matching; geographic station lookup |
| v1.7 | Holfuy collector; forecast replay prefetch cache (client-side TTL 10 min) |
| v1.8 | Replay performance: server-side in-memory TTL cache (5 min); startup warm-up; `aggregateWindow(30m)` |
| v1.9 | Virtual station deduplication (union-find, 50 m GPS, manual overrides, `display_registry`) |
| v1.10 | Replay cache correctness: `_patch_scheduler_forecast` post-collection invalidation + rewarm; cache-poisoning guard |
| v1.11 | Google OAuth login; opportunity ruleset fix |
| v1.12 | Rules engine improvements; UTC→local time; backtester; 30-day backfill; email notifications |
| v1.13 | Föhn Tracker rework; delta/trend conditions; per-user config; `foehn_active` field in ruleset editor |
| v1.14.0 | Ruleset Gallery + FGA/Meteo Oberwallis collector (9 stations, DMS coordinates, XML) |
| v1.14 | Wind Forecast Grid Map (`wind-forecast.html`); `ForecastGridCollector`; `GridForecastPoint`; `GET /api/wind-forecast/grid`; commercial Open-Meteo API key support |

---

## Backlog (unordered)

### VKPI Safetychat Replacement (high priority)

Replaces WhatsApp-based go/no-go coordination for VKPI commercial tandem operators.

- **TIMEOUT button** — org member triggers; reason (free text or quick-pick); push notification to all org members.
- **In-app voting** — 10-min window; each daily lead pilot votes (Stop / Continue with caution); auto-tally.
  - Tables: `org_timeouts`, `org_timeout_votes`
- **Daily lead pilot designation** — mark self as daily lead; only lead casts company vote.
- **Automatic TIMEOUT suggestion** — Green → Orange/Red transition surfaces prompt.
- **Resumption tracking** — 30-min countdown after Red; push when conditions recover.
- **Decision audit log** — exportable CSV. `GET /api/org/{slug}/timeouts`.
- **Company layer** — `org_companies` table; `company_id` FK on users.

### Platform Features

- **Org statistics page** — `/org/{slug}/stats`
- **Customer role scoped access** — read-only rulesets assigned by admin
- **Trusted users + field condition reports** — `is_trusted`, `weather_reports` table, map pins
- **AI weather analysis** — Ollama/Claude compares reports vs station data; `ai_insights` table
- **Push notifications (FCM)** — `fcm_tokens` table; `services/push_fcm.py`
- ~~**Email alerts**~~ — shipped in v1.12
- **Flutter mobile app** — separate repo `lenticularis-app`
- **OGN live glider overlay** — toggleable Leaflet layer, WebSocket proxy
- **OGN launch statistics** — detect takeoffs from OGN tracks
- **xcontest correlation** — correlate flight dates with ruleset decision history
- **Club area overlay** — toggleable GeoJSON polygon layer
- ~~**Duplicate station handling**~~ — shipped in v1.9
- **Wind rose chart** — replace direction scatter on station-detail
- **Performance pass** — InfluxDB query profiling; downsampling for data >90 days
- **Auto-clone preset on nearby site creation**
- **lsmfapi grid — add `ws_min`/`ws_max`, `wd_min`/`wd_max`, `vw`/`vw_min`/`vw_max`** — extend `/api/forecast/grid` response with ensemble spread + vertical wind so the wind forecast map can show ensemble bands and vertical wind component
