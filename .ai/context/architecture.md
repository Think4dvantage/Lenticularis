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
| `station_dedup_overrides` | `id`, `station_id_a`, `station_id_b`, `note`, `created_at` — manually-defined co-location pairs that are merged regardless of GPS distance; pre-seeded with Lehn pair (holfuy-1850 ↔ windline-6116) |
| `user_foehn_configs` | `user_id` PK FK → users, `config_json` (full föhn config blob), `updated_at` — per-user föhn config overrides; when present, replaces the system-wide default for that user |

---

## InfluxDB Measurements

### `weather_data`
- **Tags**: `station_id`, `network`, `canton`
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qfe`, `pressure_qff`, `precipitation`, `snow_depth`

### `weather_forecast`
- **Tags**: `station_id`, `network`, `model` (`icon-ch1` / `icon-ch2` / `open-meteo` / `icon-ch`), `source`, `init_date` (YYYY-MM-DDTHH — one series per model run hour)
- **Timestamp**: `valid_time` (the future moment the forecast is valid for)
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qff`, `precipitation` + optional `_min`/`_max` variants for ensemble spread (SwissMeteo only); `init_time` (ISO string, for Python-side dedup)
- Kept indefinitely — enables forecast-vs-actual accuracy analysis per model run.

### `station_wind_profile`
- **Tags**: `station_id`, `network`, `level_m` (altitude ASL in metres), `init_date` (YYYY-MM-DDTHH)
- **Timestamp**: `valid_time` (UTC)
- **Fields**: `wind_speed`, `wind_speed_min`, `wind_speed_max`, `wind_direction`, `wind_direction_min`, `wind_direction_max`, `vertical_wind`, `vertical_wind_min`, `vertical_wind_max`, `init_time` (ISO string)
- Written by `collectors/forecast_swissmeteo.py` via `influx.write_station_wind_profile()`; altitude bands: 500, 800, 1000, 1500, 2000, 2500, 3000, 4000, 5000 m ASL.

### `wind_forecast_grid`
- **Tags**: `grid_id` (e.g. `"46.00_7.00"`), `level_hpa` (950/900/850/800/750/700/600/500), `init_date` (YYYY-MM-DD)
- **Timestamp**: `valid_time` (UTC)
- **Fields**: `wind_speed` (km/h), `wind_direction` (degrees int), `lat`, `lon`
- Altitude → hPa mapping: 500m→950, 1000m→900, 1500m→850, 2000m→800, 2500m→750, 3000m→700, 4000m→600, 5000m→500 (550hPa is not available in ICON-seamless)
- Written by `collectors/forecast_grid.py` via `influx.write_forecast_grid()`; queried by `influx.query_forecast_grid(start_dt, end_dt, level_hpa)` which deduplicates to latest `init_date` per (`grid_id`, `valid_time`)

### `rule_decisions`
- **Tags**: `launch_site_id`, `ruleset_id`, `owner_id`, `site_type`
- **Fields**: `decision` (green/orange/red), `condition_results` (JSON array), `blocking_conditions` (JSON array of condition IDs)
- Storing `condition_results` per evaluation enables per-condition and per-station trigger statistics without re-querying raw weather data.

---

## API Contracts

### Auth
- `POST /auth/register` — `{username, email, password}` → `{user_id, token}`
- `POST /auth/login` — `{username, password}` → `{access_token, refresh_token}`
- `POST /auth/refresh` — `{refresh_token}` → `{access_token}`

### Stations
- `GET /api/stations` — list all active stations (`?network=&canton=`)
- `GET /api/stations/{station_id}` — station metadata
- `GET /api/stations/{station_id}/latest` — most recent measurement
- `GET /api/stations/{station_id}/history` — `?from=&to=&fields=`
- `GET /api/stations/replay` — `?start=&end=&forecast_hours=&include_forecast=` — all stations over a time window for map replay; **server-side in-memory cache (5 min TTL)** keyed by query params; Flux query uses `aggregateWindow(30m, last)` before `pivot` to reduce row count ~3×
- `GET /api/stations/{station_id}/forecast` — `?hours=N` — forecast points for a station; response includes top-level `forecast_source` (e.g. `"swissmeteo"`) and `forecast_model` (e.g. `"icon-ch"`) derived from the first data row
- `GET /api/stations/{id}/forecast-accuracy` — `?from=&to=` — actuals + per-init_date forecast series

