# Feature History & Backlog

## Current Version: v1.18.1 (shipped)

Everything unreleased since v1.17: the security & performance remediation batch, the
Forecast Accuracy Analysis page, the Jungfraubahn collector, the test-harness repair, and
self-hosted frontend libraries + static-asset caching.

### Static Asset Caching + Self-Hosted Libraries (v1.18.1)

| Change | Detail |
|---|---|
| `static/vendor/` — new | Leaflet 1.9.4 (`leaflet.js`, `leaflet.css`) and Chart.js v4 (`chart.umd.min.js`, `chartjs-adapter-date-fns.bundle.min.js`) vendored locally. **All CDN references removed** — no page loads from `unpkg.com` or `cdn.jsdelivr.net` any more. |
| `.gitattributes` | `static/vendor/** -text` — vendored assets must stay byte-exact; never line-ending-normalised. |
| `api/main.py` — CSP tightened | `script-src` / `style-src` reduced to `'self' 'unsafe-inline'`; the `unpkg.com` and `cdn.jsdelivr.net` allowances are gone now that nothing is loaded cross-origin. |
| `api/main.py` — `Cache-Control` on `/static/` | Versioned URLs (`?v=<app-version>`) → `public, max-age=31536000, immutable`. Unversioned hits (locale JSON, ES-module imports) → `public, max-age=600`. |
| `api/routers/pages.py` — cache-busting | Every local `href="/static/…"` / `src="/static/…"` in a page is rewritten at serve time to append `?v=<app-version>`. A version bump busts every asset atomically on deploy. |
| `api/routers/pages.py` — ETag | HTML is served `no-cache` with an ETag (`<version>-<mtime>`) and revalidates to `304`. HTML is re-read per request, so dev volume-mount edits stay live. |
| `tests/backend/test_static_caching.py` | 6 tests: `no-cache` + ETag on HTML, 304 revalidation, assets versioned, no CDN refs remain, immutable on versioned assets, short cache on unversioned, vendored libs actually served. |

Note: Leaflet's default `marker-icon.png` / `layers.png` are **not** vendored, and do not
need to be — every marker in the app uses `L.divIcon` or `L.circleMarker`, and no
`L.control.layers` is used, so those files are never requested.

### Jungfraubahn (JFB) Observation Collector

13 stations in the Jungfrau region from the Jungfraubahn middleware API. No auth, one
request per cycle, 10 min interval. Zero schema change — every kept field maps onto the
existing `WeatherMeasurement`.

| Change | Detail |
|---|---|
| `collectors/jfb.py` — new | `JfbCollector(BaseCollector)`, `NETWORK = "jfb"`. Single JSON call returns all stations. Reuses `to_float` / `normalize_wind_dir` from `collectors/utils.py`. |
| Field mapping | `FF`→`wind_speed`, `G10`→`wind_gust` (both **knots → km/h, ×1.852**), `DIR`→`wind_direction`, `TL`→`temperature`, `RH`→`humidity`, `QFE`→`pressure_qfe`. |
| Dropped params | `TD` / `DIFFTD` (derivable from `TL`+`RH`); `G1h` (1-hour max gust — different semantics from `wind_gust`, which is the 10-min peak everywhere else in the stack). |
| `pressure_qff` left `None` | QFF is **not** derivable from QFE + elevation (that is QNH). Synthesising it would inject several hPa of error at these altitudes — larger than the föhn gradients it would be compared against. Same choice as `fga.py`. |
| New wind stations | `jfb-lauberhorn` (2315 m), `jfb-wengen-dorf` (1278 m), `jfb-wengen-lauberhorn-ziel` (1285 m) — the Wengen/Lauterbrunnen bowl, previously covered only by Jungfraujoch 8 km away. |
| New atmosphere stations | Eiger (3955 m), Mittellegihütte, Hollandiahütte SAC, Kleine Scheidegg, Grütschalp, Grindelwald-Moos, Jungfrau-Ostgrat, Jungfraujoch, Lauterbrunnen-Gässli, Lauterbrunnen-Heliport — an 799 m → 3955 m elevation ladder within ~10 km. |
| Excluded stations | `Interlaken` and `Jungfraujoch-Sphinx` skipped on ingest — exact duplicates of `meteoswiss-INT` / `meteoswiss-JUN` with fewer fields and coordinates rounded to 2–3 decimals (so 50 m proximity dedup would not catch them). |
| `scheduler.py` / `services/dedup.py` | Registered in `_COLLECTOR_REGISTRY`; `"jfb"` appended to `NETWORK_PRIORITY` (lowest — MeteoSwiss wins any future proximity clash). |
| `tests/backend/test_jfb_collector.py` | 15 tests: knots conversion, direction normalisation, dropped params, exclusions, timestamp reconstruction + midnight rollover, staleness skip, `currentDateTime` always sent. |

