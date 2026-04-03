# Plan: Lenticularis — Product Spec & Implementation Brief (v2.3)

## Overview

Lenticularis is a weather aggregation and paragliding decision-support system for Switzerland. It collects data from multiple weather networks, normalises and stores it in InfluxDB, and lets each pilot build graphical per-site rule sets using a condition builder. Each condition targets a specific station — a single rule set can freely combine data from multiple stations. The system produces GREEN/ORANGE/RED decisions, stores full per-condition decision history, and exposes a statistics dashboard showing flyability patterns over time.

Core differentiator: **rules are fully pilot-owned and self-served** through a graphical editor. No admin-imposed logic.

---

## User Roles

| Role | Responsibilities |
|---|---|
| **Pilot** | Manage own launch sites, build own rule sets, view stats, configure notifications, share/clone rule sets |
| **Customer** | Read-only; sees only rulesets assigned by admin |
| **Admin** | Manage user accounts, organisations, enable/disable collectors — no involvement in personal rules |
| **Org Admin** | Manage rules for their organisation (`org_id` required); sees org rulesets; org-scoped editor |
| **Org Pilot** | Read-only member of an organisation; can see org dashboard detail view |

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

- `organizations` — `id`, `slug` (unique), `name`, `description`, `created_at`
- `users` — `id`, `username`, `email`, `hashed_password`, `role`, `org_id` FK → organizations, `created_at`
- `weather_stations` — `station_id`, `name`, `network`, `latitude`, `longitude`, `elevation`, `canton`, `active`
- `launch_sites` — `id`, `name`, `latitude`, `longitude`, `owner_id` FK → users
- `rulesets` — `id`, `name`, `description`, `launch_site_id`, `owner_id`, `org_id` FK → organizations, `combination_logic`, `is_public`, `clone_count`, `cloned_from_id`, `created_at`, `updated_at`
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

### v1.2 — Webcam Links + Preset Launch Sites + Map Fixes ✅ Shipped

**Webcam integration**
- New `ruleset_webcams` SQLite table: `id`, `ruleset_id`, `url`, `label`, `sort_order`; migration in `db.py`
- `PUT /api/rulesets/{id}/webcams` — full-replace list; pilots manage their own rulesets
- Ruleset editor: webcam section with URL + label rows, add/remove buttons
- Ruleset analysis page: webcam cards grid; Roundshot URLs get a blue "Roundshot" badge; "↗ Open" link
- `WebcamBase` / `WebcamOut` / `WebcamsReplaceRequest` Pydantic schemas; `webcams` field on `RuleSetDetail`

**Preset launch sites**
- `is_preset` boolean column on `rulesets` table; idempotent migration
- `GET /api/rulesets/presets` — returns `list[RuleSetDetail]` (with conditions) for all pilots
- `PUT /api/rulesets/{id}/set_preset?is_preset=bool` — admin-only toggle
- Ruleset editor: collapsible preset picker panel; admin ⭐ toggle; active-preset banner
- Admin panel: "Preset Sites" tab — table of current presets with condition/webcam counts and "Remove preset" action

**Decision history + forecast API**
- `GET /api/rulesets/{id}/history?hours=N` — queries `rule_decisions` from InfluxDB; parses `condition_results_json`; annotates `in_active_window` via sunrise if lat/lon set
- `GET /api/rulesets/{id}/forecast?hours=N` — calls `run_forecast_evaluation`; returns `{ steps, active_window_hours }`
- Fixed frontend spinner hang on non-OK response in `loadHistory`

**Map fixes**
- `/evaluate` endpoint now evaluates linked landing rulesets and returns `landing_decisions` + `best_landing_decision` → restores the landing-site ring on launch site markers
- Custom Leaflet pane `rulesetPane` at z-index 450 (below default markerPane 600) → weather arrows always render in front of ruleset dots

### v1.3 — Forecast Accuracy Dashboard ✅ Shipped

**Goal:** For any past time window, show how closely past model runs matched observed values per station and field.

