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

### v0.1 — Station Detail Page ✅ Shipped
MeteoSwiss collector + InfluxDB write pipeline + station API + station-detail chart page.

### v0.2 — Live Map ✅ Shipped
Leaflet.js map as landing page with station markers and latest-measurement popups.

### v0.3 — Auth & User Management ✅ Shipped
JWT register/login, `get_current_user` / `require_admin` dependencies, Google OAuth, SQLite via SQLAlchemy + Alembic.

### v0.4 — Launch Sites ✅ Shipped
Pilot-owned launch site CRUD; site markers on map with distinct icon.

### v0.5 — SLF + METAR Collectors ✅ Shipped
`collectors/slf.py` (30 min) and `collectors/metar.py` (15 min, AviationWeather); full scheduler.

### v0.6 — Rule Editor ✅ Shipped
`static/ruleset-editor.html` condition builder: per-row station picker, field/operator/value rows, AND/OR nesting, direction compass, pressure-delta two-station mode, live preview, save.

### v0.7 — Rules Evaluator + Traffic Lights ✅ Shipped
`rules/evaluator.py` live evaluator + `run_forecast_evaluation`; traffic light badges on map; `GET /api/rulesets/{id}/forecast`; ruleset card list with live badge (`static/rulesets.html`).

### v0.8 — Forecast Pipeline + Replay ✅ Shipped
`collectors/forecast_meteoswiss.py` ICON-CH1/CH2 GRIB2 pipeline; `GET /api/stations/replay` with obs + forecast merge; map time-navigation (day buttons, hour slider); forecast colour-strip on ruleset cards; `static/ruleset-analysis.html` per-condition history/forecast analysis dashboard.

### v0.9 — Statistics Dashboard ✅ Shipped
`static/stats.html` flyability statistics (hourly heatmap, monthly breakdown, seasonal, condition trigger leaderboard, site comparison, best windows); map day/hour replay navigation.

### v0.10 — Föhn Monitor + Wunderground + Personal Stations ✅ Shipped
`collectors/wunderground.py` personal weather station collector (Ecovitt/Weather Underground API); virtual föhn stations (computed from N–S pressure delta pairs, written to `weather_data`); `static/foehn.html` föhn dashboard; personal-station toggle on map (hide/show Wunderground stations).

### v1.0 — Multilanguage + Mobile-Responsive UI ✅ Shipped

**Multilanguage (EN / DE / FR / IT)**

- **Translation files**: `static/i18n/{en,de,fr,it}.json` — flat key tree covering all UI strings
- **Core engine**: `static/i18n.js` — `initI18n()`, `t(key, vars?)`, `applyDataI18n()`, `renderLangPicker()`
- **HTML markup**: `data-i18n="key"` on all static text nodes; `data-i18n-placeholder="key"` on inputs
- **JS dynamic strings**: all popup/chart/error strings use `window.t('key')` calls
- **Language picker**: dropdown injected into nav by `renderLangPicker()`; persists to `localStorage`
- **Auto-detection**: `navigator.language` slice on first visit; falls back to `en`
- **Timing guard**: non-module scripts (e.g. `map.js`) use `typeof window.t === 'function' ? window.t : k => k` to avoid `window.t is not a function` before `initI18n()` completes
- **Lazy config**: objects whose values call `window.t()` are wrapped in getter functions (e.g. `getFieldLabel()`) rather than module-level constants, so they evaluate post-init

**Mobile-Responsive UI**

- **`static/shared.css`**: single shared stylesheet linked by all pages; contains all mobile overrides using `!important` to win against per-page inline `<style>` blocks
- **Hamburger nav**: `auth.js` `renderNavAuth()` injects a `<button class="nav-hamburger">` into `.top-nav` on every page; click toggles `.nav-links.open`; nav collapses at ≤640 px
- **Grid fixes**: `station-detail` chart grid (`minmax(560px,1fr)` → `1fr`); foehn region grid (`minmax(340px,1fr)` → `1fr`)
- **Toolbars**: inputs and selects stretch full-width; page padding reduced on small screens
- **Stats/rulesets/editor**: tab bar scrolls horizontally; card actions stack; form rows collapse to single column
- **Tagged `v1.0`** in git

