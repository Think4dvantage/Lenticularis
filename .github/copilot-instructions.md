# Copilot Instructions — Lenticularis

## What this project is

Lenticularis is a weather decision-support system for Switzerland. It collects data from multiple weather networks (currently MeteoSwiss, SLF, METAR, Windline, Ecovitt; Holfuy pending), stores it in InfluxDB, and lets each user build graphical rule sets that evaluate weather conditions across multiple stations to produce traffic light decisions (Status Ok / Warning / Stop) per site.

Key capabilities (current and planned):
- **Rule engine**: condition builder with per-row station picker, AND groups, combination logic, 5-day forecast evaluation
- **Decision history**: every evaluation stored in InfluxDB `rule_decisions`; analysis page with timeline and condition breakdown
- **Weather replay**: scrub through historical + forecast data on map and station table
- **Launchsite registry** (v0.10): separate `launch_sites` table; linked to clubs, rulesets, XContest/OGN stats
- **Clubs** (v0.10): paragliding clubs with GeoJSON area polygons shown as map overlay
- **Ruleset types** (v0.10): `'risk'` (red = stop) or `'opportunity'` (green = conditions are good)
- **AI ruleset generation** (v0.11): natural language → condition JSON via Claude API
- **Station deduplication** (v0.12): admin-defined station groups; API shows only freshest source
- **Organisations** (v1.0): commercial customer orgs (VKPI, Jungfraubahn); customer role; admin backend
- **XContest stats** (v1.2): flight statistics per launchsite via XContest API
- **OGN integration** (v1.3): live glider map overlay + historical launch counts per launchsite

The full product spec and milestone plan is in `plan-lenticularisStructure.prompt.md`. Read it before making significant changes.

---

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry (`pyproject.toml`) |
| Time-series DB | InfluxDB 2.x (`influxdb-client`) |
| Relational DB | SQLite via SQLAlchemy + Alembic |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| Auth | JWT via `python-jose`, passwords via `passlib` |
| Email | `aiosmtplib` |
| Config | YAML (`config.yml`) validated by Pydantic |
| Frontend | Vanilla JS + Leaflet.js + Chart.js |
| Container | Docker + docker-compose |

---

## Project Structure

```
src/lenticularis/
├── api/
│   ├── main.py              # FastAPI app, startup/shutdown lifecycle
│   ├── dependencies.py      # get_current_user, require_admin, get_current_user_optional FastAPI deps
│   └── routers/
│       ├── auth.py          # register, login, refresh, /me
│       ├── stations.py      # weather station endpoints + replay
│       ├── rulesets.py      # ruleset CRUD, evaluate, history, forecast
│       ├── launch_sites.py  # launchsite registry CRUD (v0.10)
│       ├── clubs.py         # club CRUD (v0.10)
│       ├── ai.py            # AI ruleset generation (v0.11)
│       ├── admin.py         # admin-only: users, orgs, station groups, launchsite approval
│       ├── organizations.py # org dashboard endpoint (v1.0)
│       ├── ogn.py           # OGN live overlay (v1.3)
│       └── health.py        # collector health
├── collectors/
│   ├── base.py              # Abstract BaseCollector
│   ├── forecast_base.py     # Abstract BaseForecastCollector
│   ├── meteoswiss.py        # Live 10-min observation data
│   ├── slf.py               # Live 30-min snow/wind observations
│   ├── metar.py             # Live METAR observations (AviationWeather)
│   ├── windline.py          # Windline sport weather
│   ├── ecovitt.py           # Personal weather stations
│   ├── forecast_openmeteo.py  # Open-Meteo ICON-seamless forecast (current)
│   ├── xcontest.py          # XContest flight stats per launchsite (v1.2)
│   └── ogn.py               # OGN historical launch-track stats (v1.3)
├── database/
│   ├── models.py            # SQLAlchemy ORM models (all tables)
│   ├── db.py                # SQLAlchemy session + _run_column_migrations()
│   └── influx.py            # InfluxDB client (weather_data, weather_forecast, rule_decisions)
├── models/
│   ├── weather.py           # WeatherStation, WeatherMeasurement, ForecastPoint
│   ├── auth.py              # UserCreate, UserOut (role + subscription), Token
│   ├── rules.py             # RuleSet (incl. ruleset_type), Condition, EvaluationResult, LandingDecision
│   ├── launch_sites.py      # LaunchSite schemas (v0.10)
│   └── clubs.py             # Club schemas (v0.10)
├── rules/
│   └── evaluator.py         # Live evaluator (run_evaluation) + forecast evaluator (run_forecast_evaluation)
├── services/
│   ├── auth.py              # JWT helpers
│   └── notifications.py     # Email + Pushover dispatch on status transitions
├── config.py                # YAML loader + Pydantic config models (incl. ai, xcontest, ogn sections)
└── scheduler.py             # APScheduler wiring all collectors (weather + daily stats jobs)
static/
├── index.html / map.js          # Map: station markers, launch/landing markers, launchsite layer, club polygon layer, OGN layer, replay bar, popup condition breakdown
├── replay.js                    # ReplayEngine class
├── rulesets.html                # Rule set cards: ruleset_type badge, live decision, landing decisions, forecast strip
├── ruleset-analysis.html        # Per-ruleset analysis: current eval table, decision history, forecast
├── ruleset-editor.html          # Condition builder + AI generation panel + site type + ruleset type toggle
├── launch-sites.html            # Launchsite list + map (v0.10)
├── launch-site-detail.html      # Launchsite detail: linked rulesets, XContest/OGN stats (v0.10+)
├── dashboard.html               # Customer org dashboard (v1.0)
├── admin/
│   ├── index.html           # Admin home
│   ├── users.html           # User management
│   ├── launch-sites.html    # Launchsite approval
│   ├── clubs.html           # Club CRUD
│   ├── organizations.html   # Org + member + ruleset management
│   └── station-groups.html  # Station dedup groups
├── stations.html / station-detail.html  # Station browser + detail charts + replay bar
└── auth.js / login.html / register.html # Auth UI
```

