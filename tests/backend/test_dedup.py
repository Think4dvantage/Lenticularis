"""
Tests for the proximity-based station deduplication service.

build_deduped_registry is pure (no I/O), so no fixtures are needed.
"""
from __future__ import annotations

import pytest

from lenticularis.models.weather import WeatherStation
from lenticularis.services.dedup import build_deduped_registry, haversine_m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _station(sid, lat, lon, network="holfuy"):
    return WeatherStation(
        station_id=sid,
        name=sid,
        network=network,
        latitude=lat,
        longitude=lon,
    )


# A pair of coordinates known to be ~30 m apart (well inside 50 m threshold)
_LAT_A = 46.8182
_LON_A = 9.8472
_LAT_B = _LAT_A + 0.00025   # ~28 m north


# ---------------------------------------------------------------------------
# Geometry sanity
# ---------------------------------------------------------------------------

def test_haversine_same_point_is_zero():
    assert haversine_m(46.0, 8.0, 46.0, 8.0) == pytest.approx(0.0)


def test_haversine_known_distance():
    # ~111 km per degree of latitude at equator
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert 111_000 < d < 112_000


# ---------------------------------------------------------------------------
# Dedup logic
# ---------------------------------------------------------------------------

def test_two_distant_stations_remain_separate():
    a = _station("holfuy-1", 46.0, 8.0)
    b = _station("holfuy-2", 47.0, 9.0)  # > 100 km away
    raw = {a.station_id: a, b.station_id: b}
    display, virtual = build_deduped_registry(raw, distance_m=50.0)
    assert len(display) == 2
    assert len(virtual) == 0


def test_two_close_stations_collapse_to_one():
    a = _station("holfuy-1", _LAT_A, _LON_A, network="holfuy")
    b = _station("windline-2", _LAT_B, _LON_A, network="windline")
    raw = {a.station_id: a, b.station_id: b}
    display, virtual = build_deduped_registry(raw, distance_m=50.0)
    assert len(display) == 1
    assert len(virtual) == 1
    canon_id = next(iter(display))
    assert len(virtual[canon_id]) == 2


def test_canonical_is_higher_priority_network():
    # meteoswiss > holfuy in NETWORK_PRIORITY
    ms = _station("meteoswiss-X", _LAT_A, _LON_A, network="meteoswiss")
    hf = _station("holfuy-Y", _LAT_B, _LON_A, network="holfuy")
    raw = {ms.station_id: ms, hf.station_id: hf}
    display, virtual = build_deduped_registry(raw, distance_m=50.0)
    assert "meteoswiss-X" in display
    assert "holfuy-Y" not in display
    assert virtual["meteoswiss-X"] == ["meteoswiss-X", "holfuy-Y"]


def test_manual_pair_forces_merge_regardless_of_distance():
    a = _station("holfuy-1", 46.0, 8.0)
    b = _station("windline-2", 47.0, 9.0)  # far apart
    raw = {a.station_id: a, b.station_id: b}
    display, virtual = build_deduped_registry(
        raw, distance_m=50.0, manual_pairs=[("holfuy-1", "windline-2")]
    )
    assert len(display) == 1
    assert len(virtual) == 1


def test_foehn_stations_excluded_from_dedup():
    real = _station("holfuy-1", _LAT_A, _LON_A, network="holfuy")
    foehn = _station("foehn-haslital", _LAT_B, _LON_A, network="foehn")
    raw = {real.station_id: real, foehn.station_id: foehn}
    display, virtual = build_deduped_registry(raw, distance_m=50.0)
    # Both appear in display but are not merged
    assert len(display) == 2
    assert len(virtual) == 0


def test_three_station_transitive_cluster():
    # A–B within 50 m, B–C within 50 m → all three should merge
    c_lat = _LAT_B + 0.00025  # another ~28 m north of B
    a = _station("holfuy-1", _LAT_A, _LON_A)
    b = _station("windline-2", _LAT_B, _LON_A)
    c = _station("ecowitt-3", c_lat, _LON_A)
    raw = {s.station_id: s for s in [a, b, c]}
    display, virtual = build_deduped_registry(raw, distance_m=50.0)
    assert len(display) == 1
    canon_id = next(iter(display))
    assert len(virtual[canon_id]) == 3


def test_manual_pair_with_unknown_id_is_silently_ignored():
    a = _station("holfuy-1", _LAT_A, _LON_A)
    raw = {a.station_id: a}
    # "unknown-id" is not in raw — should not raise, just ignore
    display, virtual = build_deduped_registry(
        raw, distance_m=50.0, manual_pairs=[("holfuy-1", "unknown-id")]
    )
    assert len(display) == 1
    assert len(virtual) == 0
