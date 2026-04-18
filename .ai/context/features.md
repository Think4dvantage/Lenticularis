# Feature History & Backlog

## Current Version: v1.15.1 (shipped)

### Incremental improvements

| Change | Detail |
|---|---|
| Wind forecast grid — humidity | `GridForecastPoint` gains `humidity: Optional[float]`. Collector fetches `relative_humidity_<N>hPa` from Open-Meteo alongside wind vars. InfluxDB `wind_forecast_grid` stores `humidity` field. API frames include `rh` parallel array. Frontend: cloud icon on arrows when `rh ≥ 90` (same `_cloudSvg` pattern as station markers); arrows clickable with popup showing lat/lon, ws, wd, rh. |
| Station-detail tooltip — zone-aware filter | Replaced `mode: 'index'` with `mode: 'x'` in `CHART_DEFAULTS` — eliminates cross-dataset index bleeding between obs (10-min) and forecast (1-hour) arrays. `makeForecastFilter` hides obs items in the forecast zone and forecast items in the obs zone. Precipitation forecast datasets tagged `isForecast: true`. |
| Station-detail tooltip — ensemble min/max | `(min)` and `(max)` band anchor datasets filtered out of tooltip; their values appended inline on the probable line: `Forecast QFF: 1012.1  [1008.2 – 1015.8]`. Lookup uses timestamp matching (`new Date(p.x).getTime() === item.parsed.x`) rather than `dataIndex` — necessary because `buildFields` skips null rows so min/max arrays are shorter than the probable array. |
| `.ai/` blueprint sync | Added `instructions/00-ai-usage.md`, `05-user-profile.md`, `06-testing-conventions.md`, `07-api-conventions.md`, `08-operability.md`; added 13 prompt files (`specify`, `clarify`, `plan`, `checklist`, `tasks`, `analyze`, `implement`, `fix-bug`, `architect`, `sync`, `taskstoissues`, `update-readme`, `update-blueprint`). `04-constraints.md` gains "AI Files" section. `CLAUDE.md` removed. |

---

## Previous Version: v1.15 (shipped)

### SwissMeteo Forecast Integration