---

## Coding Conventions

- **Always use type hints** on function signatures and class attributes
- **Async/await** for all I/O — HTTP calls, DB writes, InfluxDB queries
- **Pydantic v2** for all data schemas and config validation
- **SQLAlchemy 2.0 style** (use `select()` not legacy `query()`)
- **FastAPI dependency injection** for auth (`get_current_user`, `require_admin`) and DB sessions
- **One router per domain** — never put all routes in `main.py`
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors
- **Never hardcode** config values — always read from `config.yml` or environment variables
- **Log** all collection events, rule evaluations, and errors using the standard `logging` module
- **No print statements** in production code

---

## Deployment Philosophy

Lenticularis runs self-hosted on a homelab (Traefik + Docker, Fedora host). Cloud deployment is not a goal, but the architecture must remain **cloud-portable** — avoid choices that permanently prevent horizontal scaling:

- No local file state (except InfluxDB and SQLite paths, which are volume-mounted)
- No hardcoded hostnames or absolute paths
- All config via `config.yml` or environment variables

**Database scalability note:** SQLite is acceptable for now (single instance, homelab). If the project ever needs to run multiple replicas or move to a cloud platform, the migration path is SQLite → PostgreSQL. When touching `database/models.py` or `db.py`, prefer SQLAlchemy patterns that work on both engines (avoid SQLite-only pragmas in production code paths).

---

## Rules Engine — Critical Design

The rules evaluator (`rules/evaluator.py`) must:

1. Load the rule set's condition tree from SQLite (`rule_conditions` + `condition_groups`)
2. For each condition, fetch the **latest measurement** from InfluxDB for `station_id` (and `station_b_id` for `pressure_delta`)
3. Apply the operator/value logic to produce a per-condition `result_colour`
4. Walk AND/OR group nesting to combine condition results within groups
5. Apply the ruleset's `combination_logic` (`worst_wins` or `majority_vote`) across top-level results
6. Return a `TrafficLightDecision` including a `condition_results` array: `[{condition_id, station_id, field, operator, value_a, value_b, actual_value, result_colour, group_id, group_all_matched}]`
   - `operator`, `value_a`, `value_b` are included so clients can render thresholds without a second API call
7. Write the full decision (including `condition_results` JSON) to the `rule_decisions` InfluxDB measurement via `write_decision()`

The station picker is **per condition row** — a single rule set can reference any number of different stations. This is intentional and central to the product.

### Forecast Evaluation (Milestone 2)