### Launch Sites
- `GET/POST /api/launch-sites`
- `GET/PUT/DELETE /api/launch-sites/{id}`

### Rule Sets
- `GET /api/rulesets` — user's own, `org_id IS NULL` (personal only)
- `POST /api/rulesets` — create with full condition tree; pass `org_slug` to scope to org
- `GET /api/rulesets/{id}` — full rule set including condition tree
- `PUT /api/rulesets/{id}` — replace full condition tree (editor save)
- `DELETE /api/rulesets/{id}`
- `POST /api/rulesets/{id}/evaluate` — evaluate NOW, return decision + per-condition reasoning
- `GET /api/rulesets/{id}/history` — `?hours=N` — from `rule_decisions` InfluxDB
- `GET /api/rulesets/{id}/forecast` — `?hours=N` — declared BEFORE `/{ruleset_id}` to avoid FastAPI route shadowing
- `PUT /api/rulesets/{id}/webcams` — full-replace webcam list
- `PUT /api/rulesets/{id}/set_preset` — admin-only (`?is_preset=bool`)
- `GET /api/rulesets/presets` — all preset rulesets (any pilot)
- `POST /api/rulesets/{id}/publish`, `unpublish`

### Org
- `GET /api/org/{slug}/status` — public traffic light
- `GET /api/org/{slug}/dashboard` — org member: condition breakdown + 24h history
- `GET /api/org/{slug}/rulesets` — org admin

### Gallery
- `GET /api/gallery` — public rule sets (`?q=&sort=clone_count`)
- `POST /api/gallery/{id}/clone`

### Statistics
- `GET /api/stats/{ruleset_id}/flyable-days`
- `GET /api/stats/{ruleset_id}/hourly-pattern`
- `GET /api/stats/{ruleset_id}/monthly`
- `GET /api/stats/{ruleset_id}/seasonal`
- `GET /api/stats/{ruleset_id}/condition-triggers`
- `GET /api/stats/compare?ruleset_ids=1,2,3`
- `GET /api/stats/{ruleset_id}/best-windows`

All time-range endpoints accept `?from=&to=`. Best-windows also accepts `?top_n=5`.

### Föhn
- `GET /api/foehn/status` — evaluate all regions + pressure pairs (live data); pre-fetches historical snapshots for delta conditions
- `GET /api/foehn/forecast?valid_time=` — evaluate from forecast data at a specific valid_time
- `GET /api/foehn/observation?valid_time=` — evaluate from historical observed data
- `GET /api/foehn/history?hours=&center_time=` — hourly QFF pressure per pair station for gradient chart
- `GET /api/foehn/config` — user's saved config (or system default if none)
- `PUT /api/foehn/config?set_as_default=false` — save user's config; `?set_as_default=true` (admin only) also overwrites `data/foehn_config.json`
- `DELETE /api/foehn/config?set_as_default=false` — delete user's override; `?set_as_default=true` (admin only) also resets system default to hardcoded values

### Admin (require_admin)
- `GET/PUT /api/admin/users`
- `GET/PUT /api/admin/collectors`
- `POST /api/admin/collectors/{key}/trigger`
- `GET/POST /api/admin/orgs`
- `GET /api/admin/station-dedup` — list manual dedup pairs
- `POST /api/admin/station-dedup` — add pair `{station_id_a, station_id_b, note?}`; calls `rebuild_display_registry` immediately
- `DELETE /api/admin/station-dedup/{id}` — remove pair; calls `rebuild_display_registry` immediately