| Change | Detail |
|---|---|
| `collectors/forecast_swissmeteo.py` | New `ForecastSwissMeteoCollector(BaseForecastCollector)`: SOURCE="swissmeteo", MODEL="icon-ch". `collect_for_station()` calls `GET /api/forecast/station` on `lsmfapi-dev.lg4.ch` — fills full `ForecastPoint` including ensemble min/max. `collect_altitude_for_station()` calls `GET /api/forecast/altitude-winds` — returns `list[StationWindProfilePoint]`. Registered in `_FORECAST_REGISTRY`; companion `forecast_swissmeteo_altitude` APScheduler job fires at same interval. |
| `models/weather.py` — ensemble fields | `ForecastPoint` extended with optional `wind_speed_min/max`, `wind_gust_min/max`, `wind_direction_min/max`, `temperature_min/max`, `humidity_min/max`, `pressure_qff_min/max`, `precipitation_min/max`. New `StationWindProfilePoint` model with `station_id`, `network`, `level_m`, `init_time`, `valid_time`, full wind ensemble fields. |
| InfluxDB `weather_forecast` — `init_date` | Tag format changed from `YYYY-MM-DD` to `YYYY-MM-DDTHH` so each model-run hour is a distinct series (prevents afternoon run from overwriting morning's). |
| InfluxDB `station_wind_profile` | New measurement written by SwissMeteo altitude collector. Tags: `station_id`, `network`, `level_m`, `init_date` (YYYY-MM-DDTHH). Fields: `wind_speed`, `wind_speed_min/max`, `wind_direction`, `wind_direction_min/max`, `vertical_wind`, `vertical_wind_min/max`, `init_time`. |
| Source priority + 24h fallback | `query_forecast_for_stations()` dedup: prefer `swissmeteo` when its `init_time` is ≤24h old; if stale, fall back to latest-init-time-wins across sources. `_PREFERRED_SOURCE = "swissmeteo"`, `_PREFERRED_MAX_AGE_H = 24`. |
| Forecast source badge | `GET /api/stations/{id}/forecast` response gains top-level `forecast_source` and `forecast_model` fields. `station-detail.js`: `renderForecastSourceBadge()` shows a pill badge next to the Forecast button. |
| Ensemble band charts | Chart.js 3-dataset pattern: invisible min anchor → semi-transparent max fill (dashed border) → solid probable line; all three tagged `isForecast: true`. Tooltip `filter` shows only forecast items when forecast data is present. Legend hides `(min)` anchor entries. |
| Wind rose legend fix | PolarArea `generateLabels` override produces one entry per dataset (not per sector). |
| Chart colors | `wind_gust: '#fc8181'` (red), `wind_direction: '#4fd1c5'` (teal), `FC_WIND_COLOR = '#9f7aea'` (purple), `FC_GUST_COLOR = '#ecc94b'` (yellow). |

---

## Previous Version: v1.14.1 (shipped)

### Hotfix / Cleanup

| Change | Detail |
|---|---|
| QNH → QFF migration | `pressure_qnh` removed from entire stack. All pressure now stored and displayed as `pressure_qff`. Affects: `WeatherMeasurement` model, `ForecastPoint` model, all 8 collectors, `influx.py` (write + 3 query methods), `rules/evaluator.py` FIELD_MAP, `weather_stats.py`, `app.js`, `map.js`, `station-detail.js`, `forecast-accuracy.js`, `stats.html`, `stations.html`, all 4 i18n locales, `config.yml.example` (MeteoSwiss QNH URL removed). i18n key `map.popup.qnh` renamed to `map.popup.qff`. |

---

## Previous Version: v1.14.0 (shipped)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| v0.1 | MeteoSwiss collector + InfluxDB write pipeline + station API + station-detail chart page |
| v0.2 | Leaflet.js map as landing page with station markers and latest-measurement popups |
| v0.3 | JWT register/login, `get_current_user`/`require_admin`, Google OAuth, SQLite via SQLAlchemy |
| v0.4 | Pilot-owned launch site CRUD; site markers on map with distinct icon |
| v0.5 | `collectors/slf.py` (30 min) and `collectors/metar.py` (15 min, AviationWeather); full scheduler |
| v0.6 | `static/ruleset-editor.html` condition builder: per-row station picker, field/operator/value, AND/OR nesting, direction compass, pressure-delta two-station mode, live preview, save |
| v0.7 | `rules/evaluator.py` live evaluator + `run_forecast_evaluation`; traffic light badges on map; ruleset card list with live badge |
| v0.8 | `collectors/forecast_meteoswiss.py` ICON-CH1/CH2 GRIB2 pipeline; map time-navigation (day buttons, hour slider); forecast colour-strip on ruleset cards; `ruleset-analysis.html` |
| v0.9 | `stats.html` flyability statistics (hourly heatmap, monthly breakdown, seasonal, condition trigger leaderboard, site comparison, best windows) |
| v0.10 | `collectors/wunderground.py` personal weather station collector; virtual föhn stations (N–S pressure delta pairs); `foehn.html` föhn dashboard; personal-station toggle on map |
| v1.0 | Multilanguage EN/DE/FR/IT + mobile-responsive UI (hamburger nav, `shared.css`, grid fixes) |
| v1.1 | Admin panel (`admin.html`): user management, collector control, Föhn config editor; `customer` role |
| v1.2 | Webcam links (per-ruleset); preset launch sites (admin-curated templates); decision history + forecast API; map landing-ring fix |
| v1.3 | Forecast accuracy dashboard (`forecast-accuracy.html`); `init_date` tag on `weather_forecast`; layered forecast schedule (short 3h / extended daily); `collect_all_iter` rate-limit spreading |
| v1.4 | Opportunity site type (diamond marker ✦); AI rule suggestions via Ollama (`POST /api/ai/suggest-conditions`) |
| v1.5 | Multi-tenant org system: `Organization` model, `org_admin`/`org_pilot` roles, subdomain routing (`vkpi.lenti.cloud`), org-dashboard, org-scoped editor |
| v1.6 | Help/FAQ page (`/help`, 12 accordion sections); AI input normaliser (regex wind-term → degrees); fuzzy station name matching; geographic station lookup by location name; `GET /api/rulesets` org isolation fix |
| v1.7 | Holfuy collector (`collectors/holfuy.py`, API-key auth, `{"measurements":[...]}` envelope); forecast replay prefetch cache (`ReplayEngine._cache`, TTL 10 min, `prefetch()` with AbortSignal); map wind-arrow lazy-popup fix (`window.t is not a function`); prefetch abort on page unload |
| v1.8 | Replay performance: server-side in-memory TTL cache (5 min) in `/api/stations/replay`; startup background warm-up of all 9 day-button windows; `aggregateWindow(30m, last)` before pivot reduces observation rows ~3×; removed redundant `sort()` from Flux queries; frontend prefetch expanded to all 9 offsets in outward-from-today order; browser console logging throughout |
| v1.9 | Virtual weather stations: co-located station deduplication via union-find clustering (`services/dedup.py`); 50 m GPS proximity threshold + manual admin overrides (`station_dedup_overrides` SQLite table); `display_registry` + `virtual_members` in app state; highest-priority network wins canonical metadata; newest-wins for latest data across all members; history filtered to established members only (pre-window 2 h data check prevents partial-coverage overlap); Lehn pair pre-seeded (holfuy-1850 ↔ windline-6116); admin Station Dedup tab with add/delete UI |
| v1.10 | Forecast replay cache correctness: `_patch_scheduler_forecast` monkey-patch in `main.py` fires `invalidate_forecast_replay_cache()` + background `warm_replay_cache()` after each successful forecast collector run so replay windows always serve the latest model run; startup cache-poisoning guard skips caching when `fc_frame_count == 0` (prevents obs-only entries locking out forecast data for 5–10 min after startup); `obs_frame_count`/`fc_frame_count` fields added to replay payload for client diagnostics; `_tnOffset === 0` falsy bug fixed in hour-seek logic (`else if (_tnOffset != null)`); `collect_all_iter` rewritten from serial-with-per-station-sleep to serial (concurrency=1) with 429 retry/backoff (10s→30s→60s) in `_get` — eliminates Open-Meteo rate-limit errors that were causing ~45% station failures; comprehensive `[Lenti:replay]` browser console logging throughout |
| v1.11 | Google OAuth login; opportunity ruleset fix; weather-agnostic UI |
| v1.12 | Rules Engine improvements: (1) missing i18n translations fixed (editor.opportunity_btn, editor.ai_btn, editor.opportunity_site + 11 more); (2) UTC → local time conversion across all pages; (3) AM/PM → 24h format app-wide; (4) historical backtester in ruleset editor (datetime picker, evaluate against past weather); (5) immediate evaluation on ruleset save (map badge populated instantly); (6) 30-day decision history backfill on first save (background task); (7) green dot fix for non-triggered conditions in analysis status column; (8) Chart.js 24h format fix (`time.displayFormats`); (9) email notifications — per-ruleset opt-in (green/orange/red), state-change only, SMTP via Proton Mail (dev) / Resend (prod); `SmtpConfig` in config.yml, `utils/mailer.py`, `_maybe_notify` in scheduler, notify_on + last_notified_decision columns on rulesets; `[Lenti:analysis]` console logging throughout |
| v1.14.0 | Ruleset Gallery + FGA collector: (1) Ruleset sharing — `is_public`, `clone_count`, `cloned_from_id`, `description` columns on `RuleSet`; `GET /api/rulesets/gallery` returns all public rulesets ordered by clone count, with `owner_display_name` via JOIN; `POST /api/rulesets/{id}/clone` increments `clone_count`, deep-copies conditions; description textarea in editor (visible when public toggle is on); gallery tab in `rulesets.html` with lazy load, search (name/description/owner), clone button, owner attribution; tab hash routing (`#gallery`); i18n keys in all 4 locales; Pydantic v2 `model_copy(update=…)` pattern. (2) FGA / Meteo Oberwallis collector — `collectors/fga.py` fetches live XML from `https://meteo-oberwallis.ch/wetter/{path}/daten.xml` for 9 stations in Valais (Fieschertal, Bellwald Fleschen, Ried-Brig, Chaeserstatt, Jeizinen, Grimselpass, Hohbiel, Rothorli, Klaena); Type 1 stations have DMS coordinates extracted from nested XML (`46° 24.57' N` format), Type 2 stations have hardcoded coords; staleness guard skips measurements with timestamp >2h old (handles offline stations returning stale 2022 data); pressure captured as `pressure_qfe`; registered as `"fga"` in `_COLLECTOR_REGISTRY`; config.yml.example entry; `stations.html` filter option + yellow CSS badge; fixed ecowitt CSS class typo and added missing wunderground badge. |
| v1.14 | Wind Forecast Grid Map: `static/wind-forecast.html` — dedicated Leaflet page (OpenTopoMap) showing ICON-seamless hourly wind forecasts as coloured SVG arrows on a 0.25° geographic grid (171 points, Switzerland). Right sidebar: 8 altitude buttons (500m–5000m ASL). Bottom bar: 5 day buttons (Tomorrow → +5 Days) + 13 hour buttons (07:00–19:00 CH local time) + Play/Stop (1.5s/step). `collectors/forecast_grid.py` — standalone batch collector; 4 Open-Meteo API calls (comma-separated lat/lon arrays) instead of 171 sequential calls; 8 pressure-level variable pairs (950–500 hPa); runs every 3h, jittered startup. `models/weather.py` — `GridForecastPoint` + `ALTITUDE_TO_HPA` dict (500m→950hPa … 5000m→500hPa; note: 550hPa not available in ICON-seamless, so 5000m maps to 500hPa). `database/influx.py` — `write_forecast_grid()` + `query_forecast_grid()` on new `wind_forecast_grid` measurement. `api/routers/wind_forecast.py` — `GET /api/wind-forecast/grid?date=YYYY-MM-DD&level_m=1500`; canonical grid order (lat desc, lon asc); compact response (`grid[]` + `frames[{t, ws[], wd[]}]`). Commercial Open-Meteo API key support: per-station collector (`forecast_openmeteo.py`) + grid collector both switch to `customer-api.open-meteo.com` when `api_key` set in config; scheduler extracts key from first enabled forecast-collector config entry. |
| v1.13 | Föhn Tracker rework + ruleset editor föhn condition: (1) `foehn_detection.py` fully rewritten — `FoehnCondition` class (field/operator/value/lookback_h delta support), `FoehnRegion` class, `_wc()` helper, `eval_foehn_condition()` with delta evaluation against historical snapshots, `get_required_lookback_hours()`, `get_all_pressure_pairs_from_config()`; (2) `foehn.html` rebuilt with two-tab layout (Status + My Setup), per-user region editor, region/condition builder, global pressure pairs, pressure-gradient line chart (one line per pair + ±4 hPa threshold lines), admin "Set as system default" checkbox + "Reset to hardcoded" button; (3) `PUT /api/foehn/config?set_as_default=true` admin override; `DELETE /api/foehn/config?set_as_default=true` system reset; historical snapshot pre-fetch in `/api/foehn/status` for delta conditions; (4) admin föhn panel removed from `admin.html` and `admin.py`; (5) `UserFoehnConfig` SQLite table for per-user config overrides; (6) ruleset editor gains `foehn_active` field — when selected, station autocomplete replaced by region dropdown (BEO Föhn / Haslital / Wallis / Reussthal / Rheintal / Guggiföhn / Overall) + status picker (Active / Partial or active / Inactive) with no raw operator/value inputs; `foehn_active` added to `FieldName` Literal in `models/rules.py`; (7) `foehn.html` My Setup tab: eager preload on boot for logged-in users, `loadStations()` promise-cache to deduplicate concurrent calls, `_setupLoading` guard, "Loading your setup…" indicator |

---

## Backlog (unordered)

### VKPI Safetychat Replacement (high priority)

Replaces WhatsApp-based go/no-go coordination for VKPI commercial tandem operators.

- **TIMEOUT button** — org member triggers; requires reason (free text or quick-pick: Outflow / Wind / Front approaching / Landing turbulence); push notification to all org members; stored with timestamp + caller.
- **In-app voting** — 10-minute window after TIMEOUT; each daily lead pilot casts one vote (Stop / Continue with caution); auto-tally at 10 min (tie = Red); result + vote record stored permanently.
  - New tables: `org_timeouts` (`id`, `org_id`, `called_by`, `reason`, `called_at`, `voting_closes_at`, `outcome`, `weather_snapshot_json`); `org_timeout_votes` (`id`, `timeout_id`, `company_id`, `pilot_id`, `vote`, `cast_at`)
- **Daily lead pilot designation** — mark self as daily lead for the day; only lead can cast company vote in TIMEOUT; visible on org dashboard.
- **Automatic TIMEOUT suggestion** — when Green → Orange/Red transition detected on any org ruleset, surface a "⚠ Conditions changed — call TIMEOUT?" prompt.
- **Resumption tracking** — after Red decision, 30-minute countdown; push notification when conditions recover to Green.
- **Decision audit log** — every TIMEOUT with caller, reason, live weather snapshot, per-company votes, outcome, resumption timestamp. Exportable as CSV. Endpoint: `GET /api/org/{slug}/timeouts?from=&to=`.
- **Company layer within org** — lightweight grouping (e.g. "Air Taxi Interlaken"); one-vote-per-company in TIMEOUT; shown on dashboard.
  - New table: `org_companies` (`id`, `org_id`, `name`); `company_id` FK on `users`.

### Platform Features

- **Org statistics page** — per-org flyability stats aggregated across all org rulesets; `/org/{slug}/stats` for org members.
- **Customer role scoped access** — customer users see only rulesets explicitly assigned by admin; no rule editing; read-only analysis + map.
- **Trusted users + field condition reports** — `is_trusted` boolean on `User`; trusted pilots submit on-site reports; `weather_reports` SQLite table; `POST /api/reports` + `GET /api/reports?lat=&lon=&radius_km=&hours=`; report pins on map.
- **AI weather analysis** — scheduled job (Ollama/Claude); compares trusted-user reports vs nearest station measurements; flags discrepancies; `ai_insights` table; optional map overlay.
- **Push notifications (FCM)** — `fcm_tokens` table; `POST /api/notifications/fcm-register`; `services/push_fcm.py` dispatches on ruleset status transitions.
- ~~**Email alerts**~~ — shipped in v1.12. Per-ruleset opt-in (green/orange/red checkboxes in editor), state-change only. Pushover not implemented.
- **Flutter mobile app** — separate repo `lenticularis-app`; screens: Map, My Sites, Stations, Föhn, Report conditions (GPS auto-fill), Admin.
- **OGN live glider overlay** — toggleable Leaflet layer with live glider positions from OGN APRS feed; backend WebSocket proxy `/api/ogn/stream` filtered to Swiss bounding box.
- **OGN launch statistics** — detect takeoffs from OGN tracks near known launch coordinates; store daily takeoff counts in `ogn_takeoffs` InfluxDB.
- **xcontest correlation** — correlate xcontest.org flight dates near a site with ruleset decision history; "rule accuracy" card on `ruleset-analysis.html`.
- **Club area overlay** — toggleable GeoJSON polygon layer; admin UI to upload/edit.
- ~~**Duplicate station handling**~~ — shipped in v1.9 as virtual station dedup.
- **Wind rose chart** — replace direction scatter on station-detail with proper wind rose.
- **Additional collectors** — Windline (API key); ~~FGA/Meteo Oberwallis~~ shipped in v1.14.0.
- **Performance pass** — InfluxDB query profiling; downsampling for data older than 90 days.
- **Auto-clone preset on nearby site creation** — when pilot creates a new ruleset near a known preset coordinate, offer to auto-apply that preset.