**Architecture:**
- `weather_data` = observed (tags: `station_id`, `network`, `canton`)
- `weather_forecast` = forecasts, now tagged with `init_date` (YYYY-MM-DD) so each calendar day of model runs is its own InfluxDB series — enabling per-day accuracy comparisons without overwriting history
- `query_forecast_accuracy()` in `influx.py` fetches actuals + per-init_date forecast series for a station/window; handles legacy data (no `init_date` tag) as a fallback series

**Shipped:**
- `GET /api/stations/{id}/forecast-accuracy?from=&to=` — returns `{actual, forecasts: [{init_date, data}]}` sorted newest-first; defaults to 2-days-ago window so both observations and forecasts are always available
- `static/forecast-accuracy.html` + `static/forecast-accuracy.js` — station picker, date picker, per-field Chart.js charts with actual (solid) + one overlaid line per model-run day; cursor crosshair plugin; "📊 Accuracy" button on station-detail page
- All 4 i18n files updated (`forecast_accuracy.*` keys, `station_detail.accuracy_button`)

**Collector improvements (shipped alongside v1.3):**
- `write_forecast` now tags `init_date` (YYYY-MM-DD) so same-day runs overwrite each other in InfluxDB; cross-day runs are kept independently
- Layered forecast schedule: `open-meteo-short` (180 min, 30h horizon) refreshes near-term data every 3 h; `open-meteo` (1440 min, 120h horizon) updates the extended window once per day — aligns with ICON-seamless update cadence and reduces API calls from 24×/day to 9×/day per station
- `collect_all_iter` distributes per-station HTTP requests evenly across the interval (`spread_seconds = interval_minutes × 60`) to avoid rate-limit bursts; writes to InfluxDB immediately after each station fetch
- Fixed `query_forecast_replay`: adding `init_date` to the pivot `rowKey` broke old-format data (no `init_date` tag) — reverted rowKey to the original 5-column set; Python dedup handles multiple `init_date` series correctly across tables

### v1.4 — Opportunity Site Type ✅ Shipped

- `site_type` extended to three values: `launch` | `landing` | `opportunity`
- **Opportunity**: a special location or condition window (thermal spot, XC window, favourable conditions) — independent of launch/landing semantics
- Rule editor: 3-button site-type toggle; opportunity shows a hint text explaining the concept
- Map: opportunity rulesets render as a **diamond marker** (`✦`) instead of a circle/flag — visually distinct at a glance; subtle glow when GREEN
- Ruleset cards: lime-green badge for opportunity sites
- InfluxDB `rule_decisions` now tagged with `site_type` (launch/landing/opportunity)
- AI rule suggestions (`POST /api/ai/suggest-conditions`) also shipped in this cycle (Ollama-powered)

### v1.6 — Help / FAQ + AI Input Improvements ✅ Shipped

- `static/help.html`: accordion FAQ page (12 sections, jump bar, anchor deep-links); `/help` route in `main.py`; `nav.help` key in all 4 locales; Help nav link on all pages
- Contextual `?` tooltip buttons (`.help-tip`) on rule editor, Föhn page, stats page linking to relevant help anchors
- **AI input normaliser** (`_normalize_description`): regex pipeline converts natural-language wind direction terms (DE/FR/IT/EN) to explicit degree ranges before the Ollama prompt is built — e.g. `Südkomponente → [in_direction_range 113–248°]`, `Windböen unter 25km/h → wind_gust < 25 km/h`
- **Fuzzy station name matching** (`_fuzzy_station_hints`): resolves abbreviations/local names (e.g. "amis" → Amisbühl) via prefix/substring match
- **Geographic station matching** (`_geo_station_hints`): detects Swiss location names in description, returns nearby stations by haversine distance; elevation-filtered when "same height / gleiche Höhe" mentioned; `_KNOWN_LOCATIONS` table covers ~50 Swiss PG/mountain sites
- `StationHint` extended with `latitude`, `longitude`, `elevation`; frontend passes these from `allStations`
- System prompt hardened: multilanguage input, compass reference table with broad-sector shortcuts, "one condition per station" rule for group references, prompt injection mitigation via `<input>` delimiters
- **Bug fix**: `GET /api/rulesets` now filters `org_id IS NULL` — org rulesets no longer appear on the personal map

