# Plan: Lenticularis — Product Spec & Implementation Brief (v2.3)

## Overview

Lenticularis is a weather aggregation and paragliding decision-support system for Switzerland. It collects data from multiple weather networks, normalises and stores it in InfluxDB, and lets each pilot build graphical per-site rule sets using a condition builder. Each condition targets a specific station — a single rule set can freely combine data from multiple stations. The system produces GREEN/ORANGE/RED decisions, stores full per-condition decision history, and exposes a statistics dashboard showing flyability patterns over time.

Core differentiator: **rules are fully pilot-owned and self-served** through a graphical editor. No admin-imposed logic.

---

## User Roles

| Role | Responsibilities |
|---|---|
| **Pilot (user)** | Manage own launch sites, build own rule sets, view stats, configure notifications, share/clone rule sets |
| **Admin** | Manage user accounts, enable/disable collectors and collection intervals — no involvement in rules or sites |

---

## User Stories

1. As a **pilot**, I open the map and see a traffic light for each of my launch sites.
2. As a **pilot**, I tap a launch site to see which conditions triggered the current status and why.
3. As a **pilot**, I use the rule editor to build conditions across multiple stations, e.g. Station A wind from S AND Station B wind from W both under 30 km/h → site is flyable.
4. As a **pilot**, I combine conditions with AND/OR groups and choose a combination logic.
5. As a **pilot**, I ask: "How many days was this site flyable in the last 6 months?"
6. As a **pilot**, I see which hours of the day are most often GREEN for a given site.
7. As a **pilot**, I see a seasonal/monthly flyability breakdown.
8. As a **pilot**, I see which specific rule conditions trigger RED most often, and from which station.
9. As a **pilot**, I compare flyability across two or more of my sites.
10. As a **pilot**, I find the longest consecutive GREEN windows in a past period.
11. As a **pilot**, I receive alerts when a site changes traffic light status.
12. As a **pilot**, I can publish a rule set for others to clone (not co-edit).
13. As an **admin**, I can enable/disable data sources and manage user accounts.

---

## Rule Editor Design

The rule editor is a **condition builder** (Zapier-style). Each condition row is fully independent — it carries its own station, field, operator, and value. A single rule set can freely combine data from any number of stations.

### Condition row fields

- **Station picker** — search/filter all active stations across all networks (per-row, not per-ruleset)
- **Station B picker** — only shown when field = `pressure_delta`
- **Field** — any available measurement: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure`, `pressure_delta`, `precipitation`, `snow_depth`
- **Operator** — `>`, `<`, `>=`, `<=`, `=`, `between`, `not between`, `in direction range`
- **Value A / Value B** — numeric input with inferred units displayed; direction range shows a compass graphic
- **Result colour** — GREEN / ORANGE / RED

### AND/OR grouping

Conditions can be nested into AND/OR groups (minimum 1 level of nesting supported). The station picker is per-condition row — no global station selection for a rule set.

### Multi-station example (key use case)

```
AND group
  [Station A - Beatenberg]  wind_speed       <  30 km/h          → GREEN
  [Station A - Beatenberg]  wind_direction   in range  160–220°  → GREEN
  [Station B - Niesen]      wind_speed       <  30 km/h          → GREEN
  [Station B - Niesen]      wind_direction   in range  250–310°  → GREEN

Combination logic: Worst wins
→ All four pass → GREEN (flyable)
→ Any one fails → ORANGE or RED
```

### Pressure delta (Föhn detection)

A condition with field `pressure_delta` shows two station pickers. The runtime evaluator computes `|station_A.pressure - station_B.pressure|` and applies the operator/value normally.

### Combination logic

- `Worst wins` (default) — any RED → RED; any ORANGE → ORANGE; else GREEN
- `Majority vote` — most common colour wins

### Rule set metadata

`name`, `description`, `launch_site_id`, `is_public` (false by default), `clone_count` (read-only), `cloned_from_id`

---

## Data Sources

| Source | Auth | Key measurements | Interval |
|---|---|---|---|
| MeteoSwiss | None (open data) | wind speed/gust, temp, humidity, pressure | 10 min |
| METAR (AviationWeather) | None (open data) | wind speed/direction/gust, temperature, altimeter (QNH) | 15 min |
| Holfuy | API key | wind speed/gust/direction | 5 min |
| SLF | None (open data) | wind speed/direction/gust, snow depth, temp | 30 min |
| Windline | API key | wind speed/direction | 10 min |
| Ecovitt | API key (personal weather station) | all sensor data | 15 min |

---

## Data Models

### InfluxDB

**`weather_data`** measurement:
- Tags: `station_id`, `network`, `canton`
- Fields: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure`, `precipitation`, `snow_depth`

