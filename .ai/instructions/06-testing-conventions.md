# Testing Conventions

> Generic, blueprint-owned testing patterns. Lenticularis's actual `conftest.py`, fixtures,
> gotchas, and current coverage live in `context/testing-notes.md` — read both.

## Philosophy

Backend logic must be test-gated. Tests give AI-assisted development a safety net — they catch
regressions that static analysis misses and make refactors safe.

---

## Backend: Pytest

Framework: `pytest` + `pytest-asyncio`. Dev dependency group in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"       # all async tests run automatically — no @pytest.mark.asyncio needed
testpaths = ["tests"]

[tool.poetry.group.dev.dependencies]
pytest = "^8"
pytest-asyncio = "^0.24"
httpx = "^0.28"
```

**Verify these against the actual latest stable releases before pinning** — see
`05-user-profile.md`. `httpx` 0.28 removed the `app=` shortcut on `AsyncClient`; if the pin here
predates a future httpx major, re-check the ASGI transport API still matches the example below.

### File layout

```
tests/
  __init__.py
  backend/
    __init__.py
    conftest.py               # shared fixtures
    test_widgets.py
```

---

## conftest.py — core test harness

Three concerns: config isolation, DB isolation, app wiring.

### 1. Config isolation (`autouse=True`)

```python
import [package].config as _app_config
from [package].config import MainConfig, InfluxDBConfig, DatabaseConfig, AuthConfig, LoggingConfig, APIConfig

_JWT_SECRET = "test-secret-that-is-at-least-32-chars!!"
_TEST_CONFIG = MainConfig(
    influxdb=InfluxDBConfig(enabled=False),
    collectors=[],
    database=DatabaseConfig(path=":memory:"),
    auth=AuthConfig(jwt_secret=_JWT_SECRET),
    logging=LoggingConfig(level="warning", file=""),
    api=APIConfig(),
)

@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(_app_config, "_config", _TEST_CONFIG)
```

`get_config()` checks `_config` first, so setting it directly bypasses all file I/O at every call
site. **This only works if every module resolves config through `get_config()` at call time** —
see the "resolve config at the call site" rule in `02-backend-conventions.md`. A module that
reads a config value once at import time, or caches it on its own, is invisible to this patch.

### 2. InfluxDB stub

```python
class FakeInflux:
    """Safe no-op stand-in for InfluxClient. All methods return empty / None."""
    def query_latest(self, station_id): return None
    def query_latest_for_stations(self, station_ids): return {}
    def query_history(self, *a, **kw): return []
    def write_measurement(self, *a, **kw): pass
    # add stubs for every InfluxClient method a route under test calls
```

**Stub every `InfluxClient` method a route under test calls.** A missing stub surfaces as
`AttributeError: 'FakeInflux' object has no attribute '…'`, often hidden behind an unrelated
error (see the lifespan warning below) until something forces it to the surface.

### 3. In-memory SQLite engine

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from [package].database.models import Base

@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()
```

**`poolclass=StaticPool` is mandatory, not cosmetic.** An in-memory SQLite engine defaults to
`SingletonThreadPool`, which opens one connection *per thread* — and every in-memory SQLite
connection is its own separate, empty database. FastAPI runs **sync** dependencies (`def`, not
`async def` — a common shape for `get_current_user`-style auth dependencies) in a worker
threadpool, so they land on a different thread and see none of the tables `create_all()` built on
the main thread, failing with `no such table: users`. Confusingly, `async def` handlers work
fine, because their body runs on the event loop thread — so the bug only surfaces on endpoints
that authenticate. `StaticPool` shares the one connection across all threads.

### 4. FastAPI test app (async fixture)

```python
from contextlib import asynccontextmanager
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from [package].api.main import create_app
from [package].api.dependencies import get_db

@pytest_asyncio.fixture
async def test_app(db_engine):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    fake_influx = FakeInflux()

    @asynccontextmanager
    async def _test_lifespan(app):
        """No-op stand-in for the real lifespan (scheduler, InfluxDB, collectors)."""
        yield

    app = create_app()
    app.router.lifespan_context = _test_lifespan

    # Set app.state DIRECTLY — see the warning below.
    app.state.influx = fake_influx

    def _get_test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_test_db
    yield app

@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac
```

**Key**: set `app.state` **directly** in the fixture. Do **not** set it from inside
`_test_lifespan` — httpx's `ASGITransport` never emits ASGI lifespan events, so **no
`lifespan_context` ever runs under it**. State assigned there is silently never applied,
`app.state.influx` stays unset, and every route that depends on it returns a 503. (Routes that
404 earlier on an unrelated lookup still pass, which makes the failure look arbitrary.)