`run_forecast_evaluation(ruleset, influx, horizon_hours=120)` re-uses the identical standalone/group/combination logic but iterates over hourly `valid_time` steps from `weather_forecast` instead of fetching the single latest `weather_data` value. It returns a `list[ForecastStep]` and does **not** write to InfluxDB (ephemeral, on-demand). The `GET /api/rulesets/{id}/forecast?hours=120` endpoint must be declared **before** `GET /{ruleset_id}` to avoid FastAPI route shadowing.

---

## Launch / Landing Design

Every `RuleSet` has a `site_type` field (`'launch'` | `'landing'`) and a `ruleset_type` field (`'risk'` | `'opportunity'`, default `'risk'`):

- **`site_type`** — structural role: launch or landing
- **`ruleset_type`** — semantic direction: `'risk'` means red = dangerous (current default); `'opportunity'` means green = conditions are good (e.g. mountain is soarable). Affects how the UI labels and explains colours.

A ruleset can also optionally carry a `launch_site_id` FK pointing to a `launch_sites` table entry (introduced v0.10). This link is not required — rulesets remain usable standalone with their embedded lat/lon.

### LaunchSite Registry (v0.10+)

`launch_sites` is a separate table managed independently from rulesets. It is the stable anchor for XContest/OGN statistics and club area assignments. Key fields: `id`, `name`, `lat`, `lon`, `altitude_m`, `club_id` (FK → clubs), `is_approved`. The many-to-many `launch_site_rulesets` table links launchsites to rulesets.

### Clubs (v0.10+)

`clubs` table stores paragliding clubs with an `area_geojson` polygon. Displayed as a toggle layer on the map. Admin-managed.

### Linking

A `launch` ruleset can be linked to one or more `landing` rulesets via the `launch_landing_links` join table. Manage links with `PUT /api/rulesets/{id}/landings` (`{"landing_ids": [...]}`). Each landing ID must be owned by the same user and have `site_type == 'landing'`.

### Evaluation

`GET /api/rulesets/{id}/evaluate` on a **launch** ruleset:
1. Evaluates the launch conditions normally → writes to `rule_decisions`
2. For each linked landing: evaluates its conditions → writes to `rule_decisions`
3. Returns `landing_decisions: [{ruleset_id, name, decision}]` and `best_landing_decision` (best_wins: Status Ok > Warning > Stop)

This means landing statistics are populated whenever the parent launch is evaluated — no separate evaluation call needed.

### Map visualisation

- **Launch marker**: filled circle (colour = launch decision) + optional halo ring (colour = `best_landing_decision`). No halo if no landings are linked.
- **Landing marker**: flag icon (colour = landing decision).
- **Launchsite toggle layer** (v0.10+): paraglider icon markers for official launchsites; popup shows name, club, linked ruleset decisions.
- **Club polygon toggle layer** (v0.10+): GeoJSON polygons from `/api/clubs`.
- Halo uses **best_wins**: Status Ok > Warning > Stop.

### Key invariants

- A `landing` ruleset can be linked to multiple `launch` rulesets (shared landing field).
- Cloning a launch ruleset does **not** copy landing links — they are location-specific.
- `site_type` can be changed after creation; changing a launch to another type does not auto-remove existing links (they become inactive until type is changed back).

---

## Statistics — Critical Design

All statistics are derived from the `rule_decisions` InfluxDB measurement, not from raw `weather_data`. This is intentional — the `condition_results` field already contains per-condition, per-station actual values and result colours.

Statistics service (`services/stats.py`) implements 7 metrics via Flux queries:
- Status Ok days, hourly pattern, monthly breakdown, seasonal breakdown, condition trigger rate, site comparison, best windows
- Best windows (longest consecutive Status Ok streaks) is computed server-side after fetching the decision time series

---

## Authentication & Roles

- JWT bearer tokens (access + refresh)
- `get_current_user` FastAPI dependency — required on all user-facing endpoints
- `require_admin` dependency — required on all `/api/admin/*` endpoints
- Three roles: `'pilot'` (default), `'customer'`, `'admin'`
- Two subscription tiers: `'free'` (default), `'premium'`
- Pilots can only read/write their own rulesets, launchsites, and notification configs (enforce `owner_id == current_user.id`)
- Customers are restricted to org dashboard views — no ruleset editor access; enforced in frontend redirect and backend org membership check
- Customers belong to one or more `organizations`; org dashboard endpoint requires org membership or admin

