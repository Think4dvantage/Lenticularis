# Project Overview — Lenticularis

## What This Is

Lenticularis is a paragliding weather decision-support system for Switzerland. It collects data from multiple weather networks, stores it in InfluxDB, and lets each pilot build graphical rule sets that evaluate weather conditions across multiple stations to produce GREEN / ORANGE / RED traffic-light decisions per launch site.

Core differentiator: **rules are fully pilot-owned and self-served** through a graphical condition builder. No admin-imposed logic.

---

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry (`pyproject.toml`) |
| Time-series DB | InfluxDB 2.x (`influxdb-client`) |
| Relational DB | SQLite via SQLAlchemy (no Alembic — see backend conventions) |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| Auth | JWT via `python-jose`, passwords via `passlib` |
| Config | YAML (`config.yml`) validated by Pydantic |
| Frontend | Vanilla JS + Leaflet.js + Chart.js |
| Container | Docker + docker-compose |

---

## Repository Layout

```
src/lenticularis/
├── api/
│   ├── main.py              # FastAPI app factory + lifespan; subdomain-aware root handler
│   ├── dependencies.py      # get_current_user, require_pilot, require_admin,
│   │                        #   require_org_admin, require_org_member
│   └── routers/             # One file per domain (auth, stations, rulesets, org, ai, …)
│       └── org.py           # /api/org/{slug}/status|dashboard|rulesets
├── collectors/              # One file per data network (meteoswiss, slf, metar, wunderground, …)
├── database/
│   ├── models.py            # SQLAlchemy ORM (source of truth for SQLite schema)
│   ├── db.py                # init_db(), get_db() dependency, column migrations
│   └── influx.py            # InfluxDB 2.x client (write + all query methods)
├── models/                  # Pydantic request/response schemas
├── rules/evaluator.py       # Live + forecast rule evaluation engine
├── services/                # Auth helpers, stats, AI analysis, FCM push
├── config.py                # Pydantic-validated YAML config loader (singleton)
├── scheduler.py             # APScheduler: observation + forecast + derived jobs
└── foehn_detection.py       # Föhn region definitions + pressure gradient logic
static/
├── i18n/{en,de,fr,it}.json  # Translation files — add a key to ALL 4 when needed
├── i18n.js                  # initI18n(), t(), applyDataI18n(), renderLangPicker()
├── auth.js                  # JWT storage, fetchAuth(), renderNavAuth()
├── shared.css               # Mobile-responsive overrides (linked on every page)
├── org-dashboard.html       # Public traffic-light + authenticated detail for org subdomains
└── *.html + *.js            # One HTML + inline <script type="module"> per page
```

---

## Data Flow

```
Collectors (every 5–30 min)
  → write_measurements() → InfluxDB weather_data / weather_forecast
Scheduler also runs föhn virtual-station collector (10 min)

API routes
  → query InfluxDB for live / history / forecast data
  → evaluate rulesets via rules/evaluator.py → write rule_decisions to InfluxDB
  → CRUD on SQLite via SQLAlchemy sessions (get_db() dependency)

Frontend
  → authenticated REST calls via fetchAuth()
  → Leaflet.js map, Chart.js charts, vanilla JS rendering
```

---

## Data Sources

| Source | Auth | Key measurements | Interval |
|---|---|---|---|
| MeteoSwiss | None (open data) | wind speed/gust, temp, humidity, pressure | 10 min |
| METAR (AviationWeather) | None (open data) | wind speed/direction/gust, temperature, pressure | 15 min |
| SLF | None (open data) | wind speed/direction/gust, snow depth, temp | 30 min |
| Wunderground/Ecowitt | API key | all personal weather station sensors | 5–15 min |
| Holfuy | API key (`pw=` param) | wind speed/gust/direction, temp, humidity | 5 min |
| Open-Meteo | None (or commercial API key) | 5-day forecast (short + extended); grid forecast for wind-forecast map | layered schedule |
| SwissMeteo (`lsmfapi-dev.lg4.ch`) | None (internal API) | ICON-CH ensemble forecast: probable + min/max for all fields; altitude-wind profiles per station | 60 min |

---

## User Roles

| Role | Description |
|---|---|
| `pilot` | Manage own launch sites, build own rule sets, view stats |
| `customer` | Read-only; sees only rulesets assigned by admin |
| `admin` | Manage user accounts, organisations, enable/disable collectors |
| `org_admin` | Manage rules for their organisation (`org_id` required) |
| `org_pilot` | Read-only member of an organisation |

System `admin` bypasses all org guards.
