# Backend Conventions

## New API Router

Create `src/[package]/api/routers/<domain>.py`, register it in `main.py`.

```python
# src/[package]/api/routers/widgets.py
router = APIRouter(prefix="/api/widgets", tags=["widgets"])

@router.get("")
def list_widgets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ...
```

```python
# main.py
from [package].api.routers import widgets as widgets_router
app.include_router(widgets_router.router)
```

Add a page route in the same router file if a new HTML page is needed:

```python
@router.get("/widgets-page", include_in_schema=False)
async def widgets_page():
    return FileResponse("static/widgets.html")
```

---

## New SQLite Table & Migrations

> **No Alembic.** Migrations are raw `ALTER TABLE` statements guarded by `PRAGMA table_info()`
> checks in `_run_column_migrations()` in `src/lenticularis/database/db.py`.
> There is no `_migrations` table and no `.sql` migration files.

### 1. Define ORM Model
Add the ORM model in `models.py`:

```python
class Widget(Base):
    __tablename__ = "widgets"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ...
```

### 2. Schema creation
`Base.metadata.create_all()` in `init_db()` handles the initial schema on first boot. It is
idempotent — run it every startup; it only creates tables that are missing.

### 3. Adding new columns (`_run_column_migrations`)
For columns added after the initial schema, add an idempotent guard in `_run_column_migrations()`:

```python
cols = {row[1] for row in conn.execute(text("PRAGMA table_info(widgets)")).fetchall()}
if "new_col" not in cols:
    conn.execute(text("ALTER TABLE widgets ADD COLUMN new_col TEXT"))
    conn.commit()
    logger.info("Migration: added widgets.new_col column")
```

Never use Alembic. Never create `.sql` migration files. Never maintain a `_migrations` table.

#### SQLite WAL Mode
WAL mode is enabled automatically via a SQLAlchemy `connect` event listener in `init_db()`:

```python
@event.listens_for(_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()
```

---

## Testing Conventions

See `06-testing-conventions.md` for the full strategy.
- **Backend**: Pytest in `tests/backend/`. Use `httpx.AsyncClient`.
- **Frontend**: Playwright in `tests/frontend/`.

---

## New InfluxDB Query

Add a method to `InfluxClient` in `influx.py`. Keep Flux query strings inside the method. Return plain Python dicts/lists (no ORM objects).

---

## Auth Dependencies

Import from `[package].api.dependencies`:

| Dependency | Who passes |
|---|---|
| `get_current_user` | Any logged-in user |
| `require_admin` | `admin` only |

[Add additional role-based dependencies here as they are introduced.]

---

## Config

Add new keys to `config.py` Pydantic models **and** to `config.yml.example`. Never read `os.environ` directly — always go through `get_config()`.

---

## Scheduler Jobs

Add to `CollectorScheduler` in `scheduler.py`. Use `AsyncIOScheduler` + `IntervalTrigger`. Track health in `_collector_health` dict.

---

## Coding Standards

- **Always use type hints** on function signatures and class attributes.
- **Async/await** for all I/O — HTTP calls, DB writes, InfluxDB queries.
- **Pydantic v2** for all data schemas and config validation.
- **SQLAlchemy 2.0 style** — use `select()`, not legacy `query()`.
- **One router per domain** — never put all routes in `main.py`; page routes go in `api/routers/pages.py`.
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors.
- **Log extensively** — startup sequence, every request, every job run, every config value loaded. See `08-operability.md` for the full doctrine.
- **No print statements** in production code — always use the `logging` module.

---

## Async Safety — Rules That Must Not Be Broken

### Influx calls in async handlers

InfluxDB client methods are **synchronous**. Calling them directly in an `async def` handler blocks the event loop and stalls all concurrent requests.

```python
# WRONG
async def my_handler(..., request: Request):
    data = request.app.state.influx.query_latest(station_id)

# RIGHT
data = await asyncio.to_thread(request.app.state.influx.query_latest, station_id)
```

The same rule applies to scheduler job methods that call influx `write_*`.

### Batch before looping

Never call `influx.query_latest(station_id)` in a per-station loop. Use `query_latest_for_stations(list[str])` once. See `rules/evaluator.py` for the canonical pattern.

---

## Error Responses

Use `api_error()` from `api/errors.py` instead of `HTTPException(detail="string")`:

```python
from lenticularis.api.errors import api_error

raise api_error(404, "not_found", "Station not found", f"No station '{station_id}'")
```

The global exception handler in `main.py` also wraps unhandled exceptions into the same RFC 7807 envelope.

---

## Collector Conventions

New collector checklist:
- Subclass `BaseCollector` from `collectors/base.py`.
- Import `to_float` and `normalize_wind_dir` from `collectors/utils.py` — never redefine local copies.
- Use `self._collect_concurrent(items, fn, limit=8)` for bounded parallel fetches (wraps asyncio gather with a semaphore).
- Log every fetch with elapsed time and result count.
- Use `asyncio.to_thread()` for any synchronous write to InfluxDB.
- Register the class in `_COLLECTOR_REGISTRY` in `scheduler.py`, add a config block to
  `config.yml.example`, and add the network to `NETWORK_PRIORITY` in `services/dedup.py`.
- **Normalise units to the unified schema.** `WeatherMeasurement` is km/h, °C, %, hPa. Convert at
  the collector boundary (e.g. `jfb.py` multiplies knots by 1.852) — never store a foreign unit.
- **Map only what the schema already holds.** If a source field has no `WeatherMeasurement` field,
  drop it — do not widen the model to fit one source. `jfb.py` drops `TD`/`DIFFTD` (derivable from
  temperature + humidity) and `G1h` (1-hour gust ≠ `wind_gust`, which is the 10-min peak everywhere
  else — storing it there would silently break cross-network comparability).
- **Never synthesise a field the source does not measure.** JFB reports QFE only; `pressure_qff` is
  left `None`, because QFF is not derivable from QFE + elevation (that is QNH) and a fake value
  would corrupt the föhn pressure-gradient comparison. `fga.py` does the same.
- **Guard against stale data.** Skip and `WARNING` any reading older than ~2 h. Some APIs return
  hours-old data with a `200 OK` and no error (see the `currentDateTime` note in `jfb.py`).
