# Plan: Lenticularis — Product Spec & Implementation Brief (v2.3)

## Overview

Lenticularis is a weather aggregation and decision-support system for Switzerland. It collects data from multiple weather networks, normalises and stores it in InfluxDB, and lets each user build graphical per-site rule sets using a condition builder. Each condition targets a specific station — a single rule set can freely combine data from multiple stations. The system produces Status Ok / Warning / Stop decisions, stores full per-condition decision history, and exposes a statistics dashboard showing site condition patterns over time.

The system supports multiple site types. Currently implemented: `paragliding_launch` and `paragliding_landing`. Additional site types (e.g. hiking, cycling, skiing) are planned.

Core differentiator: **rules are fully user-owned and self-served** through a graphical editor. No admin-imposed logic.

---

## User Roles

| Role | Responsibilities |
|---|---|
| **Pilot** (default) | Manage own sites, build own rule sets, view stats, configure notifications, share/clone rule sets |
| **Customer** | Read-only access to organisation dashboard assigned by admin; no ruleset editor access |
| **Admin** | Manage user accounts, roles, subscriptions; manage organisations; approve launchsites; manage clubs; enable/disable collectors |

### Subscription Tiers
| Tier | Access |
|---|---|
| **Free** | Standard pilot or customer access |
| **Premium** | Extended features (TBD — e.g. longer history, more rulesets, priority support) |

---

## User Stories

1. As a **user**, I open the map and see a traffic light for each of my sites.
2. As a **user**, I tap a site to see which conditions triggered the current status and why.
3. As a **user**, I use the rule editor to build conditions across multiple stations, e.g. Station A wind from S AND Station B wind from W both under 30 km/h → site is Status Ok.
4. As a **user**, I combine conditions with AND/OR groups and choose a combination logic.
5. As a **user**, I ask: "How many days was this site Status Ok in the last 6 months?"
6. As a **user**, I see which hours of the day are most often Status Ok for a given site.
7. As a **user**, I see a seasonal/monthly Status Ok breakdown.
8. As a **user**, I see which specific rule conditions trigger Stop most often, and from which station.
9. As a **user**, I compare Status Ok days across two or more of my sites.
10. As a **user**, I find the longest consecutive Status Ok windows in a past period.
11. As a **user**, I receive alerts when a site changes traffic light status.
12. As a **user**, I can publish a rule set for others to clone (not co-edit).
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
- **Result colour** — Status Ok (green) / Warning (orange) / Stop (red)

### AND/OR grouping

Conditions can be nested into AND/OR groups (minimum 1 level of nesting supported). The station picker is per-condition row — no global station selection for a rule set.

### Multi-station example (key use case)

```
AND group
  [Station A - Beatenberg]  wind_speed       <  30 km/h          → Status Ok
  [Station A - Beatenberg]  wind_direction   in range  160–220°  → Status Ok
  [Station B - Niesen]      wind_speed       <  30 km/h          → Status Ok
  [Station B - Niesen]      wind_direction   in range  250–310°  → Status Ok

Combination logic: Worst wins
→ All four pass → Status Ok
→ Any one fails → Warning or Stop
```

### Pressure delta (Föhn detection)

A condition with field `pressure_delta` shows two station pickers. The runtime evaluator computes `|station_A.pressure - station_B.pressure|` and applies the operator/value normally.

### Combination logic

- `Worst wins` (default) — any Stop → Stop; any Warning → Warning; else Status Ok
- `Majority vote` — most common status wins

### Rule set metadata

`name`, `description`, `site_type` (e.g. `"paragliding_launch"` | `"paragliding_landing"` — extensible), `lat`, `lon`, `altitude_m`, `is_public` (false by default), `clone_count` (read-only), `cloned_from_id`

A **paragliding_launch** ruleset can be linked to multiple **paragliding_landing** rulesets via `PUT /api/rulesets/{id}/landings`. The evaluate endpoint for a launch also evaluates all linked landings and returns `landing_decisions` + `best_landing_decision` (best_wins: Status Ok > Warning > Stop).

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
  - `decision` — green (Status Ok) / orange (Warning) / red (Stop)
  - `condition_results` — JSON array: `[{condition_id, station_id, field, operator, value_a, value_b, actual_value, result_colour}]`
  - `blocking_conditions` — JSON array of condition IDs that voted non-GREEN