### v1.5 — Multi-tenant Org System (VKPI) ✅ Shipped

**Goal**: Replace WhatsApp-based go/no-go coordination for commercial tandem operators (VKPI Interlaken) with a dedicated subdomain dashboard. Generic org layer to scale to future customers.

**Architecture**:
- `organizations` SQLite table (`id`, `slug`, `name`, `description`)
- `org_id` nullable FK on `User` and `RuleSet` (idempotent `ALTER TABLE` migrations)
- Two new roles: `org_admin`, `org_pilot` (scoped via `user.org_id`)
- System `admin` bypasses all org guards (can access any org)

**Backend**:
- `routers/org.py`: `GET /api/org/{slug}/status` (public), `GET /api/org/{slug}/dashboard` (org member), `GET /api/org/{slug}/rulesets` (org admin)
- `dependencies.py`: `require_org_member`, `require_org_admin`; system admin always passes
- `routers/admin.py`: `GET/POST /api/admin/orgs`; user update extended with `org_id`
- `main.py`: subdomain-aware root handler — unknown subdomain → `org-dashboard.html`; explicit `/org/{slug}` route for dev path access
- `RuleSetCreate`: optional `org_slug` field; backend resolves to `org_id` on create

**Frontend**:
- `org-dashboard.html`: public traffic-light circle; authenticated members see condition breakdown table + 24h history strip (colour-bucketed cells); admin sees "Manage rules" link
- `rulesets.html` + `ruleset-editor.html`: `?org={slug}` mode — org nav (slug as brand, "Personal workspace →" link that strips org subdomain), org-only ruleset list, org-scoped editor (no Opportunity, no Public/Private toggle, landing picker filtered to org-owned landing rulesets)
- `admin.html`: Organisations tab (create form + table), users table gains Organisation dropdown + `org_admin`/`org_pilot` role options
- All 4 i18n files: `org.*`, `admin.orgs.*`, `rulesets.org_title/org_subtitle/back_to_org`, `nav.personal_workspace`

**Infra**:
- `docker-compose.dev.yml`: Traefik `lenticularis-dev-vkpi` router for `vkpi.lenti-dev.lg4.ch`; explicit `.service=` on both routers to avoid Traefik ambiguity

---

## Backlog (unordered — pick any item next)

### VKPI Safetychat replacement (high priority for VKPI org)

These features replace the current WhatsApp-based go/no-go coordination workflow described at vkpi.ch/safety/vkpi-safetychat/.

- **TIMEOUT button**: prominent button on org dashboard; any org member can trigger; requires a reason (free text or quick-pick: Outflow / Wind / Front approaching / Landing turbulence); sends push notification to all org members immediately; stored with timestamp and caller identity.

- **In-app voting**: 10-minute voting window opens after a TIMEOUT; each daily lead pilot casts one vote (🔴 Stop / 🟠 Continue with caution); auto-tally at 10 min with VKPI tie-break (tie = Red); non-responding companies counted as accepting majority; result and full vote record stored permanently. New SQLite table: `org_timeouts` (`id`, `org_id`, `called_by`, `reason`, `called_at`, `voting_closes_at`, `outcome`, `weather_snapshot_json`); `org_timeout_votes` (`id`, `timeout_id`, `company_id`, `pilot_id`, `vote`, `cast_at`).

- **Daily lead pilot designation**: org members can mark themselves as daily lead for the day; only the daily lead can cast their company's vote in a TIMEOUT; visible on org dashboard so all members know who is responsible. New column `is_daily_lead` (boolean, reset at midnight) on `users` or a separate `daily_leads` table scoped per org per day.

