# Lenticularis

Paragliding weather decision-support system for Switzerland. Collects data from multiple weather networks, stores it in InfluxDB, and lets each pilot build graphical rule sets that evaluate weather conditions across multiple stations to produce GREEN / ORANGE / RED traffic-light decisions per launch site.

## Features

- **Multi-source weather collection** — MeteoSwiss (10 min), SLF (30 min), METAR/AviationWeather (15 min), Wunderground/Ecovitt personal stations, MeteoSwiss ICON-CH1/CH2 forecast (GRIB2)
- **Interactive map** — Leaflet.js with station markers, launch-site traffic lights, time-navigation replay (past + 5-day forecast), personal-station toggle
- **Rule editor** — graphical condition builder: per-row station picker, field / operator / value, AND/OR nesting, direction compass, pressure-delta two-station mode, live preview
- **Traffic light evaluation** — live + 120 h forecast evaluation; per-condition decision history with `ruleset-analysis.html`
- **Flyability statistics** — hourly heatmap, monthly/seasonal breakdown, condition trigger leaderboard, site comparison, best consecutive-GREEN windows
- **Föhn monitor** — virtual föhn stations computed from N–S pressure deltas; dedicated `foehn.html` dashboard
- **Multilanguage UI** — EN / DE / FR / IT; auto-detected from browser, switchable from nav, persisted to `localStorage`
- **Auth** — JWT register/login + Google OAuth; pilot-owned sites and rule sets; admin role for user/collector management
- **Docker deployment** — single `docker-compose up -d` deploys app + InfluxDB; dev overlay with Traefik labels and live volume mounts

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry |
| Time-series DB | InfluxDB 2.x |
| Relational DB | SQLite via SQLAlchemy + Alembic |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| Auth | JWT via `python-jose` + Google OAuth |
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
├── api/              # FastAPI routers (auth, stations, rulesets, stats, foehn, …)
├── collectors/       # MeteoSwiss, SLF, METAR, Wunderground, forecast_meteoswiss
├── database/         # InfluxDB client + SQLAlchemy ORM + Alembic migrations
├── models/           # Pydantic models (weather, auth, rules)
├── rules/            # Live + forecast evaluator
├── services/         # Auth helpers, notifications
├── config.py         # YAML config loader
└── scheduler.py      # APScheduler wiring
static/
├── index.html / map.js     # Map dashboard
├── rulesets.html            # Rule set cards + forecast strip
├── ruleset-editor.html      # Condition builder
├── ruleset-analysis.html    # Per-condition analysis
├── stats.html               # Flyability statistics
├── foehn.html               # Föhn monitor
├── stations.html / station-detail.html
├── i18n.js + i18n/          # Translation engine + locale JSON files
└── auth.js / login.html / register.html
```

## Development Status

| Milestone | Status |
|---|---|
| v0.1 — Station detail page + MeteoSwiss collector | ✅ Shipped |
| v0.2 — Live Leaflet map | ✅ Shipped |
| v0.3 — JWT auth + Google OAuth + admin | ✅ Shipped |
| v0.4 — Launch sites (pilot-owned, on map) | ✅ Shipped |
| v0.5 — SLF + METAR collectors | ✅ Shipped |
| v0.6 — Rule editor (condition builder) | ✅ Shipped |
| v0.7 — Rules evaluator + traffic lights + forecast evaluation | ✅ Shipped |
| v0.8 — ICON forecast pipeline + map time-navigation replay | ✅ Shipped |
| v0.9 — Flyability statistics dashboard | ✅ Shipped |
| v0.10 — Föhn monitor + Wunderground collector + personal-station toggle | ✅ Shipped |
| v1.0 — Multilanguage EN / DE / FR / IT | ✅ Shipped |
| v1.1 — Admin GUI + customer role | Planned |
| v1.2 — Rule types: Risk + Opportunity | Planned |
| v1.3 — Pre-seeded launch site defaults | Planned |
| v1.4 — AI-assisted rule building | Planned |
| v1.5 — OGN live overlay + launch statistics | Planned |

Full backlog in [plan-lenticularisStructure.prompt.md](plan-lenticularisStructure.prompt.md).

## Configuration

See [config.yml.example](config.yml.example) for all options (InfluxDB, collectors, JWT secret, Google OAuth, Wunderground API keys).

## License

MIT License