- Timestamp: evaluation time

> Storing `condition_results` per evaluation enables per-condition and per-station trigger statistics without re-querying raw weather data.

### SQLite tables

- `users` — `id`, `email`, `display_name`, `hashed_password`, `role` (`pilot`|`customer`|`admin`), `subscription` (`free`|`premium`), `is_active`, `created_at`, `updated_at`
- `oauth_identities` — `id`, `user_id` FK, `provider`, `provider_user_id`, `provider_email`, `created_at`
- `clubs` — `id`, `name`, `abbreviation`, `website`, `contact`, `area_geojson`, `description`, `created_at`
- `launch_sites` — `id`, `name`, `description`, `lat`, `lon`, `altitude_m`, `country`, `club_id` FK → clubs, `created_by` FK → users, `is_approved`, `created_at`, `updated_at`
- `launch_site_rulesets` — `launch_site_id` FK, `ruleset_id` FK (many-to-many join)
- `rulesets` — `id`, `name`, `description`, `site_type` (`launch`|`landing`), `ruleset_type` (`risk`|`opportunity`), `lat`, `lon`, `altitude_m`, `owner_id` FK → users, `launch_site_id` FK → launch_sites (optional), `combination_logic`, `is_public`, `clone_count`, `cloned_from_id`, `created_at`, `updated_at`
- `launch_landing_links` — `id`, `launch_ruleset_id` FK → rulesets, `landing_ruleset_id` FK → rulesets (unique pair constraint)
- `rule_conditions` — `id`, `ruleset_id`, `group_id` (nullable), `station_id`, `station_b_id` (nullable), `field`, `operator`, `value_a`, `value_b` (nullable), `result_colour`, `sort_order`
- `notification_configs` — `id`, `user_id`, `ruleset_id`, `channel`, `config_json`, `on_transitions_json`
- `organizations` — `id`, `name`, `slug` (unique), `description`, `created_at`
- `organization_members` — `org_id` FK, `user_id` FK, `org_role` (`owner`|`member`)
- `organization_rulesets` — `org_id` FK, `ruleset_id` FK, `label`, `sort_order`
- `station_groups` — `id`, `name`, `primary_station_id`, `created_at`
- `station_group_members` — `group_id` FK, `station_id`
- `xcontest_stats` — `id`, `launch_site_id` FK, `year`, `flight_count`, `avg_distance_km`, `max_distance_km`, `top_pilot`, `fetched_at`
- `ogn_stats` — `id`, `launch_site_id` FK, `year`, `flight_count`, `fetched_at`

> **Design change from original spec:** A separate `launch_sites` table now exists. Rulesets retain their own location fields and remain independently usable, but can optionally be linked to a launchsite entry via `launch_site_id`. The launchsite registry is the stable anchor for XContest/OGN statistics and club area assignments.

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
| Status Ok days | Count of calendar days with ≥1 Status Ok evaluation | `GET /api/stats/{ruleset_id}/flyable-days` |
| Hourly pattern | Status Ok % per hour-of-day (0–23) | `GET /api/stats/{ruleset_id}/hourly-pattern` |
| Monthly breakdown | Status Ok / Warning / Stop counts per calendar month | `GET /api/stats/{ruleset_id}/monthly` |
| Seasonal breakdown | Same grouped by meteorological season | `GET /api/stats/{ruleset_id}/seasonal` |
| Condition trigger rate | % of evaluations where each condition voted non-Status Ok, attributed to station | `GET /api/stats/{ruleset_id}/condition-triggers` |
| Site comparison | Status Ok days side-by-side for ≥2 rulesets | `GET /api/stats/compare?ruleset_ids=1,2,3` |
| Best windows | Top N longest consecutive Status Ok streaks | `GET /api/stats/{ruleset_id}/best-windows` |

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

### v0.7 — Rules Evaluator + Traffic Lights on Map ✅
**Goal:** rule sets are evaluated against live station data; the map shows GREEN/ORANGE/RED badges per launch site with halo for landing zones.

