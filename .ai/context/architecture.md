# Architecture Reference

## SQLite Tables

Source of truth: `database/models.py`. **These ten tables are all of them.**

| Table | Key columns |
|---|---|
| `organizations` | `id`, `slug` (unique, indexed), `name`, `description`, `created_at` |
| `users` | `id`, `email` (unique, indexed), `display_name`, `hashed_password` (**nullable** — NULL for social-login-only accounts), `role`, `is_active`, `org_id` FK → organizations (SET NULL), `created_at`, `updated_at` |
| `oauth_identities` | `id`, `user_id` FK → users (CASCADE), `provider` (`google`/`github`), `provider_user_id`, `provider_email`, `created_at`; UNIQUE(`provider`, `provider_user_id`) |
| `rulesets` | `id`, `owner_id` FK → users (CASCADE, indexed), `name`, `description`, **`lat`, `lon`, `altitude_m`** (site identity is embedded here), `site_type` (launch/landing/opportunity), `combination_logic`, `is_public`, `is_preset`, **`is_showcase`**, `clone_count`, `cloned_from_id` FK → rulesets (SET NULL), `notify_on` (nullable CSV of colours e.g. `"green,orange"`), `last_notified_decision`, `org_id` FK → organizations (SET NULL), `created_at`, `updated_at` |
| `rule_conditions` | `id`, `ruleset_id` FK → rulesets (CASCADE, indexed), `group_id` (nullable → `condition_groups.id`), `station_id`, `station_b_id` (nullable — `pressure_delta` only), `field`, `operator`, `value_a`, `value_b` (nullable), `result_colour`, `sort_order` |
| `condition_groups` | `id`, `ruleset_id` FK → rulesets (CASCADE, indexed), `name` (**nullable** — NULL = never named), `sort_order`. Added v1.19.0 |
| `launch_landing_links` | `id`, `launch_ruleset_id` FK → rulesets (CASCADE, indexed), `landing_ruleset_id` FK → rulesets (CASCADE); UNIQUE(pair). Many-to-many; meaningful only when `site_type == "launch"` |
| `ruleset_webcams` | `id`, `ruleset_id` FK → rulesets (CASCADE, indexed), `url`, `label`, `sort_order` |
| `station_dedup_overrides` | `id`, `station_id_a`, `station_id_b`, `note`, `created_at` — manually-defined co-location pairs |
| `user_foehn_configs` | `user_id` PK FK → users (CASCADE), `config_json` (full föhn config blob), `updated_at` |

### The three publish/curate flags on `rulesets` are independent

| Flag | Whose decision | Means |
|---|---|---|
| `is_public` | **Owner** | Published — visible in the gallery, and to signed-in viewers on the map |
| `is_preset` | **Admin** | Offered as a starting template in the new-rule-set form |
| `is_showcase` | **Admin** | Curated as an example for the **anonymous** map |

Anonymous map visibility is the **read-time conjunction `is_showcase AND is_public`**. An admin
cannot showcase an unpublished rule set (409 on `set_showcase`), and an owner un-publishing hides it
immediately **without** clearing `is_showcase` — so re-publishing restores it with no admin action.
`is_public=false, is_showcase=true` is therefore a legitimate, reachable state, not a broken row.

### Foreign keys are NOT enforced

`db.py` sets `journal_mode`, `synchronous` and `busy_timeout` — but **no `PRAGMA foreign_keys=ON`**.
Every `ondelete="CASCADE"` in `models.py` is therefore documentation, not enforcement: the ORM
relationship's `cascade="all, delete-orphan"` is what actually deletes children. Any new child table
**must** declare that cascade or its rows outlive their parent forever.

### Tables that do NOT exist — do not code against them

| Assumed table | Reality |
|---|---|
| `launch_sites` | **Never existed.** Site identity (`name`, `description`, `lat`, `lon`, `altitude_m`) is embedded directly in `rulesets`. There is no `rulesets.launch_site_id`. See `models.py:113` |
| `weather_stations` | Station metadata is **not** in SQLite. The registry is built in memory by the collectors at startup and lives on `app.state.station_registry` / `app.state.display_registry` |
| `notification_configs` | Email notification state is two columns on `rulesets` (`notify_on`, `last_notified_decision`), not a separate table |

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
- **Tags**: `ruleset_id`, `owner_id`, `site_type` — there is **no `launch_site_id` tag** (no launch-site entity exists; the ruleset *is* the site)
- **Fields**: `decision` (green/orange/red), `condition_results` (JSON array). There is **no `blocking_conditions` field** — it appears nowhere in the codebase
- Written by two paths in `rules/evaluator.py`, both emitting the identical tag/field set: `run_evaluation` (live, one point) and `write_decisions_batch` (history backfill, batched with original timestamps)

