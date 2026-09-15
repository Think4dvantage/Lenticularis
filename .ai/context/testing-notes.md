# Testing Notes — Project-Specific

> Companion to `instructions/06-testing-conventions.md`. That file holds the generic,
> blueprint-owned testing patterns; this file holds Lenticularis's actual `conftest.py`,
> fixtures, gotchas, and current coverage. Update this file freely; it is never touched by
> `update-blueprint.md`.

---

## File layout

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

No frontend tests (Playwright) are set up yet.

---

## conftest.py — the actual harness

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

`get_config()` checks `_config` first, so setting it directly bypasses all file I/O at every call
site. This only works because every module resolves config through `get_config()` at call time —
see the "resolve config at the call site" rule in `context/backend-notes.md`.

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

There is **no `username` field anywhere** — accounts are keyed on `email`, with a separate
`display_name`. Register takes `email` + `display_name` + `password`; login takes `email` + `password`.

```python
async def test_login_returns_token(client):
    await client.post("/api/auth/register",
                      json={"email": "u@x.com", "display_name": "U", "password": "pw"})
    r = await client.post("/api/auth/login", json={"email": "u@x.com", "password": "pw"})
    assert r.status_code == 200
    assert "access_token" in r.json()
```

### Pure-logic tests — use `SimpleNamespace` duck-typing

For rules evaluator and dedup logic, avoid touching DB/Influx at all. Duck-type the ORM rows with
`SimpleNamespace` and call `_evaluate_from_station_data` directly — it returns a
`(decision, results)` tuple. See `tests/backend/test_rules_evaluator.py` for the canonical helpers.

```python
from types import SimpleNamespace
from lenticularis.rules.evaluator import _evaluate_from_station_data

def _rs(conditions, site_type="launch", combination_logic="worst_wins"):
    return SimpleNamespace(
        id="rs-test", owner_id="owner", site_type=site_type,
        combination_logic=combination_logic, conditions=conditions,
    )

def _cond(station_id, field, operator, value_a, result_colour, *,
          value_b=None, group_id=None, station_b_id=None):
    return SimpleNamespace(
        id=f"{station_id}-{field}", station_id=station_id, station_b_id=station_b_id,
        field=field, operator=operator, value_a=value_a, value_b=value_b,
        result_colour=result_colour, group_id=group_id, sort_order=0,
    )

def test_no_conditions_returns_green():
    decision, _ = _evaluate_from_station_data(_rs([]), station_data={})
    assert decision == "green"
```

The ruleset namespace needs **no** `condition_groups` attribute — the evaluator only reads
`group_id` off each condition. There is no `evaluate_ruleset()` function.

### Auth helpers for protected endpoints

Use the **`make_token` fixture** from `conftest.py`. It returns a ready `Authorization` header dict
and mints the token through the real `create_access_token(user_id, role)`:

```python
async def test_admin_only_endpoint(client, make_token):
    r = await client.get("/api/admin/users", headers=make_token("u1", "admin"))
    assert r.status_code == 200
```

**Never hand-roll a JWT in a test.** Three reasons it will not work:

1. The stack signs with **python-jose** (`from jose import jwt`), not PyJWT — `import jwt` is the
   wrong library entirely.
2. `decode_access_token()` rejects any token whose payload lacks `type: "access"`. A hand-built
   `{"sub", "role", "exp"}` payload is refused even when the signature is valid.
3. `get_current_user` resolves `db.get(User, payload["sub"])` and requires `is_active` — the user
   row must **exist in the test DB**. A token for a fabricated `"u1"` yields 401, not 200.

Passwords are hashed with **`bcrypt` directly** — `passlib` is not a dependency of this project.

---

## `FakeInflux` can observe writes

`write_decision` / `write_decisions_batch` reach into `influx._write_api` and `influx._cfg`
directly, inside a `try/except` that only logs. `FakeInflux` therefore provides a
`RecordingWriteApi` and a stub `_cfg` — **without them a write raises `AttributeError`
internally and is silently discarded**, so a test asserting "the decision was written" would
pass having written nothing. Assert against `fake_influx.written_points`.

---

## Time-dependent fixtures — do not hardcode a clock time

