# Lenticularis

Paragliding weather decision-support system for Switzerland. Collects data from multiple weather networks, stores it in InfluxDB, and lets each pilot build graphical rule sets that evaluate weather conditions across multiple stations to produce GREEN / ORANGE / RED traffic-light decisions per launch site.

## Features

- **Multi-source weather collection** — MeteoSwiss (10 min), SLF (30 min), METAR/AviationWeather (15 min), Wunderground/Ecovitt personal stations, Open-Meteo 5-day forecast
- **Interactive map** — Leaflet.js with station markers, launch-site traffic lights, time-navigation replay (past + 5-day forecast), personal-station toggle
- **Rule editor** — graphical condition builder: per-row station picker, field / operator / value, AND/OR nesting, direction compass, pressure-delta two-station mode, live preview
- **Traffic light evaluation** — live + 120 h forecast evaluation; per-condition decision history with `ruleset-analysis.html`
- **Flyability statistics** — hourly heatmap, monthly/seasonal breakdown, condition trigger leaderboard, site comparison, best consecutive-GREEN windows
- **Föhn monitor** — virtual föhn stations computed from N–S pressure deltas; dedicated `foehn.html` dashboard with live/historical/forecast modes and editable thresholds
- **Webcam links** — attach webcam URLs (incl. Roundshot with bearing) to any ruleset; shown as cards in the analysis view with a Roundshot badge
- **Preset launch sites** — admin-curated ruleset templates; pilots pick a preset when creating a new site and customise thresholds freely; admin panel "Preset Sites" tab to manage
- **Forecast accuracy dashboard** — compare past forecasts against observed values per station and field; overlaid Chart.js lines (actual + per-init_date model runs); accessible from station-detail via "📊 Accuracy" button
- **Multi-tenant org system** — `Organisation` model with `org_admin` / `org_pilot` roles; each org gets a subdomain (`vkpi.lenti.cloud`); public traffic-light dashboard + authenticated condition breakdown + 24h history strip; org-scoped ruleset editor (no personal rules mixed in)
- **Admin panel** — user management (roles: pilot / customer / admin / org_admin / org_pilot), organisation management (create org, assign users), collector status and runtime control, Föhn config editor, preset site management
- **Multilanguage UI** — EN / DE / FR / IT; auto-detected from browser, switchable from nav, persisted to `localStorage`
- **Auth** — JWT register/login; pilot-owned sites and rule sets; admin role for user/collector management; `org_id` embedded in JWT for org-scoped access
- **Docker deployment** — single `docker-compose up -d` deploys app + InfluxDB; dev overlay with live volume mounts; Traefik multi-router label pattern for org subdomains

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry |
| Time-series DB | InfluxDB 2.x |
| Relational DB | SQLite via SQLAlchemy |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| Auth | JWT via `python-jose` |
| Config | YAML (`config.yml`) validated by Pydantic |
| Frontend | Vanilla JS + Leaflet.js + Chart.js |
| Container | Docker + docker-compose |

## Getting Started

### Prerequisites

- Python 3.11+
- Poetry (`pip install poetry`)
- Docker + docker-compose

### Quick start (Docker)

```bash
git clone https://github.com/yourusername/lenticularis.git
cd lenticularis
cp config.yml.example config.yml
# Edit config.yml — set InfluxDB token, collector API keys, JWT secret
docker-compose up -d
```

- Web interface: http://localhost:8000
- API docs: http://localhost:8000/docs

### Development overlay

```bash
# Mounts src/ and static/ live; exposes via lenti-dev.lg4.ch behind Traefik
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

## Project Structure

```
src/lenticularis/
├── api/
│   ├── main.py              # FastAPI app factory + lifespan
│   ├── dependencies.py      # get_current_user, require_pilot, require_admin
│   └── routers/             # auth, stations, rulesets, stats, foehn, admin, health
├── collectors/              # MeteoSwiss, SLF, METAR, Wunderground, Ecowitt, forecast
├── database/
│   ├── models.py            # SQLAlchemy ORM
│   ├── db.py                # init_db(), get_db(), column migrations
│   └── influx.py            # InfluxDB client (write + all query methods)
├── models/                  # Pydantic schemas (weather, auth, rules)
├── rules/evaluator.py       # Live + forecast rule evaluation engine
├── services/                # Auth helpers, weather stats
├── config.py                # YAML config loader (singleton)
├── scheduler.py             # APScheduler: collector + forecast + föhn jobs
└── foehn_detection.py       # Föhn region definitions + pressure logic
static/
├── index.html + map.js      # Map dashboard + station/ruleset markers
├── rulesets.html            # Rule set cards + forecast strip (supports ?org= mode)
├── ruleset-editor.html      # Condition builder (supports ?org= mode)
├── ruleset-analysis.html    # Per-condition analysis (history + forecast)
├── org-dashboard.html       # Public traffic-light + authenticated org detail view
├── stats.html               # Flyability statistics dashboard
├── foehn.html               # Föhn monitor
├── admin.html               # Admin panel (users, orgs, collectors, föhn config)
├── stations.html + station-detail.html
├── i18n.js + i18n/          # Translation engine + EN/DE/FR/IT JSON files
├── shared.css               # Mobile-responsive overrides
└── auth.js + login.html + register.html
```

## Development Status

| Milestone | Status |
|---|---|
| v0.1 — Station detail page + MeteoSwiss collector | ✅ Shipped |
| v0.2 — Live Leaflet map | ✅ Shipped |
| v0.3 — JWT auth + admin | ✅ Shipped |
| v0.4 — Launch sites (pilot-owned, on map) | ✅ Shipped |
| v0.5 — SLF + METAR collectors | ✅ Shipped |
| v0.6 — Rule editor (condition builder) | ✅ Shipped |
| v0.7 — Rules evaluator + traffic lights + forecast evaluation | ✅ Shipped |
| v0.8 — Forecast pipeline + map time-navigation replay | ✅ Shipped |
| v0.9 — Flyability statistics dashboard | ✅ Shipped |
| v0.10 — Föhn monitor + Wunderground + personal-station toggle | ✅ Shipped |
| v1.0 — Multilanguage EN/DE/FR/IT + mobile-responsive UI | ✅ Shipped |
| v1.1 — Admin panel + customer role + Föhn config editor | ✅ Shipped |
| v1.2 — Webcam links + preset launch sites + map fixes | ✅ Shipped |
| v1.3 — Forecast accuracy dashboard | ✅ Shipped |
| v1.4 — Opportunity site type + AI rule suggestions (Ollama) | ✅ Shipped |
| v1.5 — Multi-tenant org system (VKPI) | ✅ Shipped |

Remaining work items are tracked as an unordered backlog in [plan-lenticularisStructure.prompt.md](plan-lenticularisStructure.prompt.md).

## Configuration

See [config.yml.example](config.yml.example) for all options (InfluxDB, collectors, JWT secret, API keys).

## License

MIT License