**Removed**: `station_wind_profile` measurement was removed (v1.16). Altitude wind data is served by the wind forecast grid map, not per-station.

---

## Forecast Collectors

| Collector | File | Endpoint | Schedule | Notes |
|---|---|---|---|---|
| SwissMeteo surface | `forecast_swissmeteo.py` | lsmfapi `/api/forecast/station?station_id=X` | every 60 min | Primary; all stations fetched in parallel (`asyncio.gather`); flat fields + `_min`/`_max`; `init_time` + `forecast[]` schema |
| Open-Meteo surface | `forecast_openmeteo.py` | Open-Meteo API | **disabled** | Fallback only; re-enable in config if lsmfapi is unavailable for an extended period |
| SwissMeteo grid | `forecast_grid_swissmeteo.py` | lsmfapi `/api/forecast/grid?level_m=X` | every 60 min | Primary; 8 levels in parallel; 1272 pts; `ws`/`wd`/`rh` arrays |
| Open-Meteo grid | `forecast_grid.py` | Open-Meteo API | fallback only | Runs when SwissMeteo grid returns 0 wind pts |

**lsmfapi** (`lsmfapi-dev.lg4.ch`) is user-owned, same Docker network, no rate limiting. Serves ALL station networks. Response schema: `init_time`, `forecast[]` (surface) or `grid` + `frames[]` (grid). No per-station altitude wind endpoint — altitude data comes from the grid only. Updates ~4×/day (~04Z, 10Z, 16Z, 22Z); hourly collector runs are no-ops on most ticks.

**Scheduler status**: `ok_no_data` only when there were 0 eligible stations. When all stations fail with errors, status is `error`.

**`cron_hours`**: `ForecastCollectorConfig` supports optional `cron_hours: list[int]`. When set, scheduler uses `CronTrigger`; otherwise `IntervalTrigger(minutes=interval_minutes)`. Not currently used in production configs (hourly interval preferred).

---

## InfluxDB Client

Two clients in `InfluxClient.__init__()`:
- `_query_api` — `timeout` from config (default 10s) — used by all standard queries
- `_slow_query_api` — `slow_query_timeout` from config (default 60s) — used only by `query_forecast_replay`

Config keys: `influxdb.timeout` (ms, default 10000), `influxdb.slow_query_timeout` (ms, default 60000).

**`write_forecast_grid` — chunked writes**: Grid data (~1.17M points per run) is written in chunks of 5000 pts per InfluxDB call. A single bulk write caused read-timeout failures (~8.75 s) against the default 10s timeout.

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

### Public (unauthenticated)

- `GET /api/public/rulesets/map` — **the only unauthenticated route in the rule set surface.**
  Curated examples for visitors: `is_showcase AND is_public`, positioned, evaluated against real
  data. Returns `{data: [{id, name, lat, lon, site_type, decision}], generated_at}` — a narrow
  payload that deliberately does **not** reuse `RuleSetOut` (which carries `owner_display_name` and
  would leak owner identity by default). Served from a shared 60 s cache in
  `services/public_map.py`, so cost does not scale with visitors.
- `GET /api/rulesets/public-map` — signed-in equivalent: other owners' published rule sets, minus
  any within **500 m** of one of the viewer's own (`haversine_m` from `services/dedup.py`).
  Per-viewer, therefore **never** served from the anonymous cache entry.
- `PUT /api/rulesets/{id}/set_showcase` — admin curation toggle. **409** if the owner has not
  published it; un-curating is always allowed.

**Rule sets resting on missing data are omitted, not shown green.** `run_evaluation` returns green
when nothing triggers, including on no data ("unknown = benefit of the doubt") — fine for a pilot who
can see `no_data_stations`, a lie to a visitor. The public builder drops any rule set with a missing
station. Note the frontend's `if (!dec) return;` guard catches only *failed requests*, not this.

### Remaining routers

Not enumerated here — read the router file, which is the source of truth. (`07-api-conventions.md`
defines the response/error *format*, not the route list; it is not a route reference.)

| Prefix | File | Routes |
|---|---|---|
| `/api/auth` | `routers/auth.py` | 10 |
| `/api/rulesets` | `routers/rulesets.py` | 15 — rulesets carry site identity, gallery, presets, webcams, landing links |
| `/api/admin` | `routers/admin.py` | 10 |
| `/api/foehn` | `routers/foehn.py` | 7 |
| `/api/stats` | `routers/stats.py` | 6 |
| `/api/org` | `routers/org.py` | 3 — `/{slug}/status\|dashboard\|rulesets` |
| `/api/ai` | `routers/ai.py` | 1 |
| `/api/health` | `routers/health.py` | 1 |
| — | `routers/pages.py` | 20 HTML page routes (no `/api` prefix) |

There is **no launch-sites API** — a "launch site" is a `ruleset` with `site_type="launch"`.