### Wind Forecast Grid
- `GET /api/wind-forecast/grid?date=YYYY-MM-DD&level_m=1500` — requires `require_pilot`; maps `level_m` → `level_hpa` via `ALTITUDE_TO_HPA`; returns `{date, level_m, level_hpa, grid:[{lat,lon},...], frames:[{t, ws:[...], wd:[...]},...]}`; `grid` order is canonical (lat desc, lon asc, 171 points); `ws`/`wd` are parallel arrays indexed to `grid`; `null` for missing data

### AI
- `POST /api/ai/suggest-conditions` — Ollama-powered natural-language → condition JSON

### System
- `GET /health`
- `GET /docs` (FastAPI auto-generated Swagger UI)

---

## Rules Engine Design

`rules/evaluator.py`:

1. Load the rule set's condition tree from SQLite (`rule_conditions` + `condition_groups`)
2. For each condition, fetch the **latest measurement** from InfluxDB for `station_id` (and `station_b_id` for `pressure_delta`)
3. Apply operator/value logic to produce a per-condition `result_colour`
4. Walk AND/OR group nesting to combine results within groups
5. Apply `combination_logic` (`worst_wins` or `majority_vote`) across top-level results
6. Return `TrafficLightDecision` including `condition_results` array
7. Write full decision (including `condition_results` JSON) to `rule_decisions` InfluxDB

Station picker is **per condition row** — one rule set can reference any number of different stations. Pressure delta is a first-class condition type with two-station picker.

### Forecast Evaluation

`run_forecast_evaluation(ruleset, influx, horizon_hours=120)` reuses identical logic but iterates over hourly `valid_time` steps from `weather_forecast` instead of latest `weather_data`. Returns `list[ForecastStep]`, does **not** write to InfluxDB.

### Combination Logic

- `worst_wins` (default) — any RED → RED; any ORANGE → ORANGE; else GREEN
- `majority_vote` — most common colour wins

---

## Statistics Design

All metrics computed from `rule_decisions` InfluxDB measurement (not raw `weather_data`).

| Metric | Endpoint |
|---|---|
| Flyable days (≥1 GREEN per calendar day) | `/api/stats/{id}/flyable-days` |
| GREEN % per hour-of-day (0–23) | `/api/stats/{id}/hourly-pattern` |
| GREEN/ORANGE/RED counts per calendar month | `/api/stats/{id}/monthly` |
| Same grouped by meteorological season | `/api/stats/{id}/seasonal` |
| % evaluations where each condition voted non-GREEN | `/api/stats/{id}/condition-triggers` |
| Flyable days side-by-side for ≥2 rulesets | `/api/stats/compare` |
| Top N longest consecutive GREEN streaks | `/api/stats/{id}/best-windows` |

Best-windows is computed server-side (not Flux) for simplicity.

---

## Forecast Accuracy

`query_forecast_accuracy()` in `influx.py` fetches actuals + per-init_date forecast series for a station/window. Handles legacy data (no `init_date` tag) as fallback series.

Frontend: station picker + date picker + per-field Chart.js charts with actual (solid) + one overlaid line per model-run day.

---

## Deployment

### Traefik Label Format

This homelab requires **list format** labels, not map format:

```yaml
# CORRECT
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.myapp.rule=Host(`myapp.lg4.ch`)"
```

When a container is on multiple Docker networks, add `traefik.docker.network=proxy`.

### Healthcheck

`python:3.11-slim` does not include `curl`. Use Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

### Replay Cache Architecture

`api/routers/stations.py` has a module-level `_replay_cache: dict[str, tuple[Any, float]]` (key → payload, monotonic stored_at). TTL is 5 minutes. The cache is warmed at startup via `warm_replay_cache(influx, registry)` — a background `asyncio.Task` that iterates offsets `[1, 0, 2, -1, 3, -2, 4, -3, 5]` sequentially, covering all 9 day-button windows. Core computation is in `_build_replay_payload()` (sync, called via `run_in_executor`). The HTTP endpoint checks the cache first; on miss it calls `_build_replay_payload` and stores the result.