---

### v1.1 — Admin GUI + Role Expansion + Föhn Config Editor ✅ Shipped

- New role: `customer` (read-only access to assigned sites/rulesets, no rule editing) — roles: `pilot` | `customer` | `admin`
- Admin panel UI (`static/admin.html`): user table (list, activate/deactivate, change role), collector status (enabled/disabled toggle, interval override, last-run timestamp, error count), Föhn config editor (add/edit/remove pressure pairs and wind regions, persisted to `data/foehn_config.json`)
- `api/routers/admin.py`: `GET/PUT /api/admin/users`, `GET/PUT /api/admin/collectors`, `GET/PUT/DELETE /api/admin/foehn-config`
- Collector trigger-now button in admin panel

### v1.2 — Webcam Integration (planned)

**Goal:** Link external webcam URLs to launch sites; show them in map popups and site detail.

- New SQLite table `site_webcams`: `id`, `ruleset_id` (FK → rulesets), `url`, `label`, `provider`, `sort_order`, `is_active`, `added_by_id` (FK → users)
- **Pilots** can add/edit/delete webcams on their own sites via the ruleset editor
- **Admins** can add webcams to any site via the admin panel
- Map popup: webcam thumbnail link (static preview image URL if provider supports it, e.g. Roundshot `?latest=1`) + "Open cam" button
- API: `GET/POST /api/rulesets/{id}/webcams`, `PUT/DELETE /api/rulesets/{id}/webcams/{wid}`
- i18n keys for all 4 languages

**Critical files:**
- `src/lenticularis/database/models.py` — add `SiteWebcam` ORM model + migration
- `src/lenticularis/api/routers/rulesets.py` — add webcam sub-routes
- `static/map.js` or `static/index.html` — extend ruleset popup to show webcam link
- `static/ruleset-editor.html` — add webcam URL management section
- `static/admin.html` — webcam management for admin (any site)

### v1.3 — Forecast Accuracy Dashboard (planned)

**Goal:** For any past time window, show how closely 1-day-ahead / 2-day-ahead / 3-day-ahead forecasts matched observed values. Also show per-ruleset decision accuracy.

**Architecture — data already exists:**
- `weather_data` = observed (tags: `station_id`, `network`, `canton`)
- `weather_forecast` = forecasts (tags: `station_id`, `source`, `model`; field: `init_time` for deduplication)
- A past forecast for `valid_time=T` made at `init_time=T-24h` is queried by filtering `init_time ≤ T-22h AND init_time ≥ T-26h`

**New InfluxDB query method** (`database/influx.py`):
```python
def query_forecast_vs_actual(
    self, station_id: str, field: str,
    start: datetime, end: datetime,
    lead_hours: list[int] = [24, 48, 72]
) -> list[dict]:
    # Returns: [{timestamp, actual, fc_24h, fc_48h, fc_72h}, ...]
```

**New API endpoint** (`api/routers/stations.py`):
- `GET /api/stations/{id}/forecast-accuracy?field=wind_speed&from=&to=`

**New page** `static/forecast-accuracy.html`:
- Station picker + field picker + date range selector
- Chart: overlaid lines — actual (solid blue), 1-day forecast (dashed amber), 2-day (dotted orange), 3-day (dotted red)
- Summary card: RMSE and bias per lead time
- Ruleset tab: for each user's rulesets, % of hours where forecast decision matched actual decision

**Critical files:**
- `src/lenticularis/database/influx.py` — add `query_forecast_vs_actual()`
- `src/lenticularis/api/routers/stations.py` — add `/forecast-accuracy` endpoint
- `static/forecast-accuracy.html` — new page
- `static/i18n/*.json` — add keys

### v1.4 — Rule Types: Risk + Opportunity (planned)

