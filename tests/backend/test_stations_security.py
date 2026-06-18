"""
Station endpoint security tests — regression guard for the T01 injection fix.

The station registry is empty in these tests (test_app sets display_registry={}).
Any station_id not present in the registry MUST return 404, not leak InfluxDB
internals or allow path traversal.
"""
from __future__ import annotations

import pytest


async def test_unknown_station_id_returns_404(client):
    r = await client.get("/api/stations/nonexistent-station")
    assert r.status_code == 404


async def test_station_id_with_slash_returns_404_or_422(client):
    # Path traversal attempt — FastAPI will either 404 or strip the path.
    r = await client.get("/api/stations/holfuy-123%2F..%2Fetc%2Fpasswd")
    assert r.status_code in {404, 422}


async def test_station_id_with_special_chars_returns_404(client):
    # InfluxQL / Flux injection attempt
    r = await client.get("/api/stations/'; DROP MEASUREMENT weather_data; --")
    assert r.status_code in {404, 422}


async def test_station_latest_unknown_returns_404(client):
    r = await client.get("/api/stations/fake-id/latest")
    assert r.status_code == 404


async def test_station_history_unknown_returns_404(client):
    r = await client.get("/api/stations/fake-id/history")
    assert r.status_code == 404


async def test_station_forecast_unknown_returns_404(client):
    r = await client.get("/api/stations/fake-id/forecast")
    assert r.status_code == 404


async def test_stations_list_returns_empty_for_empty_registry(client):
    r = await client.get("/api/stations")
    assert r.status_code == 200
    body = r.json()
    # Empty registry → empty list
    assert isinstance(body, list)
    assert len(body) == 0