---

## InfluxDB Measurements

### `weather_data`
- Tags: `station_id`, `network`, `canton`
- Fields: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qfe`, `pressure_qnh`, `pressure_qff`, `precipitation`, `snow_depth`

### `rule_decisions`
- Tags: `ruleset_id`, `owner_id`
- Fields: `decision` (string: `green` = Status Ok / `orange` = Warning / `red` = Stop), `condition_results` (JSON string)

### `weather_forecast`
- Tags: `station_id`, `network`, `model` (`icon-ch1` / `icon-ch2`), `init_time` (ISO: model run initialization timestamp)
- Timestamp: `valid_time` (the future moment the forecast is valid for)
- Fields: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qnh`, `precipitation`
- **No retention policy** — kept indefinitely to enable forecast-vs-actual accuracy analysis
- One row per `(station_id, model, init_time, valid_time)` — model runs are never overwritten; query layer selects latest `init_time` per `valid_time` when evaluating
- All networks get forecast coverage (ICON grid covers all of Switzerland); stations without lat/lon in DB are skipped

---

## What the Admin role does NOT do

Admins do **not** build or edit rulesets, or configure individual pilot rule sets. Those are fully pilot-owned and self-served. Admin scope is limited to:
- Enabling/disabling collectors and changing their collection intervals
- Managing user accounts (roles, subscription, deactivation)
- Approving official launchsite entries
- Managing clubs (CRUD + GeoJSON polygon)
- Managing organisations and their ruleset assignments
- Managing station deduplication groups

---

## Community Rule Gallery

- Rule sets are private by default (`is_public = false`)
- A pilot can publish a rule set (`is_public = true`) so it appears in the gallery
- Other pilots can **clone** a public rule set into their own account (`cloned_from_id` is set, `clone_count` increments on the original)
- Clones are independent — editing a clone does not affect the original

---

## Replay & Decision History

### Weather data replay
- `GET /api/stations/data-bounds` and `GET /api/stations/replay` are declared **before** `GET /api/stations/{station_id}` in `routers/stations.py` — literal-path routes must precede path-parameter routes in FastAPI
- `replay.js` exports a `ReplayEngine` class: `load({hours}|{start,end})`, `play()`, `pause()`, `setSpeed(n)`, `seekTo(idx)`; calls `onFrame(snapshot, ts, idx, total)` and `onStateChange(state)` callbacks
- Replay data is loaded once upfront; per-frame snapshots are built by scanning each station's history for the latest measurement ≤ current timestamp

### Decision history
- `GET /api/rulesets/{id}/evaluate` also calls `write_decision(rs, result, influx)` which persists `decision` + `condition_results` JSON string to the `rule_decisions` InfluxDB measurement
- `GET /api/rulesets/{id}/history?hours=N` fetches from `rule_decisions`, parses the stored JSON back to objects
- `GET /api/rulesets/{id}/forecast` must be declared **before** `GET /api/rulesets/{id}` to avoid FastAPI route shadowing (same pattern as stations)
- The ruleset analysis page (`/ruleset-analysis`) loads both the ruleset detail and live evaluation on init; stores condition definitions in `_condDefs` keyed by `condition_id` as fallback for historical data that predates `operator`/`value_a`/`value_b` being stored

---

## Forecast Pipeline — Critical Design

The forecast pipeline delivers a 120-hour traffic-light prognosis per ruleset using MeteoSwiss ICON-CH1/CH2-EPS models. It is built in two milestones — complete Milestone 1 (data flowing into InfluxDB, verified) before starting Milestone 2 (evaluation + UI).

### Milestone 1 — Data Pipeline

`collectors/forecast_meteoswiss.py` — `ForecastMeteoSwissCollector`:
- On startup: loads **all stations from every network** from the SQLite `stations` table (all have lat/lon → all fall within the ICON Swiss domain)
- Builds a `scipy.KDTree` from the ICON grid coordinates; maps each `station_id` to the nearest grid cell index — cached in memory
- `collect_ch1()` runs every 3 h (33 hourly lead times); `collect_ch2()` runs every 6 h (87 hourly lead times, h34–120)
- For each parameter × lead time × ensemble member: downloads GRIB2 via `meteodata-lab` + HTTP REST STAC API, loads with `cfgrib`/`xarray`, extracts station values, accumulates across members, computes **element-wise ensemble mean**
- Writes to `weather_forecast` InfluxDB measurement — every model run's predictions are stored independently (tagged by `init_time`); nothing is overwritten — this enables forecast-vs-actual accuracy analysis indefinitely

