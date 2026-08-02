"""
Tests for the SwissMeteo thermal forecast collector.

Pure-logic tests: the lsmfapi payload is a small hand-written fixture (4 grid
points x 3 frames), never a real 28.6 MB capture. No network, no InfluxDB.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lenticularis.collectors.forecast_thermal_swissmeteo import (
    ForecastThermalSwissMeteoCollector,
)


# ---------------------------------------------------------------------------
# Fixture payload
# ---------------------------------------------------------------------------

_GRID = [
    {"lat": 47.0, "lon": 8.0},   # P0 — near station_a
    {"lat": 47.5, "lon": 8.5},   # P1
    {"lat": 46.0, "lon": 7.0},   # P2
    {"lat": 45.5, "lon": 6.5},   # P3
]

_INIT_TIME = "2026-07-31T00:00:00Z"


def _payload() -> dict:
    return {
        "init_time": _INIT_TIME,
        "model": "icon-ch1+ch2",
        "stride_km": 10,
        "grid": _GRID,
        "frames": [
            # Frame 0 — fully null across every raw field, every point.
            {
                "valid_time": "2026-07-31T00:00:00Z",
                "solar": [None, None, None, None],
                "sunshine": [None, None, None, None],
                "cloud_cover": [None, None, None, None],
                "cloud_low": [None, None, None, None],
                "cloud_mid": [None, None, None, None],
                "cloud_high": [None, None, None, None],
                "freezing_level": [None, None, None, None],
                "cape": [None, None, None, None],
                "cin": [None, None, None, None],
                "lcl": [None, None, None, None],
                "lfc": [None, None, None, None],
                "tke": [None, None, None, None],
            },
            # Frame 1 — P0 partially null: solar is null, but cin/lcl/lfc/tke are present.
            {
                "valid_time": "2026-07-31T01:00:00Z",
                "solar": [None, 500.0, 500.0, 500.0],
                "sunshine": [None, 40.0, 40.0, 40.0],
                "cloud_cover": [None, 30.0, 30.0, 30.0],
                "cloud_low": [None, None, None, None],
                "cloud_mid": [None, None, None, None],
                "cloud_high": [None, None, None, None],
                "freezing_level": [None, 3500.0, 3500.0, 3500.0],
                "cape": [None, 200.0, 200.0, 200.0],
                "cin": [-50.0, -50.0, -50.0, -50.0],
                "lcl": [2000.0, 2000.0, 2000.0, 2000.0],
                "lfc": [2600.0, 2600.0, 2600.0, 2600.0],
                "tke": [1.0, 1.0, 1.0, 1.0],
            },
            # Frame 2 — distinct-per-point solar (proves nearest-grid selection), and
            # cin null at every point (proves cin=None feeds derived metrics as 0, not a cap).
            {
                "valid_time": "2026-07-31T02:00:00Z",
                "solar": [600.0, 601.0, 602.0, 603.0],
                "sunshine": [50.0, 50.0, 50.0, 50.0],
                "cloud_cover": [20.0, 20.0, 20.0, 20.0],
                "cloud_low": [0.0, 0.0, 0.0, 0.0],
                "cloud_mid": [10.0, 10.0, 10.0, 10.0],
                "cloud_high": [5.0, 5.0, 5.0, 5.0],
                "freezing_level": [3800.0, 3800.0, 3800.0, 3800.0],
                "cape": [500.0, 500.0, 500.0, 500.0],
                "cin": [None, None, None, None],
                "lcl": [2200.0, 2200.0, 2200.0, 2200.0],
                "lfc": [2400.0, 2400.0, 2400.0, 2400.0],
                "tke": [1.5, 1.5, 1.5, 1.5],
                "lcl_min": [2000.0, 2000.0, 2000.0, 2000.0],
                "lcl_max": [2400.0, 2400.0, 2400.0, 2400.0],
            },
        ],
    }


def _station(station_id, lat, lon, elevation=1200.0, network="testnet"):
    return SimpleNamespace(
        station_id=station_id, network=network,
        latitude=lat, longitude=lon, elevation=elevation,
    )


# station_a sits close to P0 (47.0, 8.0) but not exactly on it — nearest-point
# selection must pick P0 over the far-away P1/P2/P3.
_STATION_A = _station("test-a", 47.01, 8.01)
_STATION_NO_COORDS = _station("test-nocoords", None, None)


@pytest.fixture
def collector():
    return ForecastThermalSwissMeteoCollector()


def _points_at(points, valid_time, station_id):
    return [p for p in points if p.valid_time.isoformat().startswith(valid_time[:16]) and p.station_id == station_id]


# ---------------------------------------------------------------------------
# build_station_points
# ---------------------------------------------------------------------------

def test_fully_null_frame_emits_zero_points(collector):
    points = collector.build_station_points(_payload(), [_STATION_A])
    frame0_points = _points_at(points, "2026-07-31T00:00:00Z", "test-a")
    assert frame0_points == []


def test_partially_null_frame_emits_point_with_null_solar(collector):
    points = collector.build_station_points(_payload(), [_STATION_A])
    frame1_points = _points_at(points, "2026-07-31T01:00:00Z", "test-a")
    assert len(frame1_points) == 1
    point = frame1_points[0]
    assert point.solar is None
    assert point.cin == -50.0
    assert point.lcl == 2000.0
    # solar is the trigger for thermal_strength — null solar makes it null too
    assert point.thermal_strength is None


def test_cin_null_is_not_treated_as_a_cap(collector):
    points = collector.build_station_points(_payload(), [_STATION_A])
    frame2_points = _points_at(points, "2026-07-31T02:00:00Z", "test-a")
    assert len(frame2_points) == 1
    point = frame2_points[0]
    assert point.cin is None
    # cape=500 (>=300 band) + cin_effective(None)=0.0 > -25 uncapped threshold → +1 bonus;
    # cloud_mid=10 <= 50 → no cloud_mid bump. Base band for 500 is 1, so risk = 1 + 1 = 2.
    assert point.overdevelopment_risk == 2


def test_nearest_grid_point_is_unambiguous(collector):
    points = collector.build_station_points(_payload(), [_STATION_A])
    frame2_points = _points_at(points, "2026-07-31T02:00:00Z", "test-a")
    # P0's solar is 600.0 — distinct from every other grid point's value.
    # station_a is close to P0 and far from P1-P3, so this proves nearest selection.
    assert frame2_points[0].solar == 600.0


def test_station_without_coords_is_skipped(collector):
    points = collector.build_station_points(_payload(), [_STATION_A, _STATION_NO_COORDS])
    assert all(p.station_id != "test-nocoords" for p in points)


def test_empty_grid_or_frames_yields_no_points(collector):
    assert collector.build_station_points({"init_time": _INIT_TIME, "grid": [], "frames": []}, [_STATION_A]) == []


def test_missing_init_time_yields_no_points(collector):
    assert collector.build_station_points({"grid": _GRID, "frames": []}, [_STATION_A]) == []


# ---------------------------------------------------------------------------
# fetch() — 503 cache_warming handling
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, **kwargs):
        return self._response

    async def aclose(self):
        pass


async def test_fetch_returns_none_on_cache_warming(collector):
    collector._http_client = _FakeAsyncClient(_FakeResponse(status_code=503))
    result = await collector.fetch()
    assert result is None


async def test_fetch_returns_parsed_payload(collector):
    payload = _payload()
    collector._http_client = _FakeAsyncClient(_FakeResponse(status_code=200, payload=payload))
    result = await collector.fetch()
    assert result == payload
