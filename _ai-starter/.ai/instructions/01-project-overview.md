# Project Overview — [PROJECT NAME]

## What This Is

[One paragraph describing what the project does and its core differentiator.]

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
| Frontend | Vanilla JS + [Mapping lib] + [Chart lib] |
| Container | Docker + docker-compose |

---

## Repository Layout

```
src/[package]/
├── api/
│   ├── main.py              # FastAPI app factory + lifespan
│   ├── dependencies.py      # Auth dependency functions
│   └── routers/             # One file per domain
├── collectors/              # One file per data source
├── database/
│   ├── models.py            # SQLAlchemy ORM (source of truth for SQLite schema)
│   ├── db.py                # init_db(), get_db() dependency, column migrations
│   └── influx.py            # InfluxDB 2.x client (write + all query methods)
├── models/                  # Pydantic request/response schemas
├── services/                # Domain logic helpers
├── config.py                # Pydantic-validated YAML config loader (singleton)
└── scheduler.py             # APScheduler jobs
static/
├── i18n/{en,de,fr,it}.json  # Translation files — add a key to ALL when needed
├── i18n.js                  # initI18n(), t(), applyDataI18n(), renderLangPicker()
├── auth.js                  # JWT storage, fetchAuth(), renderNavAuth()
├── shared.css               # Mobile-responsive overrides (linked on every page)
└── *.html + *.js            # One HTML + inline <script type="module"> per page
```

---

## Data Flow

```
Collectors (scheduled intervals)
  → write to InfluxDB / SQLite

API routes
  → query InfluxDB / SQLite
  → apply business logic
  → return JSON

Frontend
  → authenticated REST calls via fetchAuth()
  → map / chart rendering, vanilla JS
```

---

## Data Sources

| Source | Auth | Key measurements | Interval |
|---|---|---|---|
| [Source 1] | [None / API key] | [fields] | [interval] |

---

## User Roles

| Role | Description |
|---|---|
| `admin` | Manage users, system config |
| `user` | Standard authenticated access |

[Expand as needed. Document which FastAPI dependency enforces each role.]
