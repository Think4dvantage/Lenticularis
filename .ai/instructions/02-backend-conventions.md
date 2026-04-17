# Backend Conventions

## New API Router

Create `src/lenticularis/api/routers/<domain>.py`, register it in `main.py`.

```python
# src/lenticularis/api/routers/widgets.py
router = APIRouter(prefix="/api/widgets", tags=["widgets"])

@router.get("")
def list_widgets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ...
```

```python
# main.py
from lenticularis.api.routers import widgets as widgets_router
app.include_router(widgets_router.router)
```

Add a page route in the same router file if a new HTML page is needed.

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

Import from `lenticularis.api.dependencies`:

| Dependency | Who passes |
|---|---|
| `get_current_user` | Any logged-in user |
| `require_pilot` | `pilot` or `admin` (blocks `customer` + `org_pilot`) |
| `require_admin` | `admin` only |
| `require_org_member` | `org_pilot` / `org_admin` / `admin` |
| `require_org_admin` | `org_admin` / `admin` |

Enforce `owner_id == current_user.id` for pilots reading/writing their own resources.

---

## Config

Add new keys to `config.py` Pydantic models **and** to `config.yml.example`. Never read `os.environ` directly — always go through `get_config()`.

---

## Scheduler Jobs

Add to `CollectorScheduler` in `scheduler.py`. Use `AsyncIOScheduler` + `IntervalTrigger`. Track health in `_collector_health` dict.

---

## Org Multi-Tenancy

- `Organization` model: `slug` (unique), `name`.
- `org_id` nullable FK on `User` and `RuleSet`.
- Subdomain routing in `main.py` reads `Host` header — any unknown subdomain serves `org-dashboard.html`.
- Pass `org_slug` in `RuleSetCreate` to scope a new ruleset to an org (admin or org_admin only).
- `GET /api/rulesets` filters `org_id IS NULL` — org-scoped rulesets never appear on the personal map.
- Org-scoped pages use `?org={slug}` query param.

---

## Coding Standards

- **Always use type hints** on function signatures and class attributes.
- **Async/await** for all I/O — HTTP calls, DB writes, InfluxDB queries.
- **Pydantic v2** for all data schemas and config validation.
- **SQLAlchemy 2.0 style** — use `select()`, not legacy `query()`.
- **One router per domain** — never put all routes in `main.py`.
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors.
- **Log** all collection events, rule evaluations, and errors with the standard `logging` module.
- **No print statements** in production code.

---

## Collector Reference

Before implementing or debugging any collector, check the winds-mobi providers repo:
**https://github.com/winds-mobi/winds-mobi-providers**

Key lessons already learned:
- **MeteoSwiss `wind_direction`** is embedded as `properties["wind_direction"]` inside the `wind_speed` response — there is no separate windrichtung endpoint.
- **MeteoSwiss pressure** uses two endpoints: `qff` (meteorological sea-level) and `qfe` (station pressure). The `qnh` endpoint is not used.
- **MeteoSwiss timestamps** — use `reference_ts` (ISO 8601); fall back to `date` only if absent.
- **Altitude strings** can arrive as floats (`'1888.00'`) or with unit suffix (`'1538.86 m'`). Always parse via `int(float(str(raw).split()[0]))`.
- **METAR wind direction** can legitimately be missing/variable (VRB/calm). Allow nullable `wind_direction`.
- **Holfuy API** endpoint is `https://api.holfuy.com/live/` (NOT `/measurements/`). Params: `pw=<key>&m=JSON&s=all&su=km/h&tu=C&loc&utc`. Response shape: `{"measurements": [...]}` wrapper (not a flat list). Each entry: `stationId`, `stationName`, `location: {latitude, longitude, altitude}`, `dateTime` (UTC when `utc` param present), `wind: {speed, gust, direction}`, `temperature`, `humidity`. Without `loc` flag, coordinates are absent. Without `utc` flag, timestamps are CE(S)T.
- **Duplicate keyword trap in collector constructors** — some collectors explicitly pass `pressure_qff=None` even when the field is already optional. When renaming a field across constructors (e.g. `pressure_qnh` → `pressure_qff`), grep for *both* the old and new field name inside each constructor before editing to avoid `SyntaxError: keyword argument repeated`. The METAR collector was caught by this: it had both `pressure_qnh=…` and `pressure_qff=None` explicitly in the same call.

---

## AI Rule Suggestions (`routers/ai.py`)

Single Ollama call with three layers of Python pre-processing:

1. **`_normalize_description()`** — regex pipeline that annotates natural-language wind terms with explicit degrees. Add new patterns to `_DIR_PATTERNS` / `_SPEED_PATTERNS`.
2. **`_fuzzy_station_hints()`** — prefix/substring match of description words against station names.
3. **`_geo_station_hints()`** — detects Swiss location names (`_KNOWN_LOCATIONS`), returns nearest stations by haversine distance within `_GEO_RADIUS_KM` (20 km). Extend `_KNOWN_LOCATIONS` to add more sites.

`StationHint` carries `latitude`, `longitude`, `elevation` (sent by the frontend from `allStations`). `_validate_conditions()` is the hard guard — rejects any condition with invalid field/operator/colour. User input is wrapped in `<input>…</input>` delimiters as prompt injection mitigation.
