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
- **One router per domain** — never put all routes in `main.py`.
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors.
- **Log extensively** — startup sequence, every request, every job run, every config value loaded. See `08-operability.md` for the full doctrine.
- **No print statements** in production code — always use the `logging` module.