**`rule_decisions`** measurement:
- Tags: `launch_site_id`, `ruleset_id`, `owner_id`
- Fields:
  - `decision` — green / orange / red
  - `condition_results` — JSON array: `[{condition_id, station_id, field, operator, value_a, value_b, actual_value, result_colour}]`
  - `blocking_conditions` — JSON array of condition IDs that voted non-GREEN
- Timestamp: evaluation time

> Storing `condition_results` per evaluation enables per-condition and per-station trigger statistics without re-querying raw weather data.

### SQLite tables

- `users` — `id`, `username`, `email`, `hashed_password`, `role`, `created_at`
- `weather_stations` — `station_id`, `name`, `network`, `latitude`, `longitude`, `elevation`, `canton`, `active`
- `launch_sites` — `id`, `name`, `latitude`, `longitude`, `owner_id` FK → users
- `rulesets` — `id`, `name`, `description`, `launch_site_id`, `owner_id`, `combination_logic`, `is_public`, `clone_count`, `cloned_from_id`, `created_at`, `updated_at`
- `rule_conditions` — `id`, `ruleset_id`, `group_id` (nullable), `station_id`, `station_b_id` (nullable), `field`, `operator`, `value_a`, `value_b` (nullable), `result_colour`, `sort_order`
- `condition_groups` — `id`, `ruleset_id`, `parent_group_id` (nullable), `logic` (AND/OR), `sort_order`
- `notification_configs` — `id`, `user_id`, `launch_site_id`, `channel`, `config_json`, `on_transitions_json`

### Pydantic models

- `models/weather.py` — `WeatherStation`, `WeatherMeasurement`
- `models/auth.py` — `User`, `UserCreate`, `Token`
- `models/sites.py` — `LaunchSite`, `LaunchSiteCreate`
- `models/rules.py` — `RuleSet`, `RuleSetCreate`, `Condition`, `ConditionGroup`, `TrafficLightDecision`
- `models/decisions.py` — `DecisionRecord`, `ConditionResult`
- `models/stats.py` — response shapes for all 7 statistics endpoints

---

## Statistics Module

All metrics are computed from the `rule_decisions` InfluxDB measurement using Flux queries plus server-side aggregation. No raw weather re-processing needed.

### Metrics

| Metric | Description | Endpoint |
|---|---|---|
| Flyable days | Count of calendar days with ≥1 GREEN evaluation | `GET /api/stats/{ruleset_id}/flyable-days` |
| Hourly pattern | GREEN % per hour-of-day (0–23) | `GET /api/stats/{ruleset_id}/hourly-pattern` |
| Monthly breakdown | GREEN/ORANGE/RED counts per calendar month | `GET /api/stats/{ruleset_id}/monthly` |
| Seasonal breakdown | Same grouped by meteorological season | `GET /api/stats/{ruleset_id}/seasonal` |
| Condition trigger rate | % of evaluations where each condition voted non-GREEN, attributed to station | `GET /api/stats/{ruleset_id}/condition-triggers` |
| Site comparison | Flyable days side-by-side for ≥2 rulesets | `GET /api/stats/compare?ruleset_ids=1,2,3` |
| Best windows | Top N longest consecutive GREEN streaks | `GET /api/stats/{ruleset_id}/best-windows` |

All time-range endpoints accept `?from=&to=` parameters. Best-windows also accepts `?top_n=5`.

---

## Full API Contracts