Replacing `app.router.lifespan_context` with a no-op is still worth doing: it guarantees the real
lifespan (scheduler, InfluxDB connections, collector startup) cannot fire if a test ever *does*
drive the lifespan, e.g. via `asgi-lifespan`'s `LifespanManager`.

`AsyncClient(app=app, ...)` is a **removed API** as of httpx 0.28 — always construct the client
with `transport=ASGITransport(app=...)` as shown above, never the bare `app=` kwarg.

---

## Writing tests

### API tests — use the `client` fixture

```python
async def test_login_returns_token(client):
    await client.post("/api/auth/register",
                      json={"email": "u@x.com", "password": "pw"})
    r = await client.post("/api/auth/login", json={"email": "u@x.com", "password": "pw"})
    assert r.status_code == 200
    assert "access_token" in r.json()
```

### Pure-logic tests — use `SimpleNamespace` duck-typing

For business logic that reads ORM-shaped data (evaluators, aggregators, scoring), avoid touching
the DB at all. Duck-type the ORM rows with `SimpleNamespace` and call the function directly:

```python
from types import SimpleNamespace

def _row(**overrides):
    defaults = dict(id="r-test", value=1, category="a")
    return SimpleNamespace(**{**defaults, **overrides})

def test_no_rows_returns_default():
    result = _evaluate([])
    assert result == "default"
```

This is faster than going through the DB and API layers, and isolates the test from schema
changes that don't affect the fields the logic actually reads.

### Auth helpers for protected endpoints

Use a **`make_token` fixture** in `conftest.py` that mints a real token through the app's own
`create_access_token(user_id, role)` and returns a ready `Authorization` header dict:

```python
async def test_admin_only_endpoint(client, make_token):
    r = await client.get("/api/admin/users", headers=make_token("u1", "admin"))
    assert r.status_code == 200
```

**Never hand-roll a JWT in a test.** Three ways it silently fails:

1. Using the wrong JWT library for signing/decoding (e.g. `import jwt` when the app signs with
   `python-jose`'s `from jose import jwt`) produces a token the app's own decoder rejects.
2. The decoder may reject any payload missing a claim the app always sets (e.g. `type: "access"`)
   — a hand-built `{"sub", "role", "exp"}` payload can be refused even with a valid signature.
3. `get_current_user`-style dependencies resolve the user row from the DB and often require
   `is_active` — a token for a fabricated user ID yields 401, not 200, because the row doesn't
   exist in the test DB.

---

## What Not to Test

- Framework internals (FastAPI routing, SQLAlchemy's own query building).
- Third-party infrastructure behind a stub (don't assert on what `FakeInflux` returns as if it
  were real InfluxDB behaviour — the stub's job is to be inert, not correct).
- Scheduler timing (cron/interval schedules) — test the job function directly, not the trigger.

---

## Frontend: Playwright

Because this project uses a "no-build" frontend (Vanilla JS), we use `Playwright` for End-to-End
(E2E) testing. This is the most reliable way to test that the UI behaves correctly in real
browsers.

### Location
All frontend tests live in `tests/frontend/`.

### Standards
- **Naming**: `test_*.py` (using Playwright's Python library).
- **Setup**: Playwright should point to the dev instance or a local test server.
- **Interactions**: Use standard Playwright selectors (e.g., `page.get_by_text()`, `page.get_by_role()`).
- **i18n**: Test with different locale settings to ensure translations load correctly.

### Example E2E Test

```python
import pytest
from playwright.sync_api import Page, expect

def test_login_page_renders(page: Page):
    page.goto("http://localhost:8000/login")
    expect(page.get_by_text("Email Address")).to_be_visible()
    expect(page.locator("button[type='submit']")).to_be_visible()
```

---

## Time-dependent fixtures — do not hardcode a clock time

A fixture that hardcodes an observation time (`"timeUTC": "11:30"`) will pass or fail depending
on the hour the suite runs at, whenever the code under test compares against "now" (staleness
checks, expiry windows, freshness thresholds). Build fixture timestamps **relative to
`datetime.now(timezone.utc)`** instead of a fixed string.

---

## CI / Automation

GitHub Actions at `.github/workflows/test.yml`:

```yaml
name: Backend tests
on:
  push:
    branches: ["**"]
  pull_request:
    branches: ["**"]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install poetry
      - run: poetry install --with dev
      - run: poetry run pytest --tb=short -q
      - run: poetry run ruff check src/ tests/
```

- Ensure the test suite is green before merging any new feature or fix.
- **A green run is load-bearing** — never merge or tag a release while the required test job is
  red. A red integration/E2E suite that's "probably fine" is exactly how regressions reach prod.
- **Coverage**: Aim for 80%+, but prioritize testing critical paths (Auth, data collection, API
  contracts) over chasing the number on trivial code. See `context/testing-notes.md` for this
  project's current coverage table and test count.
