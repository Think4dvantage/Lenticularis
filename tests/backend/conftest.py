"""
Shared fixtures for Lenticularis backend tests.

Creates a FastAPI test app with:
  - In-memory SQLite (schema built at fixture time, rolled back between tests)
  - FakeInflux stub that returns empty data by default
  - No scheduler, no real InfluxDB, no network

Usage:
    async def test_something(client):
        r = await client.get("/api/stations")
        assert r.status_code == 200
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import lenticularis.config as _lenti_config
from lenticularis.config import (
    APIConfig,
    AuthConfig,
    DatabaseConfig,
    InfluxDBConfig,
    LoggingConfig,
    MainConfig,
    OAuthConfig,
    OllamaConfig,
    SmtpConfig,
    StationDedupConfig,
)
from lenticularis.database.db import get_db
from lenticularis.database.models import Base

# ---------------------------------------------------------------------------
# Test config — no real infrastructure, 32-char JWT secret
# ---------------------------------------------------------------------------

_JWT_SECRET = "test-secret-that-is-at-least-32-chars!!"

_TEST_CONFIG = MainConfig(
    influxdb=InfluxDBConfig(enabled=False),
    collectors=[],
    database=DatabaseConfig(path=":memory:"),
    auth=AuthConfig(jwt_secret=_JWT_SECRET),
    logging=LoggingConfig(level="warning", file=""),
    api=APIConfig(),
    ollama=OllamaConfig(enabled=False),
    station_dedup=StationDedupConfig(),
    oauth=OAuthConfig(),
    smtp=SmtpConfig(),
)


# ---------------------------------------------------------------------------
# Fake InfluxDB client — all queries return empty/None by default
# ---------------------------------------------------------------------------

class FakeInflux:
    def query_latest(self, station_id: str):
        return None

    def query_latest_for_stations(self, station_ids):
        return {}

    def query_latest_all_stations(self):
        return {}

    def query_latest_virtual(self, member_ids):
        return None

    def query_history(self, station_id, hours=24):
        return []

    def query_history_all_stations(self, start, end):
        return {}

    def query_history_for_stations(self, station_ids, days=30):
        return {}

    def query_forecast(self, station_id, hours=120):
        return []

    def query_forecast_replay(self, start, end):
        return {}

    def query_forecast_for_stations(self, station_ids, hours=120):
        return {}

    def query_forecast_accuracy_ranking(self, station_ids=None):
        return []

    def query_forecast_accuracy(self, station_id, days=30):
        return []

    def query_observation_snapshot_for_stations(self, station_ids, at_time):
        return {}

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    """Inject the test config as the singleton so get_config() never hits disk."""
    monkeypatch.setattr(_lenti_config, "_config", _TEST_CONFIG)


@pytest.fixture
def db_engine():
    # StaticPool is required, not cosmetic: an in-memory SQLite engine defaults to
    # SingletonThreadPool, which opens a separate connection — and therefore a separate,
    # empty database — per thread. FastAPI runs sync dependencies (get_current_user,
    # require_pilot, …) in a worker threadpool, so they would not see the tables that
    # create_all() built on the main thread. StaticPool shares the one connection.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest_asyncio.fixture
async def test_app(db_engine):
    """FastAPI app wired to in-memory SQLite + FakeInflux, lifespan replaced."""
    from lenticularis.api.main import create_app

    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    fake_influx = FakeInflux()

    @asynccontextmanager
    async def _test_lifespan(app):
        """No-op stand-in for the real lifespan (scheduler, InfluxDB, collectors)."""
        yield

    app = create_app()
    app.router.lifespan_context = _test_lifespan

    # Set app.state directly. It cannot be done from the lifespan: httpx's ASGITransport
    # never emits ASGI lifespan events, so no lifespan_context ever runs under it — the
    # state would stay unset and every route calling _get_influx() would 503.
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
    """httpx AsyncClient pointed at the test app (lifespan managed by the context manager)."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def make_token():
    """Return a helper that mints a bearer Authorization header for a given user."""
    from lenticularis.services.auth import create_access_token

    def _make(user_id: str, role: str = "pilot") -> dict[str, str]:
        token = create_access_token(user_id, role)
        return {"Authorization": f"Bearer {token}"}

    return _make
