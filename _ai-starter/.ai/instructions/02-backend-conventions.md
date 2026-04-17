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

## New SQLite Table

Add ORM model in `models.py`:

```python
class Widget(Base):
    __tablename__ = "widgets"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ...
```

New tables are created automatically by `Base.metadata.create_all()` in `db.py`. No migration needed.

For **new columns on existing tables**, add to `_run_column_migrations()` in `db.py`:

```python
if "new_col" not in cols:
    conn.execute(text("ALTER TABLE existing_table ADD COLUMN new_col TEXT"))
    conn.commit()
```

**Always make migrations idempotent** — check `PRAGMA table_info` first. Never skip `_run_column_migrations` when adding columns; SQLAlchemy's `create_all` does not alter existing tables.

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
- **Log** all collection events and errors with the standard `logging` module.
- **No print statements** in production code.