**API quirks (must not regress):**
- **`currentDateTime` is mandatory.** Called bare, the endpoint returns observations
  ~8 h stale with no error. The collector always sends `?currentDateTime=<now>`.
- `timeUTC` is time-only (`"11:30"`, no date) — reconstructed against the request date,
  with midnight rollover. Readings older than 2 h are skipped.
- **Known upstream data bug:** `Hollandiahütte SAC` declares elevation 3248 m but reports
  ~928 hPa / 23 °C (a ~750 m reading). Their metadata or sensor mapping is wrong. Do not
  build on that station's pressure or temperature.

---

### Backend Test Harness — two broken fixtures fixed

| Change | Detail |
|---|---|
| `tests/backend/conftest.py` — `poolclass=StaticPool` | In-memory SQLite defaulted to `SingletonThreadPool` = one connection (and one empty DB) per thread. `create_all()` only populated the main thread's. Sync deps (`get_current_user`, `require_pilot`, `require_admin`) run in FastAPI's worker threadpool → `no such table: users`. `async def` handlers were unaffected, which made it look arbitrary. |
| `tests/backend/conftest.py` — `app.state` set directly | httpx's `ASGITransport` never emits ASGI lifespan events, so `lifespan_context` never ran and `app.state.influx` was never set → `503 InfluxDB not available`. State is now assigned directly; the no-op lifespan is kept so the real scheduler/InfluxDB startup still cannot fire. |
| `tests/backend/conftest.py` — `FakeInflux.query_latest_all_stations` | Missing stub, previously masked by the 503. |
| `.ai/instructions/06-testing-conventions.md` | Corrected — it documented both broken patterns as the correct approach. |

Backend suite: **59 passed, 0 failed** (was 57 passed, 2 failed).

---

### Forecast Accuracy Analysis Page (NOT YET VERIFIED — awaiting first successful data load)

| Change | Detail |
|---|---|
| `static/forecast-analysis.html` + `forecast-analysis.js` | New `/forecast-analysis` page. Lead-time toggle D+1/D+2/D+3. Per-field ranked tables of worst-forecast stations (MAE + bias + correction hint). Colour-coded MAE cells. Station names link to `/forecast-accuracy?station=X`. |
| `database/influx.py` — `query_forecast_accuracy_ranking` | New method. Queries 90d actuals (aggregateWindow 1h) + forecasts; Python-side join keyed on hourly timestamp; circular error for wind_direction; MAE + bias per station×field×bucket. |
| `database/influx.py` — `_ranking_client` | Third InfluxDB client with `ranking_query_timeout` (default 300 s). Used only for ranking queries — avoids 60 s `_slow_query_api` timeout that was silently killing the actuals query. |
| `config.py` — `ranking_query_timeout` | `InfluxDBConfig` gains `ranking_query_timeout: int = 300000`. |
| `api/routers/stations.py` — `GET /api/stations/forecast-accuracy-ranking` | 30-min server-side cache. `force_refresh=true` param bypasses cache. `warm_accuracy_ranking_cache()` function. |
| `api/main.py` | Startup warm-up task + page route `/forecast-analysis`. |
| `scheduler.py` | 24h `IntervalTrigger` job re-warms ranking cache. |
| All 13 HTML files | Forecast Analysis nav link added. |
| `i18n/en+de+fr+it.json` | `forecast_analysis.*` keys added. |

See `.ai/context/forecast-analysis-wip.md` for full debug history and next steps.

---

### Security & Performance Remediation Batch

23-task security and performance pack (`specs/001-review-remediation/`). All tasks complete.