- `rules/evaluator.py` walks condition tree, fetches latest InfluxDB measurement per station per condition, applies operator logic, resolves combination logic, returns `TrafficLightDecision` with full `condition_results`
- Writes decision + `condition_results` JSON to `rule_decisions` InfluxDB measurement; `ConditionResult` includes `operator`, `value_a`, `value_b` so clients can render thresholds without a second API call
- `GET /api/rulesets/{id}/evaluate` — evaluates launch + all linked landings; returns `landing_decisions` + `best_landing_decision`
- `PUT /api/rulesets/{id}/landings` — replace landing zone links for a launch ruleset
- Rulesets have `site_type` (free string, default `"paragliding_launch"`); linked via `launch_landing_links` table
- Map shows vivid gust-scaled wind arrow markers; **▲ launch markers** (coloured disc + optional halo ring) and **⛑ landing markers**:
  - Launch disc colour = launch decision; halo colour = best landing decision (best_wins: green > orange > red)
  - No halo if no landings linked
- Map auto-refresh every 5 minutes
- `static/rulesets.html` cards show site type badge + inline landing decision badges per linked zone
- Ecovitt collector (personal weather stations)

---

### v0.8 — Replay + Ruleset Analysis & Decision History ✅
**Goal:** pilots can replay historical weather data on the map and table, and inspect why a rule set produced a given decision at any point in time.

#### Weather data replay
- `GET /api/stations/data-bounds` — returns earliest/latest timestamp with any data (used to constrain custom date picker)
- `GET /api/stations/replay?hours=N` or `?start=ISO&end=ISO` — returns all station history for the replay window
- `static/replay.js` — `ReplayEngine` class: `load()`, `play()`, `pause()`, `seekTo()`, speed control
  - Frame intervals: 10× 50× 100× 200× 500×
  - Per-frame snapshot: for each station finds latest measurement ≤ current timestamp
- Replay bar on map (`index.html`) and station table (`stations.html`): range selector (6h / 24h / 7d / custom), speed buttons, play/pause, scrubber
- Custom date picker constrained to actual data range via `utcToLocalInputValue` conversion

#### Ruleset analysis page
- `GET /api/rulesets/{id}/history?hours=N` — returns chronological evaluation decisions from `rule_decisions` InfluxDB measurement, with `condition_results` JSON parsed into objects
- `/ruleset-analysis` page (`static/ruleset-analysis.html`):
  - **Header**: site icon, name, meta (type / altitude / logic / visibility), current decision pill, Edit button
  - **Current evaluation table**: Station | Field | Condition | Actual | Status; no-data warning; Refresh button; Condition Group label for AND groups
  - **Decision history**: range buttons (6h / 24h / 7d / 30d), leading-empty timeline strip (full window always shown), Chart.js scatter/step chart with pinned x-axis range, grouped state-change table
  - **State-change table**: collapses consecutive same-decision entries into runs (From / To / Duration / N evals); click to expand condition detail table at the transition point (Station | Field | Condition | Actual | Status); falls back to current ruleset condition definitions for historical data lacking stored thresholds
- Map popup condition breakdown: clicking a launch/landing marker shows per-condition evaluation with coloured status dots, threshold strings, and actual values
- Rulesets list cards navigate to analysis page on click (card body is an `<a>` tag)
- "AND group" renamed to "Condition Group" throughout

#### Implementation steps completed
1. Added `query_data_bounds()` and `query_history_all_stations()` to `database/influx.py`
2. Added `query_decision_history()` to `database/influx.py`
3. Added `GET /api/stations/data-bounds` and `GET /api/stations/replay` to stations router (before `/{station_id}`)
4. Added `GET /api/rulesets/{id}/history` to rulesets router
5. Extended `ConditionResult` Pydantic model with `operator`, `value_a`, `value_b`
6. Updated evaluator to include these fields in `condition_results` dict and stored JSON
7. Created `static/replay.js` (`ReplayEngine`) and `static/ruleset-analysis.html`
8. Added popup condition breakdown to `static/index.html` and `static/map.js`

---

### v0.9 — Statistics Dashboard
**Goal:** users can view historical site condition patterns.

- `services/stats.py` — Flux queries for all 7 metrics; best-windows algorithm server-side
- All 7 `GET /api/stats/…` endpoints
- `static/stats.html` + `static/stats.js`:
  - Status Ok days card
  - Hourly heatmap (Chart.js)
  - Monthly bar chart
  - Seasonal breakdown
  - Condition trigger leaderboard (with station attribution)
  - Site comparison chart
  - Best windows list
