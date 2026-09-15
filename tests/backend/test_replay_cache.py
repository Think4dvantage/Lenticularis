"""
Replay cache tests — stale-while-revalidate (v1.23.3).

The TTL (5 min) is shorter than the warm-up cadence (startup + once per hourly
forecast run), so a request landing in that gap used to delete the stale entry
and rebuild synchronously — the ~10s cold-miss cost the user reported. These
tests cover the fix: a stale hit is served immediately and refreshed in the
background, with at most one refresh in flight per cache key.

``_build_replay_payload`` is monkeypatched throughout — these are control-flow
tests (hit / stale-serve-and-refresh / miss-and-store), not payload-content
tests, so FakeInflux's empty responses are irrelevant here.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from lenticularis.api.routers import stations as stations_router


@pytest.fixture(autouse=True)
def _isolate_replay_cache():
    """The cache and in-flight set are module-level — reset around every test."""
    stations_router._replay_cache._data.clear()
    stations_router._replay_refresh_inflight.clear()
    yield
    stations_router._replay_cache._data.clear()
    stations_router._replay_refresh_inflight.clear()


def _seed(cache_key: str, payload: dict, age_s: float) -> None:
    stations_router._replay_cache[cache_key] = (payload, time.monotonic() - age_s)


_PARAMS = {
    "start": "2026-01-01T00:00:00.000Z",
    "end": "2026-01-01T23:59:59.000Z",
    "forecast_hours": 24,
}
_CACHE_KEY = "None|2026-01-01T00:00:00.000Z|2026-01-01T23:59:59.000Z|True|24"


async def test_fresh_cache_hit_returns_cached_payload_without_rebuild(client, monkeypatch):
    sentinel = {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 1, "sentinel": True}
    _seed(_CACHE_KEY, sentinel, age_s=1)

    def _boom(*a, **kw):
        raise AssertionError("a fresh hit must not rebuild")

    monkeypatch.setattr(stations_router, "_build_replay_payload", _boom)

    r = await client.get("/api/stations/replay", params=_PARAMS)
    assert r.status_code == 200
    assert r.json()["sentinel"] is True


async def test_stale_hit_serves_stale_payload_and_refreshes_in_background(client, monkeypatch):
    sentinel = {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 1, "sentinel": True}
    _seed(_CACHE_KEY, sentinel, age_s=stations_router._REPLAY_CACHE_TTL_S + 10)

    rebuild_calls = []

    def _sync_rebuild(*a, **kw):
        rebuild_calls.append(1)
        return {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 1, "rebuilt": True}

    monkeypatch.setattr(stations_router, "_build_replay_payload", _sync_rebuild)

    r = await client.get("/api/stations/replay", params=_PARAMS)
    assert r.status_code == 200
    # The request itself must be served the stale payload immediately, not a rebuild.
    assert r.json()["sentinel"] is True

    # Give the scheduled background refresh a chance to run (crosses to the executor and back).
    await asyncio.sleep(0.2)
    assert rebuild_calls == [1]

    cached = stations_router._replay_cache.get(_CACHE_KEY)
    assert cached is not None
    assert cached[0]["rebuilt"] is True


async def test_concurrent_stale_hits_trigger_only_one_background_refresh(client, monkeypatch):
    sentinel = {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 1, "sentinel": True}
    _seed(_CACHE_KEY, sentinel, age_s=stations_router._REPLAY_CACHE_TTL_S + 10)

    rebuild_calls = []

    def _sync_rebuild(*a, **kw):
        rebuild_calls.append(1)
        return {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 1, "rebuilt": True}

    monkeypatch.setattr(stations_router, "_build_replay_payload", _sync_rebuild)

    # Fire both requests concurrently so they race for the in-flight refresh guard —
    # awaiting them one after another would let the first request's background
    # refresh finish before the second even starts, which isn't what "concurrent" means here.
    r1, r2 = await asyncio.gather(
        client.get("/api/stations/replay", params=_PARAMS),
        client.get("/api/stations/replay", params=_PARAMS),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    await asyncio.sleep(0.2)
    assert len(rebuild_calls) == 1


async def test_cache_miss_builds_synchronously_and_stores(client, monkeypatch):
    calls = []

    def _sync_rebuild(*a, **kw):
        calls.append(1)
        return {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 1, "built": True}

    monkeypatch.setattr(stations_router, "_build_replay_payload", _sync_rebuild)

    params = {
        "start": "2026-02-01T00:00:00.000Z",
        "end": "2026-02-01T23:59:59.000Z",
        "forecast_hours": 24,
    }
    r = await client.get("/api/stations/replay", params=params)
    assert r.status_code == 200
    assert r.json()["built"] is True
    assert calls == [1]

    cache_key = "None|2026-02-01T00:00:00.000Z|2026-02-01T23:59:59.000Z|True|24"
    cached = stations_router._replay_cache.get(cache_key)
    assert cached is not None
    assert cached[0]["built"] is True


async def test_no_forecast_data_skips_caching_the_background_refresh(client, monkeypatch):
    """A refresh that comes back with no forecast data must not overwrite the stale
    (but populated) entry — the next stale hit should retry rather than being stuck
    with an empty payload."""
    sentinel = {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 1, "sentinel": True}
    _seed(_CACHE_KEY, sentinel, age_s=stations_router._REPLAY_CACHE_TTL_S + 10)

    def _empty_rebuild(*a, **kw):
        return {"data": {}, "station_count": 0, "obs_frame_count": 0, "fc_frame_count": 0}

    monkeypatch.setattr(stations_router, "_build_replay_payload", _empty_rebuild)

    r = await client.get("/api/stations/replay", params=_PARAMS)
    assert r.status_code == 200
    assert r.json()["sentinel"] is True

    await asyncio.sleep(0.2)
    # Stale sentinel must still be there — the empty rebuild was not stored.
    cached = stations_router._replay_cache.get(_CACHE_KEY)
    assert cached is not None
    assert cached[0]["sentinel"] is True
    # And the in-flight guard must have been released so a later request can retry.
    assert _CACHE_KEY not in stations_router._replay_refresh_inflight