- `RuleSet.rule_type`: `risk` (default, existing) | `opportunity`
- **Risk** (existing): conditions define limits — exceeding them → ORANGE/RED
- **Opportunity**: conditions define ideal windows — all met → separate GREEN badge
- Two independent badges per launch site on the map: safety badge (risk) + opportunity badge
- Rule editor gains rule-type toggle; condition builder UX is identical
- Evaluator writes to `rule_decisions` with tag `rule_type` = risk/opportunity

### v1.5 — Pre-seeded Launch Site Defaults (planned)

- Admin can define a default ruleset template per launch site (or a global fallback)
- New SQLite table: `site_default_rulesets` (`site_id` nullable, `ruleset_json`, `created_by_admin_id`)
- When a pilot adds a site that has a default template, the template is cloned into their account as an editable starting point
- Admin UI: Default Rules tab per site in admin panel

### v1.6 — AI Rule Building + AI Weather Analysis + Trusted Users (planned)

**Goal:** Two complementary AI features plus trusted-user field confirmations.

**(A) AI Rule Building** (existing scope from v1.4):
- Free-text input in rule editor: "wind from south under 30, gusts under 40, not raining"
- `POST /api/rulesets/ai-suggest` sends prompt + available station list to Claude API, returns a pre-filled condition tree JSON
- Pilot reviews and saves the suggested tree

**(B) AI Weather Analysis + Trusted Users** (new):
- New boolean flag `is_trusted` on `User` model (admin-toggled, no new role)
- Trusted users are experienced pilots who submit brief condition reports
- **Two report triggers**: persistent "Report conditions" button (always available) + push prompt when AI detects a station/forecast anomaly

**New SQLite table `weather_reports`:**
- `id`, `reporter_id` FK → users, `lat`, `lon`, `reported_at`
- `wind_speed_approx` (km/h range: calm / 0–15 / 15–30 / 30–50 / 50+)
- `wind_direction_approx` (N/NE/E/SE/S/SW/W/NW)
- `conditions` (enum: clear / cloudy / rain / snow / föhn / thermal)
- `notes` (free text, max 500 chars)
- `nearest_station_id` (auto-computed from lat/lon at submit time)

**New API endpoints** (`api/routers/reports.py`):
- `POST /api/reports` — submit a condition report (trusted users only)
- `GET /api/reports?lat=&lon=&radius_km=&hours=` — recent reports near a location

**AI Analysis job** (new scheduler task, runs every 6h):
- Fetches last 24h of station data + all reports in the same window
- For each report, finds nearest station(s) within 20 km and compares report values against station measurements (flag discrepancies > 2σ)
- Sends data summary + discrepancies to Claude API with a structured prompt
- Stores generated insight in a new `ai_insights` SQLite table: `id`, `generated_at`, `area` (bounding box), `insight_text`, `anomalies_json`

**UI:**
- Map: trusted users see "Report conditions" FAB button → quick modal form
- Map: optional "AI insights" layer showing area summaries (admin-visible initially)
- New `static/ai-insights.html` page (admin only initially)
- Admin panel: toggle `is_trusted` on user management table

**Requires** `claude_api_key` in `config.yml`.

**Critical files:**
- `src/lenticularis/database/models.py` — add `WeatherReport`, `AiInsight`, `is_trusted` on User
- `src/lenticularis/api/routers/reports.py` — new router
- `src/lenticularis/services/ai_analysis.py` — Claude API integration
- `src/lenticularis/scheduler.py` — new 6h AI analysis job
- `static/map.js` — "Report conditions" FAB for trusted users
- `static/admin.html` — `is_trusted` toggle in user table
- `config.yml.example` — add `claude_api_key` config key

### v1.7 — OGN Live Map Overlay + Launch Statistics (planned)

- **Live overlay**: toggleable Leaflet layer showing glider positions from OGN APRS feed
  - Backend WebSocket proxy (`/api/ogn/stream`) relays APRS messages filtered to Swiss bounding box
  - Frontend: `static/ogn.js` subscribes, places/moves glider markers