### Auth
- `POST /auth/register` — `{username, email, password}` → `{user_id, token}`
- `POST /auth/login` — `{username, password}` → `{access_token, refresh_token}`
- `POST /auth/refresh` — `{refresh_token}` → `{access_token}`

### Stations
- `GET /api/stations` — list all active stations (`?network=&canton=`)
- `GET /api/stations/{station_id}` — station metadata
- `GET /api/stations/{station_id}/latest` — most recent measurement
- `GET /api/stations/{station_id}/history` — `?from=&to=&fields=`

### Launch Sites
- `GET /api/launch-sites`
- `POST /api/launch-sites`
- `GET /api/launch-sites/{id}`
- `PUT /api/launch-sites/{id}`
- `DELETE /api/launch-sites/{id}`

### Rule Sets
- `GET /api/rulesets` — user's own (`?launch_site_id=`)
- `POST /api/rulesets` — create with full condition tree in body
- `GET /api/rulesets/{id}` — full rule set including condition tree
- `PUT /api/rulesets/{id}` — replace full condition tree (editor save)
- `DELETE /api/rulesets/{id}`
- `POST /api/rulesets/{id}/evaluate` — evaluate NOW, return decision + per-condition reasoning
- `POST /api/rulesets/{id}/publish`
- `POST /api/rulesets/{id}/unpublish`

### Community Gallery
- `GET /api/gallery` — public rule sets (`?q=&sort=clone_count`)
- `GET /api/gallery/{id}` — read-only view
- `POST /api/gallery/{id}/clone` — clone into current user's rule sets

### Statistics
- `GET /api/stats/{ruleset_id}/flyable-days`
- `GET /api/stats/{ruleset_id}/hourly-pattern`
- `GET /api/stats/{ruleset_id}/monthly`
- `GET /api/stats/{ruleset_id}/seasonal`
- `GET /api/stats/{ruleset_id}/condition-triggers`
- `GET /api/stats/compare?ruleset_ids=1,2,3`
- `GET /api/stats/{ruleset_id}/best-windows`

### Decisions history
- `GET /api/decisions?launch_site_id=&from=&to=`

### Notifications
- `GET /api/notifications`
- `POST /api/notifications`
- `PUT /api/notifications/{id}`
- `DELETE /api/notifications/{id}`

### Admin (require_admin dependency)
- `GET /api/admin/users`
- `PUT /api/admin/users/{id}`
- `GET /api/admin/collectors`
- `PUT /api/admin/collectors/{name}`

### System
- `GET /health`
- `GET /docs` (FastAPI auto-generated Swagger UI)

---

## Implementation Steps

1. Update `.cursorrules` and `.github/copilot-instructions.md` with final project conventions
2. Finalise `pyproject.toml` — add `python-jose`, `passlib`, `aiosmtplib`
3. SQLite schema + Alembic migrations — all 7 tables in `database/models.py`
4. Auth system — JWT in `api/routers/auth.py`; `get_current_user` + `require_admin` dependencies
5. Config system — complete `config.py` for all 5 collectors, InfluxDB, SQLite, SMTP/Pushover
6. Collector framework — complete `collectors/base.py`; build all 5 collectors
7. Pydantic models — all 6 model files listed above
8. InfluxDB client — `database/influx.py`; write + query + `query_decisions()` helper
9. Rules evaluator — `rules/evaluator.py`; walks condition tree, fetches live data per condition (per station), applies operator logic, resolves combination logic, writes full `condition_results` to InfluxDB
10. Statistics service — `services/stats.py`; Flux queries for all 7 metrics; best-windows as server-side algorithm
11. Scheduler — `scheduler.py`; all enabled collectors + periodic evaluation of all active rulesets
12. API routers — wire all endpoints; `api/main.py` startup/shutdown lifecycle
13. Notification service — `services/notifications.py`; status-transition triggers; email + Pushover dispatch
14. Frontend — Rule Editor (`static/editor.js`): per-row station picker, field/operator/value inputs, AND/OR group nesting, pressure-delta two-station mode, direction range compass graphic, live preview panel, save
15. Frontend — Statistics Dashboard (`static/stats.js`): flyable days card, hourly heatmap, monthly bar chart, condition trigger leaderboard (with station attribution), site comparison chart, best windows list
16. Frontend — Map & Dashboard (`static/app.js`): Leaflet.js map, traffic light badges per site, weather history chart panel, community gallery page, mobile-responsive layout
17. Docker — `Dockerfile` (multi-stage), `docker-compose.yml` (app + InfluxDB), health checks, `restart: unless-stopped`, updated `README.md`

