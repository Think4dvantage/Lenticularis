# Claude Code Instructions — Lenticularis

## Project Overview

Lenticularis is a Python/FastAPI + Vanilla JS paragliding weather decision-support system.
Backend: FastAPI + SQLAlchemy (SQLite) + InfluxDB + APScheduler.
Frontend: plain HTML/CSS/JS — no build step, no framework, no bundler.
All static files are served directly from `static/` by FastAPI's `StaticFiles` mount.

---

## Repository Layout

```
src/lenticularis/
├── api/
│   ├── main.py              # FastAPI app factory + lifespan (startup/shutdown)
│   ├── dependencies.py      # get_current_user, require_pilot, require_admin
│   └── routers/             # One file per domain (auth, stations, rulesets, …)
├── collectors/              # One file per data network (meteoswiss, slf, metar, …)
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
└── *.html + *.js            # One HTML + inline <script type="module"> per page
```

---

## Key Conventions

### Backend

- **New router**: create `src/lenticularis/api/routers/<domain>.py`, register it in `main.py` with `app.include_router(…)`. Add a page route there too if a new HTML page is needed.
- **New SQLite table**: add ORM model in `models.py`, then add a migration block in `db.py → _run_column_migrations()` for any new columns on existing tables. New tables are created automatically by `Base.metadata.create_all()`.
- **New InfluxDB query**: add a method to `InfluxClient` in `influx.py`. Keep Flux query strings inside the method. Return plain Python dicts/lists (no ORM objects).
- **Auth dependencies**: `get_current_user` (any logged-in user), `require_pilot` (pilot or admin), `require_admin` (admin only). Import from `lenticularis.api.dependencies`.
- **Config**: add new keys to `config.py` Pydantic models and to `config.yml.example`. Never read `os.environ` directly — always go through `get_config()`.
- **Scheduler jobs**: add to `CollectorScheduler` in `scheduler.py`. Use `AsyncIOScheduler` + `IntervalTrigger`. Track health in `_collector_health` dict.
- **No Alembic**: schema migrations are done with raw `ALTER TABLE` in `_run_column_migrations()`. Always make them idempotent (check `PRAGMA table_info` first).

### Frontend

- **No build step**: changes to `static/` are live immediately in dev (volume-mounted). Never introduce npm, webpack, vite, or any bundler.
- **i18n**: every user-visible string must have a key in all 4 locale files (`en.json`, `de.json`, `fr.json`, `it.json`). Static HTML nodes use `data-i18n="key"`. Dynamic JS strings use `window.t('key')` or `window.t('key', { var: val })`.
- **Module scripts**: each page has exactly one `<script type="module">` block that imports from `i18n.js` and `auth.js`. Non-module scripts (e.g. `map.js`) run before `initI18n()` resolves — guard with `typeof window.t === 'function' ? window.t : k => k`.
- **Dark theme**: all pages share the same design system — `#0f1117` body, `#1a1f2e` cards/nav, `#2d3748` borders, `#e2e8f0` text, `#90cdf4` accent. Match existing pages exactly.
- **fetchAuth()**: use `fetchAuth()` from `auth.js` for all authenticated API calls. It auto-refreshes the JWT and redirects to `/login` on session expiry.

### Data Flow

```
Collectors (every 5–30 min)
  → write_measurements() → InfluxDB weather_data / weather_forecast
Scheduler also runs föhn virtual-station collector (10 min)
API routes
  → query InfluxDB for live/history/forecast data
  → evaluate rulesets via rules/evaluator.py → write rule_decisions to InfluxDB
  → CRUD on SQLite via SQLAlchemy sessions (get_db() dependency)
Frontend
  → authenticated REST calls via fetchAuth()
  → Leaflet.js map, Chart.js charts, vanilla JS rendering
```

---

## Patterns to Follow

### Adding a new API router

```python
# src/lenticularis/api/routers/widgets.py
router = APIRouter(prefix="/api/widgets", tags=["widgets"])

@router.get("")
def list_widgets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ...
```

Then in `main.py`:
```python
from lenticularis.api.routers import widgets as widgets_router
app.include_router(widgets_router.router)
```

### Adding a new SQLite table

```python
# models.py — add ORM class
class Widget(Base):
    __tablename__ = "widgets"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ...
```

No extra migration needed for new tables — `Base.metadata.create_all()` handles them.
For new columns on existing tables, add to `_run_column_migrations()` in `db.py`:

```python
if "new_col" not in cols:
    conn.execute(text("ALTER TABLE existing_table ADD COLUMN new_col TEXT"))
    conn.commit()
```

### Adding i18n keys

Add to **all four** files simultaneously: `static/i18n/en.json`, `de.json`, `fr.json`, `it.json`.
Use the same nested key structure as existing keys (e.g. `"admin.users.col_trusted"`).

---

## What NOT to Do

- **Never touch prod directly.** All production changes go through the `lg4` IaC repo.
- **Never add npm / a build step.** The frontend is intentionally dependency-free.
- **Never use `rg` / `grep` / `cat` as bash commands** when Grep/Read tools are available.
- **Never commit secrets** (`config.yml`, `.env`). Only `config.yml.example` is committed.
- **Never skip `_run_column_migrations`** when adding columns to existing tables — SQLAlchemy's `create_all` does not alter existing tables.
- **Never hardcode strings in JS** without a corresponding i18n key in all 4 locale files.

---

## Current Version: v1.1 (shipped)

Next planned: **v1.2 — Webcam Integration**. See `plan-lenticularisStructure.prompt.md` for full roadmap.
