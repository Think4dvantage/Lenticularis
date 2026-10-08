"""Verified-only registry for the public MCP server (specs/010-mcp-server, invariant 1)."""
from __future__ import annotations

from lenticularis.mcp_server.registry import McpRegistry
from lenticularis.mcp_server.sanitize import name_matches
from lenticularis.models.weather import WeatherStation

VERIFIED = ["meteoswiss", "slf", "metar", "holfuy", "windline", "fga", "jfb"]


def _st(sid: str, network: str, lat: float = 46.0, lon: float = 8.0, name: str | None = None) -> WeatherStation:
    return WeatherStation(station_id=sid, name=name or sid, network=network, latitude=lat, longitude=lon)


def _raw(*stations: WeatherStation) -> dict[str, WeatherStation]:
    return {s.station_id: s for s in stations}


def test_private_networks_never_listed():
    reg = McpRegistry(VERIFIED)
    reg.rebuild(_raw(
        _st("meteoswiss-A", "meteoswiss", 46.0, 8.0),
        _st("ecowitt-1", "ecowitt", 47.0, 9.0),
        _st("wunderground-1", "wunderground", 47.5, 9.5),
    ), 300.0)
    assert set(reg.stations) == {"meteoswiss-A"}
    assert reg.resolve("ecowitt-1") is None
    assert reg.resolve("wunderground-1") is None


def test_foehn_virtual_stations_excluded():
    reg = McpRegistry(VERIFIED + ["foehn"])  # even if someone allowlists it by mistake
    reg.rebuild(_raw(_st("foehn-beo", "foehn"), _st("meteoswiss-A", "meteoswiss", 46.5, 8.5)), 300.0)
    assert "foehn-beo" not in reg.stations


def test_dedup_member_leak_closed():
    """MeteoSwiss + Ecowitt at the same spot: the website would pool both. MCP must not."""
    reg = McpRegistry(VERIFIED)
    reg.rebuild(_raw(
        _st("meteoswiss-A", "meteoswiss", 46.0, 8.0),
        _st("ecowitt-1", "ecowitt", 46.0001, 8.0001),
        _st("holfuy-1", "holfuy", 46.0002, 8.0002),
    ), 300.0)
    assert reg.member_ids("meteoswiss-A") == ["meteoswiss-A", "holfuy-1"]
    assert "ecowitt-1" not in reg.member_ids("meteoswiss-A")
    assert reg.resolve("ecowitt-1") is None
    assert reg.resolve("holfuy-1") == "meteoswiss-A"


def test_private_canonical_cluster_does_not_hide_verified_member():
    """jfb ranks below ecowitt in website priority; MCP dedups verified-only so jfb stands alone."""
    reg = McpRegistry(VERIFIED)
    reg.rebuild(_raw(
        _st("ecowitt-1", "ecowitt", 46.0, 8.0),
        _st("jfb-x", "jfb", 46.0001, 8.0001),
    ), 300.0)
    assert set(reg.stations) == {"jfb-x"}
    assert reg.member_ids("jfb-x") == ["jfb-x"]


def test_manual_pair_with_unverified_station_is_ignored():
    reg = McpRegistry(VERIFIED)
    reg.rebuild(_raw(
        _st("meteoswiss-A", "meteoswiss", 46.0, 8.0),
        _st("ecowitt-1", "ecowitt", 47.0, 9.0),
    ), 300.0, manual_pairs=[("meteoswiss-A", "ecowitt-1")])
    assert reg.member_ids("meteoswiss-A") == ["meteoswiss-A"]


def test_new_network_hidden_until_allowlisted():
    reg = McpRegistry(VERIFIED)
    reg.rebuild(_raw(_st("newnet-1", "newnet")), 300.0)
    assert reg.stations == {}


def test_rebuild_replaces_state():
    reg = McpRegistry(VERIFIED)
    reg.rebuild(_raw(_st("meteoswiss-A", "meteoswiss")), 300.0)
    reg.rebuild(_raw(_st("slf-B", "slf", 47, 9)), 300.0)
    assert set(reg.stations) == {"slf-B"}


def test_search_by_point_is_nearest_first_and_radius_bound():
    reg = McpRegistry(VERIFIED)
    reg.rebuild(_raw(
        _st("meteoswiss-near", "meteoswiss", 46.01, 8.0, "Near"),
        _st("meteoswiss-mid", "meteoswiss", 46.1, 8.0, "Mid"),
        _st("meteoswiss-far", "meteoswiss", 47.5, 8.0, "Far"),
    ), 300.0)
    res = reg.search(lat=46.0, lon=8.0, radius_km=30)
    assert [s.station_id for s, _ in res] == ["meteoswiss-near", "meteoswiss-mid"]
    assert res[0][1] < res[1][1]


def test_name_matching_ignores_accents_and_umlauts():
    assert name_matches("zuerich", "Zürich / Fluntern")
    assert name_matches("zurich", "Zürich / Fluntern")
    assert name_matches("Säntis", "Saentis")
    assert name_matches("geneve", "Genève")
    assert not name_matches("basel", "Zürich")