- **Launch statistics**: background job detects takeoffs from OGN tracks near known launch site coordinates
  - Takeoff heuristic: altitude gain >50 m within 500 m of site lat/lon
  - Stores daily takeoff count per site in InfluxDB `ogn_takeoffs` measurement
  - Stats dashboard: OGN launch activity vs ruleset decision chart

### v1.8 — xcontest Statistics (planned)

- Correlate xcontest.org flight dates near each site with ruleset decision history
- Rule accuracy card on `ruleset-analysis.html`: % of flight days that matched GREEN decision
- If many flights on RED days, rule may be too pessimistic

### v1.9 — Paragliding Club Area Overlay (planned)

- Map layer showing club coverage polygons from a manually-maintained GeoJSON
- Popup: club name, website, contact
- Admin UI: upload/edit club GeoJSON

### v1.10 — Duplicate Station Handling (planned)

- Admin can mark two stations as same physical location with a priority order
- Map and API surface the station with the most recent data
- Admin UI: Station aliases table

### v1.11 — VKPI / BOB White-label (planned)

- White-label map view scoped to specific sites + customer role access
- Details TBD

### v2.0 — Flutter Mobile App (Android + iOS) (planned)

**Approach:** Flutter native app in a separate repo (`lenticularis-app`), consuming the existing REST API. Play Store + App Store distribution.

**Backend additions** (in this repo, done as part of v2.0 prep):
- New SQLite table `fcm_tokens`: `id`, `user_id`, `token`, `platform` (android/ios), `created_at`
- New API endpoint `POST /api/notifications/fcm-register` — stores FCM token for a user
- New backend service `services/push_fcm.py` — dispatches FCM messages on ruleset status transitions
- `config.yml.example` — add `firebase_service_account_json` key

**Push notification triggers** (backend):
- Ruleset status transition (RED → GREEN, GREEN → RED, etc.)
- AI anomaly detected near user's sites (trusted users only)

**Flutter app screens:**
1. **Map** — flutter_map (Leaflet port) with ruleset traffic light markers; tap → popup with status + webcam link
2. **My Sites** — ruleset list with live GREEN/ORANGE/RED badges; tap → condition breakdown
3. **Stations** — station list + detail charts (fl_chart)
4. **Föhn** — föhn dashboard (pressure gradient + region cards)
5. **Report conditions** — trusted-user condition report form (lat/lon auto-filled via device GPS)
6. **Admin** (admin role only) — user management, collector status

**Critical files — backend prep:**
- `src/lenticularis/database/models.py` — add `FcmToken`
- `src/lenticularis/api/routers/notifications.py` — new router
- `src/lenticularis/services/push_fcm.py` — FCM dispatcher
- `src/lenticularis/rules/evaluator.py` — trigger push on status transition
- `config.yml.example` — FCM config

**Critical files — Flutter (separate repo):**
- `pubspec.yaml` — flutter_map, fl_chart, flutter_secure_storage, firebase_messaging, http
- `lib/main.dart`, `lib/screens/`, `lib/services/api.dart` (REST client), `lib/services/auth.dart`

---

## Decisions

| Topic | Decision |
|---|---|
| Webcam authorship | Pilots manage their own sites; admins manage any site |
| Android/iOS approach | Flutter native app in a separate repo → Play Store + App Store |
| Trusted user model | `is_trusted` boolean flag on existing pilot role (admin-toggled, no new role) |
| Report triggers | Both: persistent button (pull) + push prompt on AI anomaly (push) |

---

## Backlog (post-v2.0, unprioritised)

- **Community Rule Gallery** — `GET /api/gallery`, `POST /api/gallery/{id}/clone`; pilots can publish rule sets for others to clone
- **Notifications** — email + Pushover alerts on ruleset status transitions
- **Additional Collectors** — Holfuy, Windline (API-key networks)
- **Wind rose chart** — replace direction scatter on station-detail with a proper wind rose
- **Performance pass** — InfluxDB query profiling, downsampling task for data older than 90 days

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
- MeteoSwiss, SLF, and METAR are the primary no-auth collectors; Holfuy, Windline, and Ecovitt are deferred to a later version