---

## Release Milestones

### v0.1 — Station Detail Page (historical data)
**Goal:** MeteoSwiss data is collected into InfluxDB and a station detail page lets users browse historical charts per station directly by URL — no map required yet.

#### Backend
- MeteoSwiss collector running on schedule, writing to InfluxDB `weather_data`
- `GET /api/stations` — list all active stations
- `GET /api/stations/{station_id}` — station metadata
- `GET /api/stations/{station_id}/latest` — most recent measurement
- `GET /api/stations/{station_id}/history?from=&to=&fields=` — time-series query; default window last 24 h; presets 6 h / 24 h / 48 h / 7 days / 30 days
- Response shape: `{ station: WeatherStation, series: { field: str, unit: str, values: [{timestamp, value}] }[] }`
- InfluxDB write + latest-query + history-query helpers in `database/influx.py`
- Docker Compose stack running on homelab

#### Frontend — `static/station-detail.html` + `static/station-detail.js`
- Page opened as `station-detail.html?station_id={id}`
- Header: station name, network badge, elevation, canton
- Time-range selector (6 h / 24 h / 48 h / 7 days / 30 days) → re-fetches and re-renders all charts
- One Chart.js line/bar chart per available field:
  - **Wind speed** (km/h line) + **wind gust** (shaded area overlay)
  - **Wind direction** (scatter plot with direction as 0–360° Y-axis)
  - **Temperature** (°C line)
  - **Humidity** (% line)
  - **Pressure** (hPa line)
  - **Precipitation** (mm bar chart)
  - **Snow depth** (cm line, only shown if station reports it)
- Charts that have no data for the selected window are hidden (not shown as blank)
- Station list page (`static/stations.html`) — sortable table of all stations with a link to each detail page; persists across all versions
- `static/index.html` is a placeholder redirect to the map (added in v0.2); v0.1 entry point is `stations.html`

#### Implementation steps for v0.1
1. Complete MeteoSwiss collector (`collectors/meteoswiss.py`) — all fields normalised, scheduler wired
2. Implement `GET /api/stations`, `GET /api/stations/{station_id}`, `GET /api/stations/{station_id}/latest` endpoints
3. Add `query_station_history()` helper to `database/influx.py` and `GET /api/stations/{station_id}/history` endpoint
4. Create `static/station-detail.html` + `static/station-detail.js` (Chart.js charts, time-range selector)
5. Create `static/stations.html` — station table with name, network, canton, elevation, and "View history →" link per row
6. Verify Docker Compose stack deploys cleanly on homelab with Traefik labels

---

### v0.2 — Live Map
**Goal:** add a Leaflet.js map as the primary landing page (`index.html`); the station table (`stations.html`) and detail pages remain available as secondary views.

- `static/index.html` — Leaflet.js map showing all active weather stations as markers (replaces placeholder)
- `static/app.js` — fetch `/api/stations`, place markers, bind popup with latest measurement and **"View history →"** link to `station-detail.html?station_id={id}`
- Navigation header links: **Map** | **Stations** | *(future: Rules / Stats)*
- No new backend endpoints needed (reuses v0.1 API)

#### Implementation steps for v0.2
1. Create `static/index.html` Leaflet.js map shell with navigation header
2. Create `static/app.js` — fetch `/api/stations`, place markers, bind popup with latest measurement and "View history →" link
3. Add navigation header to `static/stations.html` and `static/station-detail.html` for consistent navigation

---

### v0.3 — Auth & User Management
**Goal:** secure the application with JWT authentication so only registered pilots can manage their own data.

