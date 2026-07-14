# Testing Conventions

## Philosophy

Backend logic must be test-gated. Tests give AI-assisted development a safety net — they catch regressions
that static analysis misses and make refactors safe.

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
httpx = "^0.27"
```

### File layout

```
tests/
  __init__.py
  backend/
    __init__.py
    conftest.py               # shared fixtures
    test_auth.py
    test_rules_evaluator.py
    test_stations_security.py
    test_dedup.py
```

---

## conftest.py — core test harness

Three concerns: config isolation, DB isolation, app wiring.

### 1. Config isolation (`autouse=True`)

```python
import lenticularis.config as _lenti_config
from lenticularis.config import MainConfig, InfluxDBConfig, DatabaseConfig, AuthConfig, ...

_JWT_SECRET = "test-secret-that-is-at-least-32-chars!!"
_TEST_CONFIG = MainConfig(
    influxdb=InfluxDBConfig(enabled=False),
    collectors=[],
    database=DatabaseConfig(path=":memory:"),
    auth=AuthConfig(jwt_secret=_JWT_SECRET),
    logging=LoggingConfig(level="warning", file=""),
    api=APIConfig(),
    ollama=OllamaConfig(enabled=False),
)

@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    monkeypatch.setattr(_lenti_config, "_config", _TEST_CONFIG)
```

`get_config()` checks `_config` first, so setting it directly bypasses all file I/O at every call site.

### 2. InfluxDB stub

```python
class FakeInflux:
    """Safe no-op stand-in for InfluxClient. All methods return empty / None."""
    def query_latest(self, station_id): return None
    def query_latest_for_stations(self, station_ids): return {}
    def query_history(self, *a, **kw): return []
    def query_replay(self, *a, **kw): return []
    def query_forecast_replay(self, *a, **kw): return {}
    def query_forecast_for_stations(self, *a, **kw): return {}
    def write_measurement(self, *a, **kw): pass
    def write_forecast_grid(self, *a, **kw): pass
    # add stubs for any new InfluxClient methods as needed
```

### 3. In-memory SQLite engine

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from lenticularis.database.models import Base

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

**`poolclass=StaticPool` is mandatory, not cosmetic.** An in-memory SQLite engine defaults
to `SingletonThreadPool`, which opens one connection *per thread* — and every in-memory
SQLite connection is its own separate, empty database. FastAPI runs **sync** dependencies
(`get_current_user`, `require_pilot`, `require_admin` — all `def`, not `async def`) in a
worker threadpool, so they land on a different thread and see none of the tables
`create_all()` built on the main thread, failing with `no such table: users`. Confusingly,
`async def` handlers work fine, because their body runs on the event loop thread — so the
bug only surfaces on endpoints that authenticate. `StaticPool` shares the one connection
across all threads.

### 4. FastAPI test app (async fixture)

```python
from contextlib import asynccontextmanager
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from lenticularis.api.main import create_app
from lenticularis.api.dependencies import get_db

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
    app.state.station_registry = {}
    app.state.display_registry = {}
    app.state.virtual_members = {}
    app.state.dedup_distance_m = 50.0

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
`app.state.influx` stays unset, and every route that calls `_get_influx()` returns
`503 InfluxDB not available`. (Routes that 404 earlier on a registry lookup still pass,
which makes the failure look arbitrary.)

Replacing `app.router.lifespan_context` with a no-op is still worth doing: it guarantees
the real lifespan (scheduler, InfluxDB connections, collector startup) cannot fire if a
test ever *does* drive the lifespan, e.g. via `asgi-lifespan`'s `LifespanManager`.

**Stub every `InfluxClient` method a route under test calls.** A missing stub surfaces as
`AttributeError: 'FakeInflux' object has no attribute '…'` — `query_latest_all_stations`
was missed exactly this way, hidden behind the 503 above until the lifespan bug was fixed.

---

## Writing tests

### API tests — use the `client` fixture

```python
async def test_login_returns_token(client):
    await client.post("/api/auth/register", json={"username": "u", "email": "u@x.com", "password": "pw"})
    r = await client.post("/api/auth/login", json={"username": "u", "password": "pw"})
    assert r.status_code == 200
    assert "access_token" in r.json()
```

### Pure-logic tests — use `SimpleNamespace` duck-typing

For rules evaluator and dedup logic, avoid touching DB/Influx at all:

```python
from types import SimpleNamespace

def _cond(station_id, field, operator, value_a, result_colour="red", **kw):
    return SimpleNamespace(
        station_id=station_id, field=field, operator=operator,
        value_a=value_a, value_b=kw.get("value_b"),
        result_colour=result_colour, group_id=None,
    )

def test_no_conditions_returns_green():
    rs = SimpleNamespace(conditions=[], condition_groups=[], combination_logic="worst_wins")
    result = evaluate_ruleset(rs, measurements={})
    assert result.colour == "green"
```

### Auth helpers for protected endpoints

```python
_JWT_SECRET = "test-secret-that-is-at-least-32-chars!!"

def _make_token(role="user"):
    import jwt, time
    payload = {"sub": "u1", "role": role, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")

async def test_admin_only_endpoint(client):
    r = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {_make_token('admin')}"})
    assert r.status_code == 200
```

---

## CI

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
        continue-on-error: true
```

---

## Coverage expectations

| Area | What's tested |
|---|---|
| Auth | register, login, refresh, `/me`; duplicate user 409; wrong password 401 |
| Rules evaluator | no conditions → green; worst_wins; majority_vote; AND groups; direction wrapping; pressure field mapping |
| Station security | unknown ID → 404; special chars → 422/404; empty registry → `[]` |
| Dedup | haversine sanity; proximity merge; priority (meteoswiss > holfuy); manual pairs; foehn exclusion; transitive cluster |

No frontend tests (Playwright) are set up yet — the no-build frontend makes this straightforward to add later via `tests/frontend/`.