A collector fixture that hardcodes an observation time (`"timeUTC": "11:30"`) will pass or fail
depending on the hour the suite runs at, because collectors reject readings older than their
staleness threshold. Build fixture timestamps **relative to `datetime.now(timezone.utc)`**.
See `_fresh_time()` in `tests/backend/test_jfb_collector.py`.

---

## Coverage expectations

| Area | What's tested |
|---|---|
| Auth | register, login, refresh, `/me`; duplicate user 409; wrong password 401 |
| Rules evaluator | no conditions → green; worst_wins; majority_vote; AND groups; direction wrapping; pressure field mapping |
| Station security | unknown ID → 404; special chars → 422/404; empty registry → `[]` |
| Dedup | haversine sanity; proximity merge; priority (meteoswiss > holfuy); manual pairs; foehn exclusion; transitive cluster |
| Static caching | HTML `no-cache` + ETag → 304; assets rewritten with `?v=`; no CDN refs remain; versioned assets immutable; vendored libs served |
| JFB collector | knots → km/h; direction normalisation; unmappable params dropped; MeteoSwiss duplicates excluded; timestamp reconstruction + midnight rollover; staleness skip; `currentDateTime` always sent |
| Public rule sets | `is_showcase AND is_public` gate; curated-but-unpublished never appears; **no-data omitted, not green**; payload carries no owner fields; **one Influx call regardless of rule set count**; cache isolation between viewers; 500 m proximity boundary; 409 on curating an unpublished rule set |
| Condition groups | backfill idempotency; **decisions identical across the migration**; **empty group is inert**; one-condition group ≡ standalone; fail-closed 422 on a dangling `group_id`; re-saving the same group id; clone gets independent groups |
| Thermal derived metrics (`test_thermal_derived.py`) | every §6 band boundary on-threshold and either side; null paths incl. `cin=None` not a cap; `sunshine`/`cloud_mid` guard terms; `ceiling_spread_m == 0.0` staying distinct from `None` |
| Thermal collector (`test_thermal_collector.py`) | hand-written 4-point×3-frame fixture; fully-null frame → zero points; partially-null frame → point emitted with the null field absent; unambiguous nearest-grid-point selection; station with no coords skipped; 503 cache_warming → `None` |
| Unmet-green precedence (`test_unmet_green_precedence.py`, `test_rules_evaluator.py`) | a matched other group/condition is not overridden by an unrelated unmet green group; the original spec-004 case (nothing else matches) still fails safe to red; covers all 3 duplicated decision blocks that don't delegate to `_evaluate_from_station_data` |
| Influx query clients (`test_influx_query_clients.py`) | forecast snapshot runs its pivot on the slow (60s) client and only the narrow `init_date` lookup on the fast one; forecast/thermal-forecast snapshot and the Föhn pressure history's forecast leg all generate an OR-chain `==` filter, never `contains()`; **snapshot never uses `last()`** — newest run wins regardless of row order, swissmeteo beats open-meteo, closest reading to `valid_time` wins within a run (v1.23.1 stale-forecast regression); **per-field gap-fill across runs** — a newer run's null frame is backfilled from the previous run, an older run fills only gaps and never overwrites a newer value, an all-null hour is omitted, all three readers share one `init_date` filter, and the fallback depth stays bounded (v1.23.2) |
| Reactive evaluation (`test_reactive_evaluation.py`) | station→ruleset reverse lookup incl. `station_b_id` and **both directions** of virtual-cluster expansion; zero-condition rule set never triggered; in-flight claim/release; the forecast-step→batch-tuple adapter; measurement routing (default vs `rule_decisions_forecast`); the read query's explicit `stop:` and absence of `contains()`; router cache-hit / nearest-hour / cache-miss-fallback / naive-datetime handling; the removed scheduler job; `_maybe_notify` suppression surviving the move |
| Replay cache (`test_replay_cache.py`) | fresh hit never rebuilds; **stale hit serves the stale payload immediately and refreshes in the background** (v1.23.3 stale-while-revalidate); concurrent stale hits (fired via `asyncio.gather`) trigger exactly one background refresh, never two; true cache miss still builds synchronously and stores; an empty-forecast background refresh is discarded (stale-but-populated entry survives) and releases the in-flight guard so a later request can retry |

Current suite: **250 passing** (2026-09-15).