ICON → internal field mapping (verify from MeteoSwiss parameter overview CSV before coding):
- `FF_10M` → `wind_speed`, `DD_10M` → `wind_direction`, `VMAX_10M` → `wind_gust`
- `T_2M` (K − 273.15) → `temperature`, `RELHUM_2M` → `humidity`
- `PMSL` (Pa ÷ 100) → `pressure_qnh`, `TOT_PREC` → `precipitation`

**Milestone 1 is complete when**: InfluxDB `weather_forecast` contains future `valid_time` timestamps with all 7 fields, `model` and `init_time` tags, and two consecutive runs produce two distinct `init_time` values (no overwriting).

### Milestone 2 — Evaluation & UI

`run_forecast_evaluation(ruleset, influx, horizon_hours=120)` re-uses the identical standalone/group/combination logic but iterates over hourly `valid_time` steps from `weather_forecast` instead of fetching the single latest `weather_data` value. It returns a `list[ForecastStep]` and does **not** write to InfluxDB (ephemeral, on-demand). The `GET /api/rulesets/{id}/forecast?hours=120` endpoint must be declared **before** `GET /{ruleset_id}` to avoid FastAPI route shadowing.

UI: `rulesets.html` shows a horizontally scrollable hourly colour-strip below each card's live badge. Default view: 48 h. Visual separator + label at the CH1/CH2 boundary (33 h). Hover tooltip: valid_time + triggered conditions. Graceful "Forecast pending…" empty state before first collector run.

---

## Reference Implementation — winds-mobi

**Before implementing or debugging any collector, check the winds-mobi providers repo first:**

> **https://github.com/winds-mobi/winds-mobi-providers**
>
> Each file under `providers/` is a battle-tested production collector for the same networks we use
> (MeteoSwiss, Holfuy, SLF, Windline, and others). API field names, URL patterns, authentication
> quirks, and data-shape edge cases are already solved there.

Key lessons already learned from that repo:

- **MeteoSwiss `wind_direction`** is NOT a separate GeoJSON endpoint. It is embedded as
  `properties["wind_direction"]` inside every feature of the `wind_speed` response. There is no
  `windrichtung-10min` endpoint — requesting it returns 404.
- **MeteoSwiss pressure** has three distinct endpoints: `qff`, `qfe`, and `qnh`. The plain
  `luftdruck-10min` slug (without a variant suffix) does not exist.
- **MeteoSwiss timestamps** — use `reference_ts` (ISO 8601) as the primary field; fall back to
  `date` only if absent.
- **Altitude/elevation strings** can arrive as floats (`'1888.00'`) or with a unit suffix
  (`'1538.86 m'`). Always parse via `int(float(str(raw).split()[0]))`.
- **METAR wind direction** can legitimately be missing/variable in some reports (e.g. VRB/calm).
  Collector logic must allow nullable `wind_direction` instead of forcing a numeric value.

---

## Deployment Notes (homelab)

Target: Fedora host `172.18.10.50`, deployed via `scripts/remote.ps1`.

### Traefik label format

This homelab's Traefik instance requires **list format** labels, not map format:

```yaml
# CORRECT
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.myapp.rule=Host(`myapp.lg4.ch`)"

# WRONG — Traefik silently ignores map-format labels here
labels:
  traefik.enable: "true"
```

When a container is on **multiple Docker networks**, you must also add:
```yaml
  - "traefik.docker.network=proxy"
```
otherwise Traefik picks the wrong network IP and the upstream connection fails.

### Healthcheck

`python:3.11-slim` does **not** include `curl`. Use Python's stdlib instead:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

Traefik's Docker provider explicitly filters out containers whose health status is `unhealthy` or
`starting` — a broken healthcheck means the route never appears in the Traefik API, regardless of
how correct the labels are.

### Dev overlay

`docker-compose.dev.yml` extends the base compose file with:
- Live `src/` and `static/` volume mounts (`:ro,z`)
- `proxy` external network + Traefik labels for `lenti-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` — prevents stale `.pyc` files baked into the image from
  shadowing the volume-mounted `.py` sources