- Time-range filter (`?from=&to=`) on all charts

#### Implementation steps for v0.9
1. Implement `services/stats.py` with all 7 Flux queries and best-windows algorithm
2. Add `api/routers/stats.py` with all 7 endpoints
3. Create `static/stats.html` page shell
4. Create `static/stats.js` with Chart.js charts for all 7 metrics

---

### v0.10 — Launchsite Registry + Clubs + Ruleset Types
**Goal:** introduce a proper launchsite registry separate from rulesets, paragliding clubs with area polygons on the map, and a risk/opportunity type distinction on rulesets.

> **Design change:** A separate `launch_sites` table is introduced. Previously, site identity was embedded in `rulesets`. Rulesets keep their own location fields (they remain usable standalone), but can now optionally be linked to an official launchsite entry. Launchsites serve as the stable anchor for XContest/OGN statistics in later milestones.

#### New SQLite tables

**`clubs`**
```
id           UUID PK
name         TEXT NOT NULL
abbreviation TEXT
website      TEXT
contact      TEXT
area_geojson TEXT  -- GeoJSON polygon/multipolygon of responsible area
description  TEXT
created_at   DATETIME
```

**`launch_sites`**
```
id           UUID PK
name         TEXT NOT NULL
description  TEXT
lat          FLOAT NOT NULL
lon          FLOAT NOT NULL
altitude_m   INT
country      TEXT DEFAULT 'CH'
club_id      UUID FK → clubs.id SET NULL
created_by   UUID FK → users.id SET NULL
is_approved  BOOL DEFAULT FALSE   -- admin-approved official entry
created_at   DATETIME
updated_at   DATETIME
```

**`launch_site_rulesets`** (many-to-many join)
```
launch_site_id  UUID FK → launch_sites.id CASCADE
ruleset_id      UUID FK → rulesets.id CASCADE
PRIMARY KEY (launch_site_id, ruleset_id)
```

Add optional column to `rulesets`: `launch_site_id UUID FK → launch_sites.id SET NULL`

Migration: `ALTER TABLE rulesets ADD COLUMN launch_site_id TEXT REFERENCES launch_sites(id)`

#### Ruleset type
Add `ruleset_type TEXT NOT NULL DEFAULT 'risk'` to `rulesets` table.
- `'risk'` — current behaviour: red = dangerous condition
- `'opportunity'` — semantic inversion: green = good condition (e.g. "soarable mountain ridge")

Migration: `ALTER TABLE rulesets ADD COLUMN ruleset_type TEXT NOT NULL DEFAULT 'risk'`

UI changes: badge on ruleset cards/editor; swap colour-label descriptions in analysis page for opportunity type.

#### API endpoints

**Clubs** (`/api/clubs`):
- `GET /api/clubs` — list all (public, no auth)
- `GET /api/clubs/{id}` — detail with linked launchsites
- `POST /api/clubs` — admin only
- `PUT /api/clubs/{id}` — admin only
- `DELETE /api/clubs/{id}` — admin only

**Launchsites** (`/api/launch-sites`):
- `GET /api/launch-sites` — list all (public, no auth); `?approved_only=true`
- `GET /api/launch-sites/{id}` — detail with linked rulesets
- `POST /api/launch-sites` — authenticated
- `PUT /api/launch-sites/{id}` — owner or admin
- `DELETE /api/launch-sites/{id}` — owner or admin
- `PUT /api/launch-sites/{id}/approve` — admin only

#### Frontend
- `static/launch-sites.html` — Leaflet map + sortable table of all launchsites; click to detail
- `static/launch-site-detail.html` — site info, linked rulesets with live decisions, placeholder stat cards for XContest/OGN (future)
- Map overlay in `index.html`: optional toggle layer for launchsite markers (paraglider icon); popups show site name, club, and linked ruleset decisions
- Map overlay in `index.html`: optional toggle layer for club polygons (GeoJSON from `/api/clubs`); on-click shows club name, website