- SQLite schema + Alembic migrations for all 7 tables (`database/models.py`)
- Pydantic models: `models/auth.py`, `models/sites.py`, `models/rules.py`, `models/decisions.py`, `models/stats.py`
- `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`
- `get_current_user` FastAPI dependency (JWT bearer)
- `require_admin` FastAPI dependency
- All existing station endpoints remain public (read-only); future pilot endpoints require auth
- `GET /api/admin/users`, `PUT /api/admin/users/{id}` (admin only)

#### Implementation steps for v0.3
1. Add `python-jose`, `passlib`, `alembic` to `pyproject.toml`
2. Write SQLAlchemy models for all 7 tables in `database/models.py`
3. Create Alembic migration for initial schema
4. Implement `api/routers/auth.py` with register/login/refresh
5. Add `get_current_user` + `require_admin` dependencies to `api/main.py`
6. Add `api/routers/admin.py` for user management endpoints
7. Protect appropriate existing endpoints with the auth dependency

---

### v0.4 — Launch Sites
**Goal:** pilots can create and manage their own launch sites, visible on the map.

- `GET/POST /api/launch-sites`, `GET/PUT/DELETE /api/launch-sites/{id}`
- Launch sites owned by the authenticated pilot (`owner_id == current_user.id`)
- Site markers shown on the Leaflet map (distinct icon from weather station markers)
- `models/sites.py` fully wired

#### Implementation steps for v0.4
1. Implement `api/routers/launch_sites.py` with all CRUD endpoints
2. Register router in `api/main.py`
3. Add launch site markers to `static/app.js` with a separate layer/icon

---

### v0.5 — SLF Collector + Full Scheduler
**Goal:** add the SLF (snow depth / temperature) data source and harden the scheduler so both active collectors run reliably.

- Complete `collectors/base.py` (abstract `BaseCollector`)
- Implement `collectors/slf.py` — snow depth + temperature from SLF open data
- `scheduler.py` wires MeteoSwiss (10 min) and SLF (30 min) at their configured intervals
- `GET /api/admin/collectors`, `PUT /api/admin/collectors/{name}` let admin enable/disable and adjust intervals at runtime
- Unit tests: `normalize_data()` fixture tests for both collectors

#### Implementation steps for v0.5
1. Finalise `collectors/base.py` abstract interface
2. Implement `collectors/slf.py` (consult winds-mobi reference repo)
3. Harden `scheduler.py` — both enabled collectors, error handling per job
4. Add admin collector endpoints in `api/routers/admin.py`
5. Write `normalize_data()` unit tests for MeteoSwiss and SLF

### v0.5.1 — METAR Collector (No-auth)
**Goal:** add AviationWeather METAR as an independent no-auth observed-wind source for Swiss airport stations.

- Implemented `collectors/metar.py`
- Scheduler supports METAR interval configuration (dev currently 15 min)
- Station registry priming includes METAR stations from `stationinfo`
- Measurement mapping: `wspd/wgst` (kt→km/h), `wdir`, `temp`, `altim`→`pressure_qnh`

---

### v0.6 — Rule Editor (condition builder UI)
**Goal:** pilots can build per-site rule sets with AND/OR condition trees through a graphical editor.

- SQLite `rulesets` + `rule_conditions` + `condition_groups` tables (from v0.3 schema) fully used
- `GET/POST /api/rulesets`, `GET/PUT/DELETE /api/rulesets/{id}`
- `static/editor.js` — condition builder:
  - Per-row station picker (search/filter all active stations)
  - Field, operator, value A / value B inputs with inferred units
  - Station B picker shown only when field = `pressure_delta`
  - Direction range shows compass graphic
  - AND/OR group nesting (1 level min)
  - Combination logic selector (`worst_wins` / `majority_vote`)
  - Save button posts full condition tree JSON to API

#### Implementation steps for v0.6
1. Implement `api/routers/rulesets.py` CRUD endpoints
2. Create `static/editor.html` page shell
3. Create `static/editor.js` condition builder (station picker, field/op/value rows, AND/OR nesting)
4. Wire save to `PUT /api/rulesets/{id}`