- **Automatic TIMEOUT suggestion**: when Lenticularis detects a Green → Orange or Green → Red transition on any org ruleset, surface a "⚠ Conditions changed — call TIMEOUT?" prompt to all logged-in org members (no forced trigger, human still decides).

- **Resumption tracking**: after a Red decision, dashboard shows a 30-minute countdown; monitors org rulesets; sends push notification when conditions recover to Green ("Conditions improved — resumption possible").

- **Decision audit log**: every TIMEOUT event stored with: caller, reason, live weather snapshot at trigger time (wind/gust/direction from key stations pulled from InfluxDB), per-company votes, outcome, resumption timestamp. Exportable as CSV. Replaces WhatsApp chat history as the official decision record. Endpoint: `GET /api/org/{slug}/timeouts?from=&to=`.

- **Company layer within org**: lightweight grouping of users into companies within an org (e.g. "Air Taxi Interlaken", "Paragliding Interlaken" under VKPI umbrella); one-vote-per-company logic in TIMEOUT; company name shown on dashboard. New SQLite table: `org_companies` (`id`, `org_id`, `name`); `company_id` FK on `users`.



- **Org statistics page**: per-organisation flyability statistics (same metrics as personal stats but aggregated across all org rulesets); accessible at `/org/{slug}/stats` for org members

- **Customer role — scoped access**: `customer` users can only see rulesets explicitly shared with them; admin assigns which rulesets a customer can view; no rule editing, no gallery; read-only analysis + map view

- **Trusted users + field condition reports**: `is_trusted` boolean on `User` (admin-toggled); trusted pilots submit brief on-site reports (wind approx, direction, conditions, notes); new `weather_reports` SQLite table; `POST /api/reports` + `GET /api/reports?lat=&lon=&radius_km=&hours=`; reports shown as pins on map

- **AI weather analysis** (Ollama/Claude): scheduled job every 6 h; compares trusted-user reports against nearest station measurements; flags discrepancies; stores insights in `ai_insights` table; optional map overlay + `ai-insights.html` page (admin-visible initially)

- **Push notifications — FCM**: `fcm_tokens` SQLite table; `POST /api/notifications/fcm-register`; `services/push_fcm.py` dispatches on ruleset status transitions (RED↔GREEN); `config.yml.example` gets `firebase_service_account_json`

- **Email / Pushover alerts**: status-transition alerts via email or Pushover; user-configurable per ruleset; `notification_configs` table

- **Flutter mobile app** (separate repo `lenticularis-app`): Flutter native app consuming the existing REST API; screens: Map, My Sites, Stations, Föhn, Report conditions (GPS auto-fill), Admin; Play Store + App Store

- **OGN live glider overlay**: toggleable Leaflet layer with live glider positions from OGN APRS feed; backend WebSocket proxy `/api/ogn/stream` filtered to Swiss bounding box

- **OGN launch statistics**: detect takeoffs from OGN tracks near known launch coordinates (altitude gain >50 m within 500 m); store daily takeoff counts in InfluxDB `ogn_takeoffs`; stats dashboard: activity vs ruleset decision chart

- **xcontest correlation**: correlate xcontest.org flight dates near a site with ruleset decision history; "rule accuracy" card on `ruleset-analysis.html` (% of flight days that matched GREEN)

- **Club area overlay**: toggleable map layer with club coverage polygons from a manually-maintained GeoJSON; popup: club name, website, contact; admin UI to upload/edit GeoJSON

- **Duplicate station handling**: admin marks two stations as same physical location with a priority order; map and API surface the station with the most recent data; admin UI: Station aliases table

- **Wind rose chart**: replace direction scatter on station-detail with a proper wind rose (Chart.js polar area or custom SVG)

- **Additional collectors**: Holfuy (API key), Windline (API key)

- **Performance pass**: InfluxDB query profiling; downsampling task for data older than 90 days

- **Auto-clone preset on nearby site creation**: when a pilot creates a new ruleset near a known preset launch coordinate, offer to auto-apply that preset

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