#### Implementation steps for v0.10
1. Add `clubs`, `launch_sites`, `launch_site_rulesets` ORM models to `database/models.py`; add `launch_site_id` and `ruleset_type` columns to `rulesets` migration in `db.py`
2. Add Pydantic schemas: `models/launch_sites.py`, `models/clubs.py`; update `models/rules.py` for `ruleset_type`
3. Implement `api/routers/launch_sites.py` and `api/routers/clubs.py`; register in `main.py`
4. Create `static/launch-sites.html` and `static/launch-site-detail.html`
5. Add club polygon + launchsite marker toggle layers to `static/index.html` / `map.js`
6. Add `ruleset_type` badge to ruleset editor, cards, and analysis page

---

### v0.11 — AI-Assisted Ruleset Creation
**Goal:** users can describe a ruleset in plain text and have the AI generate the condition rows automatically.

**New dependency:** `anthropic` Python SDK

**New config section in `config.yml`:**
```yaml
ai:
  enabled: true
  anthropic_api_key: "sk-ant-..."
  model: "claude-sonnet-4-6"
```

**New API endpoint:** `POST /api/ai/generate-ruleset`
- Auth: required
- Body: `{ "description": "string", "station_hints": [station_id, ...] }` — optional station IDs for context
- Calls Claude API with a structured system prompt that explains the full condition schema: fields, operators, colours, group_id semantics
- Returns: `{ "name": "string", "ruleset_type": "risk|opportunity", "conditions": [RuleConditionCreate, ...], "explanation": "string" }`
- Response is a **preview only** — user must review and save manually; nothing is persisted by this endpoint

**Frontend changes in `ruleset-editor.html`:**
- "Generate with AI" expandable panel above the condition builder
- Textarea for natural language description
- Optional station search for hints
- Submit calls `/api/ai/generate-ruleset`, populates condition rows from response
- Shows the AI's explanation text in a dismissible info box
- User can edit any condition row before saving

**New files:**
- `src/lenticularis/api/routers/ai.py` — mounted at `/api/ai`; config-gated (disabled if `ai.enabled = false`)

#### Implementation steps for v0.11
1. Add `ai` section to `config.py` (`AiConfig` Pydantic model, optional)
2. Add `anthropic` to `pyproject.toml`
3. Implement `api/routers/ai.py` with `POST /api/ai/generate-ruleset`; write system prompt encapsulating full condition schema
4. Add AI generation panel to `static/ruleset-editor.html`
5. Register router in `api/main.py` (only if `ai.enabled`)

---

### v0.12 — Station Deduplication (Station Groups)
**Goal:** admin can group equivalent weather stations from different networks (e.g. a Holfuy and a MeteoSwiss station at the same mountain) so that the UI shows only the one with the most recent data.

**New SQLite tables:**

**`station_groups`**
```
id                 UUID PK
name               TEXT NOT NULL   -- e.g. "Kleine Scheidegg"
primary_station_id TEXT NOT NULL   -- preferred station_id (admin pick, used as canonical ID)
created_at         DATETIME
```

**`station_group_members`**
```
group_id   UUID FK → station_groups.id CASCADE
station_id TEXT NOT NULL
PRIMARY KEY (group_id, station_id)
```