---

### v0.7 — Rules Evaluator + Traffic Lights on Map
**Goal:** rule sets are evaluated against live station data; the map shows GREEN/ORANGE/RED badges per launch site.

- `rules/evaluator.py` walks condition tree, fetches latest InfluxDB measurement per station per condition, applies operator logic, resolves combination logic, returns `TrafficLightDecision` with full `condition_results`
- Writes decision + `condition_results` JSON to `rule_decisions` InfluxDB measurement
- `POST /api/rulesets/{id}/evaluate` endpoint (on-demand evaluation)
- Scheduler runs periodic evaluation of all active rulesets (from v0.5 scheduler)
- `GET /api/decisions?launch_site_id=&from=&to=` endpoint
- Map (`static/app.js`) shows traffic light badges on launch site markers; clicking shows which conditions triggered

#### Implementation steps for v0.7
1. Implement `rules/evaluator.py` (condition tree walk, per-condition InfluxDB fetch, operator logic, combination logic)
2. Add `write_decision()` + `query_decisions()` helpers to `database/influx.py`
3. Add evaluate endpoint to `api/routers/rulesets.py`
4. Add `api/routers/decisions.py` for decisions history endpoint
5. Add evaluation scheduler job in `scheduler.py`
6. Update `static/app.js` to fetch and display traffic light badges

---

### v0.8 — Statistics Dashboard
**Goal:** pilots can view historical flyability patterns for their sites.

- `services/stats.py` — Flux queries for all 7 metrics; best-windows algorithm server-side
- All 7 `GET /api/stats/…` endpoints
- `static/stats.html` + `static/stats.js`:
  - Flyable days card
  - Hourly heatmap (Chart.js)
  - Monthly bar chart
  - Seasonal breakdown
  - Condition trigger leaderboard (with station attribution)
  - Site comparison chart
  - Best windows list
- Time-range filter (`?from=&to=`) on all charts

#### Implementation steps for v0.8
1. Implement `services/stats.py` with all 7 Flux queries and best-windows algorithm
2. Add `api/routers/stats.py` with all 7 endpoints
3. Create `static/stats.html` page shell
4. Create `static/stats.js` with Chart.js charts for all 7 metrics

---

### v0.9 — Notifications
**Goal:** pilots receive alerts when a launch site changes traffic light status.

- `services/notifications.py` — status-transition detection; dispatch via email (`aiosmtplib`) and Pushover
- `notification_configs` SQLite table (from v0.3 schema) fully used
- `GET/POST/PUT/DELETE /api/notifications` endpoints
- Notification config UI on the launch site detail page (channel + transition filter)
- `aiosmtplib` + Pushover HTTP API integration

#### Implementation steps for v0.9
1. Add `aiosmtplib` to `pyproject.toml`
2. Implement `services/notifications.py` (transition detection, email + Pushover dispatch)
3. Call notification service from evaluator after each decision write
4. Implement `api/routers/notifications.py`
5. Add notification config UI to launch site detail popup/page

---

### v1.0 — Full MVP Release
**Goal:** stable, tested, fully deployable release of all core features.

- All endpoints from the API contracts section are implemented and tested
- Integration test suite: user → site → ruleset → evaluations → stats → notifications
- Unit tests for all condition operators, pressure delta, AND/OR logic, both combination modes, all 7 stat functions
- Docker multi-stage `Dockerfile`, `docker-compose.yml` with health checks and `restart: unless-stopped`
- `docker-compose.dev.yml` with live volume mounts and Traefik labels
- `README.md` updated with full setup and deployment instructions
- Mobile-responsive CSS pass on all pages

#### Implementation steps for v1.0
1. Write pytest unit tests for all collectors, operators, evaluator, stats
2. Write integration test (full user journey)
3. Final Docker / docker-compose polish (multi-stage build, health checks)
4. CSS mobile-responsive pass across all static pages
5. Update `README.md` with deployment guide

---