---

## Rules Engine Design

`rules/evaluator.py`:
1. Load the flat condition list from SQLite (not a tree — see below)
2. Fetch latest measurements from InfluxDB for **all** stations in one batch (`query_latest_for_stations`)
3. Apply operator/value logic → per-condition colour
4. Bucket conditions by `group_id`: same non-NULL `group_id` → ANDed; `group_id = NULL` → standalone
5. Apply `combination_logic` (`worst_wins` or `majority_vote`)
6. Return `TrafficLightDecision` + write to `rule_decisions` InfluxDB

**Grouping is one level deep — AND only.** There is no OR-group and no nesting. Do not document or
build against a "condition tree".

**The evaluator buckets groups from the conditions — never from `condition_groups` rows.** This is
load-bearing and must not be "cleaned up":

- An empty group contributes no conditions, so it never becomes a bucket, never counts toward
  `total_units`, and is inert **by construction** rather than by a guard someone must remember.
- Iterating group rows instead would hit `all([]) → True` (vacuous) and then `_worst([])`, which is
  `max()` of an empty sequence → **`ValueError`**, killing evaluation for the whole rule set.
- `group_name` on `ConditionResult` is populated by a lookup layered onto the *output* only
  (`_group_names()`); it can never influence a decision.

A one-condition group evaluates identically to a standalone condition: `total_units` is
`len(standalone) + len(groups)`, so which bucket a lone condition lands in does not change the count,
and a one-member group contributes `_worst([c])` — that same colour.

Public entry points: `run_evaluation`, `run_evaluation_at`, `run_forecast_evaluation`,
`run_history_backfill`, `write_decisions_batch`. The core is `_evaluate_from_station_data(ruleset,
station_data) -> (decision, results)`. There is **no** `evaluate_ruleset()` function.

Forecast evaluation reuses identical logic over hourly `valid_time` steps. Does NOT write to InfluxDB.

---

## Replay Cache Architecture

`api/routers/stations.py` module-level `_replay_cache: dict[str, tuple[Any, float]]` (key → payload, monotonic stored_at). TTL 5 min.

**Warm-up**: background `asyncio.Task` at startup iterating offsets `[1, 0, 2, -1, 3, -2, 4, -3, 5]` sequentially.

**Cache poisoning guard**: skip writing when `fc_frame_count == 0` and `include_forecast` is true — prevents obs-only entries from blocking forecast data.

**Post-forecast invalidation**: `main.py` lifespan wires a real async hook via `scheduler.on_forecast_run = _make_forecast_hook(influx, display_registry)`. After each successful forecast run (`status == "ok"` and `measurement_count > 0`), the hook calls `invalidate_forecast_replay_cache()` then spawns `warm_replay_cache()` as a background task.

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
4. Pick canonical by priority: meteoswiss > slf > metar > holfuy > windline > ecowitt > wunderground > jfb
5. Non-canonical stations omitted from `display_registry`

**History**: member must have data in 2 h slice BEFORE window to be included.

---

## Static Asset Delivery

`api/routers/pages.py` + the security-headers middleware in `api/main.py`.

| Concern | Behaviour |
|---|---|
| Libraries | Leaflet 1.9.4 + Chart.js v4 self-hosted in `static/vendor/`. **No CDN** — CSP is `script-src 'self'` / `style-src 'self'`, so external refs are browser-blocked. |
| Cache-busting | `_page()` rewrites every local `href`/`src="/static/…"` to append `?v=<app-version>` at serve time. `_APP_VERSION` comes from `importlib.metadata.version("lenticularis")` → `pyproject.toml`. |
| Versioned assets | `?v=` present → `Cache-Control: public, max-age=31536000, immutable` |
| Unversioned assets | No `?v=` (locale JSON, ES-module imports) → `Cache-Control: public, max-age=600` |
| HTML | `Cache-Control: no-cache` + `ETag: "<version>-<mtime>"`; revalidates to `304`. Re-read per request so dev volume-mount edits stay live. |

**A version bump in `pyproject.toml` is mandatory when static assets change** — the version is
the cache key. Without it, changed assets stay pinned in browsers for a year.

---

## Deployment

### Versioning & Image Publishing

Version is single-sourced in `pyproject.toml`, read at runtime via `importlib.metadata`.

Pushing a `v*` git tag triggers `.github/workflows/docker-publish.yml`, which builds multi-arch
(amd64 + arm64) and publishes to `ghcr.io`. `docker/metadata-action` derives all tags from the
one git tag — `v1.18.1` produces `:v1.18.1`, `:1.18.1`, `:1.18`, `:1`, **and `:latest`**. There
is no separate "latest" tag to push.

Tags must be 3-part semver (`v1.2.3`) or the semver patterns do not activate.

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