**Logic change in `stations` router** (`GET /api/stations`):
- After fetching all latest measurements from InfluxDB, load all station groups from SQLite
- For each group: find the member with the most recent timestamp; suppress the others from the response; surface the winner under the `primary_station_id` identity (name, network stay as the actual winning station's, but `station_id` is rewritten to the primary)
- The deduplication happens at the API layer, not in InfluxDB

**Admin endpoints** (all require admin, exposed under `/api/admin/station-groups`):
- `GET /api/admin/station-groups`
- `POST /api/admin/station-groups`
- `PUT /api/admin/station-groups/{id}`
- `DELETE /api/admin/station-groups/{id}`

**Frontend:**
- `static/admin/station-groups.html` — admin UI to create/manage groups (station search autocomplete)
- `static/stations.html` — grouped stations show a "grouped" badge; tooltip: "Showing freshest of N sources"

#### Implementation steps for v0.12
1. Add `StationGroup`, `StationGroupMember` ORM models to `database/models.py`
2. Add Pydantic schemas; implement dedup logic in stations router
3. Add admin endpoints to `api/routers/admin.py`
4. Create `static/admin/station-groups.html`
5. Add grouped badge to `static/stations.html`

---

### v1.0 — Organizations, User Roles & Admin Backend
**Goal:** introduce commercial customer organisations, expand user roles, and build a full admin backend UI.

#### User role expansion
Add `subscription TEXT NOT NULL DEFAULT 'free'` column to `users` table.
Roles: `'pilot'` (existing), `'admin'` (existing), `'customer'` (new).
Subscription tiers: `'free'`, `'premium'`.

#### New SQLite tables

**`organizations`**
```
id           UUID PK
name         TEXT NOT NULL
slug         TEXT UNIQUE NOT NULL   -- URL-friendly identifier
description  TEXT
created_at   DATETIME
```

**`organization_members`**
```
org_id   UUID FK → organizations.id CASCADE
user_id  UUID FK → users.id CASCADE
org_role TEXT NOT NULL DEFAULT 'member'   -- 'owner' | 'member'
PRIMARY KEY (org_id, user_id)
```

**`organization_rulesets`**
```
org_id     UUID FK → organizations.id CASCADE
ruleset_id UUID FK → rulesets.id CASCADE
label      TEXT       -- custom display label for the dashboard
sort_order INT DEFAULT 0
PRIMARY KEY (org_id, ruleset_id)
```

#### API endpoints

**Admin** (`/api/admin`):
- `GET /api/admin/users` — list with role/subscription filters
- `PUT /api/admin/users/{id}` — update role, subscription, is_active
- `GET /api/admin/organizations` — list all orgs
- `POST /api/admin/organizations` — create org
- `PUT /api/admin/organizations/{id}` — update org
- `POST /api/admin/organizations/{id}/members` — add member
- `DELETE /api/admin/organizations/{id}/members/{user_id}` — remove member
- `PUT /api/admin/organizations/{id}/rulesets` — assign/update ruleset list for org

**Organizations** (`/api/organizations`):
- `GET /api/organizations/{slug}/dashboard` — org-scoped ruleset list with current evaluations; auth required (org member or admin)

#### Frontend admin pages (all gate on `role == 'admin'`, redirect to `/login` otherwise)
- `static/admin/index.html` — admin home: user count, org count, collector health summary
- `static/admin/users.html` — user table with inline role/subscription editor
- `static/admin/launch-sites.html` — approve/edit/delete launchsites; link to clubs
- `static/admin/clubs.html` — club CRUD with GeoJSON polygon editor (textarea + Leaflet preview)
- `static/admin/organizations.html` — org CRUD + member management + ruleset assignment
- `static/admin/station-groups.html` — station group CRUD (from v0.12, moved here for consistency)

#### Customer dashboard
- `static/dashboard.html?org={slug}` — org-scoped dashboard showing assigned rulesets with large go/no-go decision blocks (suitable for VKPI daily briefing or Jungfraubahn control room)
- Customers (`role == 'customer'`) are restricted to this view; no ruleset editor access

#### Implementation steps for v1.0
1. Add `subscription` column migration for `users`; add `Organization`, `OrganizationMember`, `OrganizationRuleset` ORM models
2. Update `models/auth.py` `UserOut` to expose `subscription`; update `require_admin` dep (no change needed, role check stays same)
3. Implement `api/routers/admin.py` (expanded) and `api/routers/organizations.py`
4. Create all `static/admin/*.html` pages
5. Create `static/dashboard.html` customer dashboard

---

### v1.2 — XContest Flight Statistics
**Goal:** each launchsite shows historical flight statistics fetched from the XContest API (flight count, average/max distance, top pilot).

**Requires:** v0.10 launchsites table. XContest API credentials.

**New SQLite table:**
```
xcontest_stats
  id              UUID PK
  launch_site_id  UUID FK → launch_sites.id CASCADE
  year            INT
  flight_count    INT
  avg_distance_km FLOAT
  max_distance_km FLOAT
  top_pilot       TEXT
  fetched_at      DATETIME
```

**New collector:** `collectors/xcontest.py`
- Queries XContest API by GPS launch coordinates + configurable search radius (default 500 m)
- Maps results to launchsites; writes aggregated yearly stats to `xcontest_stats`
- Runs as a **daily job** in the scheduler (not a weather-interval job)

**New config section:**
```yaml
xcontest:
  enabled: false
  api_key: ""
  search_radius_m: 500
```

**API endpoint:** `GET /api/launch-sites/{id}/stats` — returns `xcontest_stats` rows + OGN stats (placeholder until v1.3)

**Frontend:** `static/launch-site-detail.html` stats cards show flight count, avg/max distance, top pilot per year.

#### Implementation steps for v1.2
1. Add `XContestStats` ORM model; migration
2. Implement `collectors/xcontest.py`
3. Wire daily job in `scheduler.py`
4. Add `xcontest` config section to `config.py`
5. Implement `GET /api/launch-sites/{id}/stats` endpoint
6. Update `static/launch-site-detail.html` with stats cards

---

### v1.3 — Open Glider Network (OGN) Integration
**Goal:** enrich launchsite detail pages with OGN launch-track statistics and add a live OGN aircraft overlay to the map.

**Two components:**

**A) Historical launch stats per launchsite:**
- New collector `collectors/ogn.py` — queries OGN historical API (glidernet.org) for tracks starting within configurable radius + altitude band of each launchsite
- New SQLite table: `ogn_stats` (similar schema to `xcontest_stats`: launch_site_id, year, flight_count, fetched_at)
- Daily scheduled job; results exposed via `GET /api/launch-sites/{id}/stats`