### v1.1 — Community Rule Gallery
**Goal:** pilots can publish their rule sets for others to discover and clone.

- `is_public`, `clone_count`, `cloned_from_id` fields on rulesets fully enforced
- `GET /api/gallery`, `GET /api/gallery/{id}`, `POST /api/gallery/{id}/clone`
- Gallery page in the frontend: searchable list of public rule sets, clone button, shows `clone_count`
- Clone creates an independent copy under the cloning pilot's account

#### Implementation steps for v1.1
1. Implement `api/routers/gallery.py`
2. `POST /api/rulesets/{id}/publish` + `unpublish` endpoints
3. Create `static/gallery.html` + gallery UI in `static/app.js`

---

### v1.2 — Additional Collectors (Holfuy, Windline, Ecovitt)
**Goal:** add the three API-key-based personal-station networks for pilots who own or use those devices.

> These collectors involve third-party API keys, non-trivial authentication flows, and proprietary data shapes. Deferred until the core product is stable.

- `collectors/holfuy.py` — wind speed / gust / direction (Holfuy API key)
- `collectors/windline.py` — wind speed / direction (Windline API key)
- `collectors/ecovitt.py` — full personal weather station sensor set (Ecovitt API key)
- All three wired into `scheduler.py` and admin collector endpoints
- Unit tests: `normalize_data()` for each new collector

#### Implementation steps for v1.2
1. Implement `collectors/holfuy.py` (consult winds-mobi reference repo)
2. Implement `collectors/windline.py`
3. Implement `collectors/ecovitt.py`
4. Add config blocks for each new network in `config.yml.example`
5. Wire into scheduler and admin endpoints
6. Write `normalize_data()` unit tests with fixture JSON for each

---

### v2.0 — Polish, Performance & Admin Panel
**Goal:** production-grade release suitable for a wider pilot community.

- Full admin panel UI: user management (enable/disable accounts, change role), collector status overview with interval controls
- Wind rose chart on station detail page (replaces direction scatter plot)
- Offline/stale data indicator — popup badge when latest measurement is older than 2× collection interval
- Rate-limit on auth endpoints (prevent brute-force)
- OpenAPI docs reviewed and finalised (`/docs`)
- Collector reliability: retry logic, exponential back-off, dead-letter log per network
- Performance: InfluxDB Flux queries reviewed for efficiency; add downsampling task for data older than 90 days
- Accessibility pass (WCAG AA) on all frontend pages
- `CHANGELOG.md` maintained from this version onward

---

## Backlog (post-v2.0, unprioritised)

- **Multi-language support** — i18n for all frontend strings (DE/FR/IT/EN); language picker in header; all hardcoded labels extracted to locale files

---

## Verification

- Unit tests (`pytest`): config loading, each collector's `normalize_data()`, all condition operators, pressure delta, AND/OR group logic, both combination modes, all 7 statistics metric functions with fixture data
- Integration test: create user → site → rule set with multi-station conditions → trigger evaluations → query stats endpoints → assert flyable-day count matches seeded decisions
- Manual: open rule editor, build a rule mixing Station A (wind speed) and Station B (wind direction), save, run scheduler, open stats dashboard, verify condition trigger chart attributes correctly to each station
- Community gallery: publish as user 1 → clone as user 2 → verify `clone_count` increments

---

## Key Decisions

- Rules are 100% pilot-owned; admin limited to collector config and user management
- Station picker is **per condition row** — no ruleset-level station selection — enabling multi-station rules natively
- `condition_results` JSON (including `station_id`) written per evaluation to InfluxDB enables per-station trigger statistics without raw weather re-queries
- Condition tree stored normalised in `rule_conditions` + `condition_groups` tables (not a JSON blob) for queryability
- Pressure delta is a first-class condition type with two-station picker
- Best-windows metric computed server-side (not Flux) for simplicity
- Rule sharing is clone-only (no co-editing); private by default, opt-in publish
- Chart.js for all charts (lightweight, no framework required)
- MeteoSwiss, SLF, and METAR are the primary no-auth collectors; Holfuy, Windline, and Ecovitt are deferred to v1.2