**Cache poisoning guard**: Both `warm_replay_cache` and the `GET /api/stations/replay` endpoint skip writing to the cache when `fc_frame_count == 0` and `include_forecast` is true. This prevents startup warm-up (which runs before the forecast collector's first write) from caching obs-only entries for future-day windows, which would block forecast data for up to 10 min.

**Post-forecast invalidation**: `invalidate_forecast_replay_cache()` removes all cache entries whose key contains `|True|` (forecast-enabled windows). `_patch_scheduler_forecast()` in `main.py` monkey-patches `scheduler._run_forecast_collector` to call `invalidate_forecast_replay_cache()` + fire a new `warm_replay_cache()` background task immediately after each successful forecast run (status == "ok" and measurement_count > 0). This ensures users see the latest model run without waiting for the 5-min TTL.

**Payload fields**: `obs_frame_count`, `fc_frame_count` are included in the JSON payload for client-side console diagnostics (logged by `ReplayEngine._applyJson`).

The frontend mirrors this with a client-side `ReplayEngine._cache` (Map, 10 min TTL) so repeated clicks within a session are instant without any HTTP round-trip. Prefetch fires after `window._stationsReady` resolves, iterating the same offset priority order sequentially via `await _mapReplay.prefetch()`.

---

### Forecast Collector Rate Limiting

`BaseForecastCollector.collect_all_iter` (in `collectors/forecast_base.py`) iterates stations **serially** (concurrency=1). With Open-Meteo's ~7 s API response latency, this gives ~0.14 req/s (~8 req/min) — well within the free-tier rate limits. 475 stations complete in ~55 min, safely within the 60-min collection interval.

`BaseForecastCollector._get` includes **429 retry with backoff**: on HTTP 429, waits 10s, 30s, 60s between successive retries (3 attempts total) before raising. This handles transient rate-limit spikes without silently dropping stations.

**Commercial API key**: when `api_key` is set under any `forecast_collectors[*].config` in `config.yml`, both `ForecastOpenMeteoCollector` and `ForecastGridCollector` switch to `customer-api.open-meteo.com` (unlimited rate limits). The scheduler extracts the key from the first enabled forecast-collector entry with a non-empty key. The grid collector no longer sleeps between batches when running on a paid plan (inter-batch sleep was removed; 429 retry remains as safety net).

---

## Virtual Station Deduplication

`services/dedup.py` — `build_deduped_registry(raw, distance_m=50.0, manual_pairs=None)` → `(display_registry, virtual_members)`.

**Algorithm**:
1. Exclude `foehn` network stations (synthetic pressure-delta stations).
2. Union-find over all eligible stations; union any pair within `distance_m` metres (Haversine).
3. Union any manually-specified pair from `station_dedup_overrides` regardless of distance.
4. For each cluster, pick the canonical station by network priority: meteoswiss > slf > metar > holfuy > windline > ecowitt > wunderground.
5. Canonical station gets `member_ids` populated (canonical first); non-canonical stations are omitted from `display_registry`.

**Runtime state** (on `app.state`):
- `station_registry` — raw dict of all real stations (all collectors write here)
- `display_registry` — deduplicated dict for API responses / map
- `virtual_members` — `{canonical_id: [canonical_id, member_id, ...]}` for multi-member stations
- `dedup_distance_m` — threshold from config (default 50 m)

**WeatherStation model**: `member_ids: list[str]` (empty for single stations).

**Latest data** (`query_latest_virtual`): queries all members, returns highest-timestamp result.

**History data** (`query_history_virtual`): calls `_members_established_for_window` first. A member must have data in the 2 h slice **before** the window opens to be included. This prevents newly-added stations (partial coverage) from producing jagged/overlapping chart lines alongside an established station's full history. Three fallback levels: (1) pre-window established, (2) any in-window, (3) all IDs on error.

**Registry updates**:
- Startup: manual pairs loaded from DB, `build_deduped_registry` called before server opens.
- Scheduler patch (`_patch_scheduler_registry`): after each collect run, if a new station appears, rebuilds dedup registries.
- Admin API: `rebuild_display_registry(app_state)` called immediately on pair add/delete.

**config.yml** key: `station_dedup.distance_m` (default 50).

---

### Föhn Detection Design

`foehn_detection.py` — shared between the scheduler collector and the API router.

**`FoehnCondition`** (`__slots__`): `station_id`, `field`, `operator`, `value_a`, `value_b`, `lookback_h` (optional int: 1/2/3/6), `label`, `result_colour`.

**`FoehnRegion`**: `key`, `label`, `description`, `pressure_pair` (south/north/threshold or None), `conditions: list[FoehnCondition]`. Alias `Region = FoehnRegion` for backward compat with the scheduler.

**`eval_foehn_condition(cond, latest, historical=None)`**: For delta conditions (`lookback_h` set), evaluates `current_field − historical[lookback_h][station][field]` vs threshold. Supports all fields and operators including `between`, `not_between`, `in_direction_range`. Returns rich dict with `actual_value`, `prev_value`, `is_delta`, `met`, `data_available`.

**Delta/trend conditions** solve the "föhn arriving" problem: `humidity Δ2h < −10` fires when humidity drops >10% in 2 hours, even before wind confirms. The API router pre-fetches historical snapshots for each unique `lookback_h` in the config before evaluating.

**Config persistence**: `data/foehn_config.json` for system-wide default; `user_foehn_configs` SQLite table for per-user overrides. `get_foehn_config_dict()` / `set_foehn_config()` / `reset_foehn_config()` manage the file. `_region_from_dict()` handles both new format and legacy `speed_min/dir_low/dir_high` format transparently.

**Virtual foehn stations** (`VIRTUAL_STATIONS` list): `foehn-beo`, `foehn-haslital`, `foehn-wallis`, `foehn-reussthal`, `foehn-rheintal`, `foehn-guggi`, `foehn-overall`. Written to InfluxDB `weather_data` measurement every 10 min by `FoehnCollector` with field `foehn_active` (1.0=active, 0.5=partial, 0.0=inactive, −1.0=no_data). These virtual stations appear in the station registry and can be used in ruleset conditions with field `foehn_active`.

**Ruleset editor integration**: Field `foehn_active` in `FieldName` Literal. When selected, station autocomplete is replaced by a region dropdown + status picker (Active/Partial or active/Inactive); raw operator+value are set behind the scenes (`= 1`, `>= 0.5`, `= 0`). `FIELD_MAP` in `rules/evaluator.py` maps `foehn_active → foehn_active`.

---

### Email Notifications

`utils/mailer.py` — `send_email(cfg, to, subject, body_text, body_html)` sends via `smtplib` STARTTLS. Returns `bool` (errors logged, never raised). Switch providers by changing `smtp:` in config.yml — no code changes needed.

**Config** (`config.yml`):
```yaml
smtp:
  enabled: true/false
  host: smtp.protonmail.ch        # or smtp.resend.com / smtp-relay.brevo.com for prod
  port: 587
  user: you@proton.me
  password: ...
  from_address: ...
  from_name: Lenticularis
  timeout_seconds: 30
```

**Notification logic** (`scheduler.py` → `_maybe_notify`):
- Called after every `write_decision` in `_run_ruleset_evaluator`
- Fires only when: `rs.notify_on` is set AND new decision is in `notify_on` AND decision changed from `rs.last_notified_decision`
- On send: updates `rs.last_notified_decision` and commits to SQLite

**Per-ruleset config**: `notify_on` column (CSV string e.g. `"green"`, `"green,orange"`) set via ruleset editor checkboxes. Exposed in `RuleSetUpdate` / `RuleSetOut` Pydantic models.

---

### Dev Overlay

`docker-compose.dev.yml` extends the base with:
- Live `src/` and `static/` volume mounts (`:ro,z`)
- `proxy` external network + Traefik labels for `lenti-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` — prevents stale `.pyc` files from shadowing volume-mounted sources