**B) Live OGN aircraft overlay on map:**
- New API endpoint: `GET /api/ogn/live` — fetches last N minutes of OGN APRS data for a configurable bounding box; returns GeoJSON FeatureCollection of aircraft positions + track lines
- Map toggle layer in `index.html` showing live glider positions (auto-refresh every 60 s when layer is active)

**New config section:**
```yaml
ogn:
  enabled: false
  bounding_box: [5.9, 45.8, 10.5, 47.8]   # Switzerland
  live_minutes: 30
  search_radius_m: 500
```

#### Implementation steps for v1.3
1. Add `OgnStats` ORM model; migration
2. Implement `collectors/ogn.py` (historical stats)
3. Wire daily job in `scheduler.py`
4. Implement `GET /api/ogn/live` endpoint (live proxying)
5. Add OGN toggle layer to `static/index.html` / `map.js`
6. Update `static/launch-site-detail.html` with OGN launch count

---

### v1.4 — Notifications
**Goal:** pilots receive alerts when a launch site changes traffic light status.

- `services/notifications.py` — status-transition detection; dispatch via email (`aiosmtplib`) and Pushover
- `notification_configs` SQLite table (from v0.3 schema) fully used
- `GET/POST/PUT/DELETE /api/notifications` endpoints
- Notification config UI on the launch site detail page (channel + transition filter)
- `aiosmtplib` + Pushover HTTP API integration

#### Implementation steps for v1.4
1. Add `aiosmtplib` to `pyproject.toml`
2. Implement `services/notifications.py` (transition detection, email + Pushover dispatch)
3. Call notification service from evaluator after each decision write
4. Implement `api/routers/notifications.py`
5. Add notification config UI to launch site detail popup/page

---

### v1.5 — Full MVP Release
**Goal:** stable, tested, fully deployable release of all core features.

- All endpoints from the API contracts section are implemented and tested
- Integration test suite: user → site → ruleset → evaluations → stats → notifications
- Unit tests for all condition operators, pressure delta, AND/OR logic, both combination modes, all 7 stat functions
- Docker multi-stage `Dockerfile`, `docker-compose.yml` with health checks and `restart: unless-stopped`
- `docker-compose.dev.yml` with live volume mounts and Traefik labels
- `README.md` updated with full setup and deployment instructions
- Mobile-responsive CSS pass on all pages

#### Implementation steps for v1.5
1. Write pytest unit tests for all collectors, operators, evaluator, stats
2. Write integration test (full user journey)
3. Final Docker / docker-compose polish (multi-stage build, health checks)
4. CSS mobile-responsive pass across all static pages
5. Update `README.md` with deployment guide

---

### v1.6 — Community Rule Gallery
**Goal:** pilots can publish their rule sets for others to discover and clone.