| Phase | Tasks | Summary |
|---|---|---|
| Security | T01–T05 | Flux injection hardening; JWT fail-closed; webcam URL validation + XSS escaping; security-header + CORS middleware; OAuth tokens out of URL + `email_verified` check |
| Performance | T06–T11 | GZip middleware; async event-loop unblocking in Influx handlers; scheduler writes offloaded via `asyncio.to_thread`; rule evaluator batches Influx fetch; bounded in-memory caches with lock; SQLite WAL + `busy_timeout=30000` |
| Architecture | T12–T19 | RFC 7807 error envelope + global exception handler; pages router extracted from `main.py`; scheduler post-run hooks (replaces monkey-patching); Alembic dependency dropped; version single-sourced; SQLAlchemy 2.0 `select()` style; swallowed-exception fixes; InfluxDB duplicate field key + `_source` dedup no-op fixed |
| Quality | T20–T23 | Pytest harness + GitHub Actions CI; collector `_to_float`/wind-dir/concurrency dedup (`collectors/utils.py`, `base._collect_concurrent`); frontend nav/bootstrap dedup (`static/bootstrap.js`, `static/shared.css`); remaining hardcoded strings → i18n keys |

Key new files: `api/errors.py`, `api/routers/pages.py`, `collectors/utils.py`, `static/bootstrap.js`, `tests/backend/conftest.py + 4 test files`, `.github/workflows/test.yml`.

---

## Previous Version: v1.17 (shipped)

### Replay fix, collector scheduling overhaul, stats table improvements

| Change | Detail |
|---|---|
| `static/index.html` — `tnPlayHours()` | Always returns all 13 hours `[7…19]` for every day offset. Previously returned only `[8,11,14,17]` for offset ≥ 2 (CH2 3-hourly mode). Removed now that lsmfapi delivers hourly 120h. |
| `static/index.html` — `tnStartPlay()` | Frame speed fixed at 600 ms for all days (was 1000 ms for 4-hour mode). |
| `collectors/forecast_swissmeteo.py` — parallel collect | Added `collect_all_iter` override using `asyncio.gather` — all stations fetched in parallel. lsmfapi is co-located, no rate limits. Replaces serial `BaseForecastCollector.collect_all_iter`. |
| `collectors/forecast_base.py` — source override | `__init__` reads optional `source` key from config dict and overrides class-level `SOURCE` tag. Useful if two instances of the same collector class need distinct source tags. |
| `scheduler.py` + `config.py` — `cron_hours` | Added optional `cron_hours: list[int]` to `ForecastCollectorConfig`. When set, uses `CronTrigger(hour=...)` instead of `IntervalTrigger`. Kept as a supported feature but not used in production (hourly interval preferred). |
| Collector scheduling — hourly | Both swissmeteo station collector and wind forecast grid collector changed from cron `04/10/16/22Z` to `IntervalTrigger(minutes=60)`. lsmfapi updates ~4×/day; no-op runs are harmless. |
| `config.yml` / `config.yml.example` | `open-meteo` and `open-meteo-short` set `enabled: false`. `swissmeteo` uses `interval_minutes: 60`, `cron_hours` removed. |
| `database/influx.py` — `write_forecast_grid` chunked | Grid write now batches at 5000 pts per InfluxDB call (was one call for all points). Fixes read-timeout crash when writing 1.17M points (~234 chunks × 5k pts). |
| `static/stats.html` — collector table | Added "Records" column (`last_measurement_count` — points written in last run, e.g. `243 pts`). Fixed "Schedule" column: shows `04/10/16/22Z` for cron collectors, `N min` for interval. i18n keys `col_interval` renamed to "Schedule"/"Zeitplan"/etc.; `col_records` added to all 4 locales. |

---

## Previous Version: v1.16 (shipped)

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

### Thermal Forecast (lsmfapi thermal-grid endpoint — ready to integrate)

Full API spec + TypeScript types + fetch helpers in `.ai/context/lsmfapi-thermal-grid.md`.

Key fields: `solar` (W/m²), `lcl` (cloud base m ASL), `lfc`, `freezing_level`, `cape`, `cin`, `cloud_cover`, `tke`, `sunshine`. All with ensemble `_min`/`_max`. 120-hour horizon, ~4×/day refresh. Default `stride_km=10` (~200 pts over Switzerland, ~0.5 MB uncompressed).

Candidate integration surfaces:
- **Thermal map page** (`/thermal`) — grid overlay coloured by `solar` or `lcl`, time-nav controls (same pattern as `/wind-forecast`)
- **Station-detail thermal panel** — cloud base, freezing level, CAPE, sunshine below wind chart; nearest grid point via `nearestGridIndex`
- **Ruleset conditions** — `lcl > X`, `cape < Y`, `cloud_cover < Z` — requires thermal fields in InfluxDB
- **Thermal suitability badge** — green/orange/red on map popups

Implementation note: confirm schema stability with lsmfapi owner before writing to InfluxDB.

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
