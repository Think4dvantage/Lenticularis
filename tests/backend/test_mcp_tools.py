"""Public MCP tool logic (specs/010-mcp-server) — exercised directly, no transport."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lenticularis.config import McpConfig
from lenticularis.mcp_server.registry import McpRegistry
from lenticularis.mcp_server.tools import McpToolError, PublicWeatherTools
from lenticularis.models.weather import WeatherStation

NOW = datetime.now(timezone.utc)


def _st(sid, network, lat=46.0, lon=8.0, name=None, canton="BE", elev=1000):
    return WeatherStation(station_id=sid, name=name or sid, network=network,
                          latitude=lat, longitude=lon, canton=canton, elevation=elev)


class FakeInflux:
    def __init__(self):
        self.calls: list = []
        self.latest: dict | None = None
        self.forecast: dict = {}
        self.history: list = []
        self.obs_snapshot: dict = {}
        self.latest_for: dict = {}
        self.fc_snapshot: dict = {}

    def query_latest_virtual(self, member_ids):
        self.calls.append(("latest", list(member_ids)))
        return self.latest

    def query_forecast_for_stations(self, station_ids, horizon_hours=120, keep_init_date=False):
        self.calls.append(("forecast", list(station_ids), horizon_hours, keep_init_date))
        return self.forecast

    def query_history_range(self, member_ids, start, end, every, fields):
        self.calls.append(("history", list(member_ids), every, list(fields)))
        return self.history

    def query_latest_for_stations(self, ids):
        self.calls.append(("foehn_latest", list(ids)))
        return {sid: v for sid, v in self.latest_for.items() if sid in ids}

    def query_observation_snapshot_for_stations(self, ids, at):
        return {sid: v for sid, v in self.obs_snapshot.items() if sid in ids}

    def query_forecast_snapshot_for_stations(self, ids, at):
        return {sid: v for sid, v in self.fc_snapshot.items() if sid in ids}


def _make(stations, influx=None, **cfg):
    raw = {s.station_id: s for s in stations}
    cfgm = McpConfig(**cfg)
    reg = McpRegistry(cfgm.verified_networks)
    reg.rebuild(raw, 300.0)
    state = SimpleNamespace(influx=influx or FakeInflux(), mcp_registry=reg, station_registry=raw)
    return PublicWeatherTools(lambda: state, cfgm, "test"), state


STATIONS = [
    _st("meteoswiss-JUN", "meteoswiss", 46.547, 7.985, "Jungfraujoch", "BE", 3571),
    _st("meteoswiss-SMA", "meteoswiss", 47.378, 8.566, "Zürich / Fluntern", "ZH", 556),
    _st("holfuy-1", "holfuy", 46.70, 7.80, "Niesen Holfuy"),
    _st("ecowitt-1", "ecowitt", 46.0, 8.0, "Private Garden"),
]


# --------------------------------------------------------------------------- US1

async def test_search_by_name_ignores_accents_and_hides_private():
    tools, _ = _make(STATIONS)
    res = await tools.search_stations(query="zuerich")
    assert [s["station_id"] for s in res["stations"]] == ["meteoswiss-SMA"]
    res = await tools.search_stations(query="Private")
    assert res["count"] == 0 and "hint" in res


async def test_search_near_point_sorted_with_distance():
    tools, _ = _make(STATIONS)
    res = await tools.search_stations(lat=46.6, lon=7.9, radius_km=50)
    ids = [s["station_id"] for s in res["stations"]]
    assert ids[0] in ("meteoswiss-JUN", "holfuy-1") and "ecowitt-1" not in ids
    assert all("distance_km" in s for s in res["stations"])


@pytest.mark.parametrize("kwargs", [
    {"lat": 46.0}, {"lat": 95.0, "lon": 8.0}, {"radius_km": 5.0}, {"lat": 46, "lon": 8, "radius_km": 500},
    {"network": "ecowitt"},
])
async def test_search_validation(kwargs):
    tools, _ = _make(STATIONS)
    with pytest.raises(McpToolError) as e:
        await tools.search_stations(**kwargs)
    assert e.value.code == "VALIDATION_FAILED"


async def test_search_limit_is_capped():
    many = [_st(f"meteoswiss-{i}", "meteoswiss", 46 + i * 0.01, 8 + i * 0.01) for i in range(60)]
    tools, _ = _make(many, max_search_results=5)
    res = await tools.search_stations(limit=1000)
    assert res["count"] == 5 and res["truncated"] is True


# --------------------------------------------------------------------------- US2

async def test_current_weather_shape_units_and_fresh():
    tools, state = _make(STATIONS)
    state.influx.latest = {"timestamp": NOW - timedelta(minutes=7), "wind_speed": 12.345, "temperature": -3.0,
                           "wind_gust": None, "station_id": "x", "network": "y"}
    res = await tools.get_current_weather("meteoswiss-JUN")
    assert res["values"] == {"wind_speed": 12.35, "temperature": -3.0}      # None + non-allowlisted dropped
    assert res["stale"] is False and res["age_minutes"] in (7, 8)
    assert res["units"]["wind_speed"] == "km/h" and res["units"]["temperature"] == "°C"
    assert res["source"] == "meteoswiss" and res["timezone"] == "UTC"


async def test_current_weather_stale_is_labelled():
    tools, state = _make(STATIONS)
    state.influx.latest = {"timestamp": NOW - timedelta(hours=5), "wind_speed": 3.0}
    res = await tools.get_current_weather("meteoswiss-JUN")
    assert res["stale"] is True and "not current" in res["message"]


async def test_current_weather_no_data():
    tools, _ = _make(STATIONS)
    res = await tools.get_current_weather("meteoswiss-JUN")
    assert res["values"] == {} and res["stale"] is True


@pytest.mark.parametrize("sid,code", [
    ("ecowitt-1", "ENTITY_NOT_FOUND"), ("nope", "ENTITY_NOT_FOUND"),
    ('x"; drop', "VALIDATION_FAILED"), ("", "VALIDATION_FAILED"), ("a" * 65, "VALIDATION_FAILED"),
])
async def test_station_id_validation_and_private_not_found(sid, code):
    tools, state = _make(STATIONS)
    with pytest.raises(McpToolError) as e:
        await tools.get_current_weather(sid)
    assert e.value.code == code
    assert state.influx.calls == []          # nothing reached the database


async def test_pooled_members_are_verified_only():
    stations = [_st("meteoswiss-A", "meteoswiss"), _st("ecowitt-1", "ecowitt", 46.0001, 8.0001),
                _st("holfuy-1", "holfuy", 46.0002, 8.0002)]
    tools, state = _make(stations)
    await tools.get_current_weather("meteoswiss-A")
    assert state.influx.calls[0] == ("latest", ["meteoswiss-A", "holfuy-1"])


async def test_hollandiahuette_faulty_fields_suppressed_everywhere():
    jfb = _st("jfb-hollandiahutte-sac", "jfb", 46.5, 8.0, "Hollandiahütte SAC")
    tools, state = _make([jfb])
    state.influx.latest = {"timestamp": NOW, "temperature": 23.0, "humidity": 40.0, "pressure_qfe": 928.0,
                           "pressure_qff": 1000.0, "wind_speed": 20.0}
    cur = await tools.get_current_weather("jfb-hollandiahutte-sac")
    assert cur["values"] == {"wind_speed": 20.0}

    state.influx.history = [{"timestamp": NOW - timedelta(hours=1), "temperature": 22.0, "wind_speed": 5.0}]
    hist = await tools.get_weather_history("jfb-hollandiahutte-sac", hours=6)
    assert all(set(r) <= {"time", "wind_speed"} for r in hist["data"])

    state.influx.forecast = {"jfb-hollandiahutte-sac": {
        (NOW + timedelta(hours=1)).isoformat(): {"temperature": 20.0, "temperature_min": 18.0, "wind_speed": 9.0}}}
    fc = await tools.get_forecast("jfb-hollandiahutte-sac", 6)
    assert all(set(r) <= {"time", "wind_speed"} for r in fc["data"])


# --------------------------------------------------------------------------- US4

async def test_forecast_reports_model_run_missing_hours_and_keeps_init_date():
    tools, state = _make(STATIONS)
    h1 = (NOW + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    h2 = h1 + timedelta(hours=1)
    state.influx.forecast = {"meteoswiss-JUN": {
        h1.isoformat(): {"wind_speed": 10.0, "wind_speed_min": 8.0, "wind_speed_max": 14.0,
                         "source": "swissmeteo", "model": "icon-ch1", "init_date": "2026-10-08T04", "init_time": "x"},
        h2.isoformat(): {"wind_speed": None, "wind_gust": None, "source": "swissmeteo", "model": "icon-ch1",
                         "init_date": "2026-10-08T04"},
    }}
    res = await tools.get_forecast("meteoswiss-JUN", hours=6)
    assert state.influx.calls[0][-1] is True                      # keep_init_date requested
    assert res["source"] == "swissmeteo" and res["model"] == "icon-ch1"
    assert res["forecast_issued"] == "2026-10-08T04:00:00Z"
    assert [r["wind_speed"] for r in res["data"]] == [10.0] and res["data"][0]["wind_speed_max"] == 14.0
    assert res["missing_hours"]["count"] >= 1 and res["missing_hours"]["ranges"]
    assert "never interpolated" in res["note"] or "never" in res["note"]


async def test_forecast_hours_validation():
    tools, _ = _make(STATIONS)
    for bad in (0, 121):
        with pytest.raises(McpToolError):
            await tools.get_forecast("meteoswiss-JUN", bad)


# --------------------------------------------------------------------------- US3

async def test_history_lookback_resolution_and_aggregation_labels():
    tools, state = _make(STATIONS)
    state.influx.history = [
        {"timestamp": NOW - timedelta(hours=2), "wind_speed": 10.0, "wind_gust": 25.0, "precipitation": 1.2,
         "wind_direction": 270},
        {"timestamp": NOW - timedelta(hours=1)},                          # empty row dropped
    ]
    res = await tools.get_weather_history("meteoswiss-JUN", hours=24)
    assert res["count"] == 1 and res["resolution"] in ("10m", "30m", "1h")
    assert res["aggregation"] == {"precipitation": "sum", "wind_direction": "last",
                                  "wind_gust": "max", "wind_speed": "mean"}
    assert res["timezone"] == "UTC" and res["units"]["precipitation"] == "mm"


async def test_history_never_exceeds_point_cap_by_coarsening():
    tools, state = _make(STATIONS, max_history_points=100)
    res = await tools.get_weather_history("meteoswiss-JUN", hours=720, resolution="10m")
    assert res["resolution_bumped"] is True and "coarsened" in res["note"]
    every = state.influx.calls[0][2]
    assert every in ("12h", "1d", "6h")           # 720 h at <=100 points needs >= 7.2 h buckets


async def test_history_range_validation():
    tools, _ = _make(STATIONS)
    cases = [
        dict(hours=5, start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"),
        dict(start="2026-01-01T00:00:00Z"),
        dict(start="2026-02-01T00:00:00Z", end="2026-01-01T00:00:00Z"),
        dict(start="2024-01-01T00:00:00Z", end="2026-01-01T00:00:00Z"),
        dict(start="garbage", end="also garbage"),
        dict(hours=721),
        dict(hours=5, fields=["password"]),
        dict(hours=5, resolution="7m"),
    ]
    for kw in cases:
        with pytest.raises(McpToolError) as e:
            await tools.get_weather_history("meteoswiss-JUN", **kw)
        assert e.value.code == "VALIDATION_FAILED", kw


async def test_history_empty_range_is_success_with_hint():
    tools, _ = _make(STATIONS)
    res = await tools.get_weather_history("meteoswiss-JUN", hours=3)
    assert res["count"] == 0 and "hint" in res


# --------------------------------------------------------------------------- US5 / US6

async def test_foehn_drops_unverified_inputs_at_runtime(monkeypatch):
    from lenticularis import foehn_detection as fd

    monkeypatch.setattr(fd, "get_all_station_ids_from_config", lambda cfg=None: ["meteoswiss-JUN", "ecowitt-1"])
    monkeypatch.setattr(fd, "get_required_lookback_hours", lambda cfg=None: set())
    seen = {}

    def fake_eval(region, latest, historical=None):
        seen["latest"] = set(latest)
        return {"key": "r", "status": "inactive"}

    monkeypatch.setattr(fd, "eval_region", fake_eval)
    monkeypatch.setattr(fd, "build_all_pressures", lambda latest, pairs=None: [])
    monkeypatch.setattr(fd, "regions_from_config", lambda cfg=None: [object()])
    monkeypatch.setattr(fd, "pressure_pairs_from_config", lambda cfg=None: [])
    tools, state = _make(STATIONS)
    state.influx.latest_for = {"meteoswiss-JUN": {"x": 1}, "ecowitt-1": {"x": 2}}
    res = await tools.get_foehn_status()
    assert seen["latest"] == {"meteoswiss-JUN"}
    assert res["inputs_excluded"] == ["ecowitt-1"]


async def test_foehn_default_config_stations_are_all_verified_networks():
    """If an admin ever adds a private station to the default config this fails loudly in CI."""
    from lenticularis import foehn_detection as fd

    ids = fd.get_all_station_ids_from_config(None)
    allowed = tuple(f"{n}-" for n in McpConfig().verified_networks)
    assert ids and all(i.startswith(allowed) for i in ids), ids


async def test_foehn_validates_time():
    tools, _ = _make(STATIONS)
    with pytest.raises(McpToolError):
        await tools.get_foehn_status(at="not a time")
    with pytest.raises(McpToolError):
        await tools.get_foehn_status(at=(NOW + timedelta(days=30)).isoformat())


async def test_describe_service_states_policy_and_counts():
    tools, _ = _make(STATIONS)
    res = await tools.describe_service()
    assert res["coverage"]["station_count"] == 3
    assert "Wunderground" in res["data_policy"] and res["units"]["precipitation"] == "mm"
    assert "describe_service" in res["tools"]