- `is_public`, `clone_count`, `cloned_from_id` fields on rulesets fully enforced
- `GET /api/gallery`, `GET /api/gallery/{id}`, `POST /api/gallery/{id}/clone`
- Gallery page in the frontend: searchable list of public rule sets, clone button, shows `clone_count`
- Clone creates an independent copy under the cloning pilot's account

#### Implementation steps for v1.6
1. Implement `api/routers/gallery.py`
2. `POST /api/rulesets/{id}/publish` + `unpublish` endpoints
3. Create `static/gallery.html` + gallery UI in `static/app.js`

---

### v1.7 — Additional Collectors (Holfuy, Windline, Ecovitt)
**Goal:** add the three API-key-based personal-station networks for pilots who own or use those devices.

> These collectors involve third-party API keys, non-trivial authentication flows, and proprietary data shapes. Deferred until the core product is stable.

- `collectors/holfuy.py` — wind speed / gust / direction (Holfuy API key)
- `collectors/windline.py` — wind speed / direction (Windline API key)
- `collectors/ecovitt.py` — full personal weather station sensor set (Ecovitt API key)
- All three wired into `scheduler.py` and admin collector endpoints
- Unit tests: `normalize_data()` for each new collector

#### Implementation steps for v1.7
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
- Integration test: create user → site → rule set with multi-station conditions → trigger evaluations → query stats endpoints → assert Status Ok day count matches seeded decisions
- Manual: open rule editor, build a rule mixing Station A (wind speed) and Station B (wind direction), save, run scheduler, open stats dashboard, verify condition trigger chart attributes correctly to each station
- Community gallery: publish as user 1 → clone as user 2 → verify `clone_count` increments

---

## Key Decisions

- Rules are 100% user-owned; admin limited to collector config, user management, launchsite approval, and org management
- Station picker is **per condition row** — no ruleset-level station selection — enabling multi-station rules natively
- `condition_results` JSON (including `station_id`) written per evaluation to InfluxDB enables per-station trigger statistics without raw weather re-queries
- Condition tree stored normalised in `rule_conditions` table (not a JSON blob) for queryability
- Pressure delta is a first-class condition type with two-station picker
- Best-windows metric computed server-side (not Flux) for simplicity
- Rule sharing is clone-only (no co-editing); private by default, opt-in publish
- Chart.js for all charts (lightweight, no framework required)
- MeteoSwiss, SLF, and METAR are the primary no-auth collectors; Holfuy, Windline, and Ecovitt are deferred to v1.7
- **LaunchSites are a separate entity** (`launch_sites` table, v0.10+): rulesets retain their own embedded location but can optionally be linked to an official launchsite entry. The launchsite is the stable anchor for XContest/OGN statistics and club area assignments. This supersedes the original "no separate `launch_sites` table" decision.
- **`site_type`** on rulesets is `'launch'` or `'landing'` (was a free string in the original spec; simplified to the two implemented values)
- **`ruleset_type`** (`'risk'` | `'opportunity'`): risk = current behaviour (green = safe, red = stop); opportunity = semantic inversion (green = good conditions exist, e.g. mountain is soarable)
- **Launch–landing linking**: a launch ruleset links to ≥0 landing rulesets via `launch_landing_links`; evaluate returns both decisions; landing decisions also written to `rule_decisions`
- **Map halo**: launch markers have a coloured halo showing the best landing decision; no halo = no landings linked; landing markers use a distinct icon
- **Traffic light labels**: green = Status Ok, orange = Warning, red = Stop
- **Station deduplication** (v0.12+): admin creates `station_groups` linking equivalent stations from different networks; API layer surfaces only the freshest measurement per group under the primary station ID
- **Organisations** (v1.0+): commercial customers (VKPI, Jungfraubahn) belong to an org; admin assigns rulesets to org dashboards; customers have read-only dashboard access; no ruleset editor
- **AI ruleset generation** (v0.11+): natural language → condition JSON via Claude API; response is always a preview — user must explicitly save; endpoint is config-gated
- **XContest integration** (v1.2+): requires XContest API credentials; statistics fetched per launchsite daily; stored in `xcontest_stats` SQLite table (not InfluxDB)
- **OGN integration** (v1.3+): historical launch-track statistics per launchsite (daily); live APRS overlay on map (60 s refresh when layer active)