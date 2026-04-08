# Feature History & Backlog

## Current Version: v1.8 (shipped)

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
- **Email / Pushover alerts** — status-transition alerts; user-configurable per ruleset; `notification_configs` table.
- **Flutter mobile app** — separate repo `lenticularis-app`; screens: Map, My Sites, Stations, Föhn, Report conditions (GPS auto-fill), Admin.
- **OGN live glider overlay** — toggleable Leaflet layer with live glider positions from OGN APRS feed; backend WebSocket proxy `/api/ogn/stream` filtered to Swiss bounding box.
- **OGN launch statistics** — detect takeoffs from OGN tracks near known launch coordinates; store daily takeoff counts in `ogn_takeoffs` InfluxDB.
- **xcontest correlation** — correlate xcontest.org flight dates near a site with ruleset decision history; "rule accuracy" card on `ruleset-analysis.html`.
- **Club area overlay** — toggleable GeoJSON polygon layer; admin UI to upload/edit.
- **Duplicate station handling** — admin marks two stations as same physical location with priority; map surfaces station with most recent data.
- **Wind rose chart** — replace direction scatter on station-detail with proper wind rose.
- **Additional collectors** — Windline (API key).
- **Performance pass** — InfluxDB query profiling; downsampling for data older than 90 days.
- **Auto-clone preset on nearby site creation** — when pilot creates a new ruleset near a known preset coordinate, offer to auto-apply that preset.
