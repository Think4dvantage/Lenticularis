"""
Tests for the Jungfraubahn (JFB) observation collector.

Pure-logic tests: the API payload is stubbed, so no network and no InfluxDB.
The fixture mirrors the real response shape, including the server-side
double-encoded station names.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lenticularis.collectors.jfb import (
    KNOTS_TO_KMH,
    JfbCollector,
    _reconstruct_timestamp,
    _slug,
)


def _fresh_time() -> str:
    """A ``timeUTC`` the collector will accept as current.

    Must be relative to now, not hardcoded: the collector rejects readings older than
    _MAX_AGE_HOURS, so a fixed clock time would make these tests pass or fail depending
    on the hour they are run at.
    """
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%H:%M")


def _obs(value: str, time_utc: str) -> dict:
    return {"value": value, "timeUTC": time_utc}


def _payload(time_utc: str | None = None) -> dict:
    """A trimmed but structurally faithful observations/current response."""
    time_utc = time_utc or _fresh_time()
    return {
        "meta": {"observationParameters": []},
        "observationsByStation": [
            {
                # Wind station — the reason this collector exists.
                "station": {"name": "Lauberhorn", "lat": 46.585, "lon": 7.95, "elevation": 2315},
                "observations": {
                    "DIR": _obs("230", time_utc),
                    "FF": _obs("14", time_utc),
                    "G10": _obs("22", time_utc),
                    "G1h": _obs("31", time_utc),
                    "RH": _obs("68", time_utc),
                    "TL": _obs("4.2", time_utc),
                    "TD": _obs("-1.4", time_utc),
                    "DIFFTD": _obs("5.6", time_utc),
                },
            },
            {
                # Wind-only station: no temperature/humidity/pressure at all.
                "station": {"name": "Wengen-Dorf", "lat": 46.605, "lon": 7.92, "elevation": 1278},
                "observations": {
                    "DIR": _obs("360", time_utc),
                    "FF": _obs("5", time_utc),
                    "G10": _obs("9", time_utc),
                    "G1h": _obs("12", time_utc),
                },
            },
            {
                # Umlaut in the name — must survive into WeatherStation.name, while the
                # station_id slug folds to ASCII.
                "station": {"name": "Grütschalp", "lat": 46.595, "lon": 7.89, "elevation": 1469},
                "observations": {
                    "RH": _obs("68", time_utc),
                    "QFE": _obs("851.2", time_utc),
                    "TL": _obs("11.4", time_utc),
                    "TD": _obs("5.6", time_utc),
                    "DIFFTD": _obs("5.8", time_utc),
                },
            },
            {
                # Duplicate of meteoswiss-INT — must never be emitted.
                "station": {"name": "Interlaken", "lat": 46.67, "lon": 7.87, "elevation": 577},
                "observations": {
                    "DIR": _obs("270", time_utc),
                    "FF": _obs("8", time_utc),
                    "TL": _obs("18.1", time_utc),
                },
            },
            {
                # Duplicate of meteoswiss-JUN — must never be emitted.
                "station": {
                    "name": "Jungfraujoch-Sphinx", "lat": 46.545, "lon": 7.99, "elevation": 3580,
                },
                "observations": {
                    "DIR": _obs("180", time_utc),
                    "FF": _obs("14", time_utc),
                    "TL": _obs("-2.5", time_utc),
                },
            },
        ],
    }


@pytest.fixture
def collector(monkeypatch):
    """A JfbCollector whose HTTP layer is replaced by the static fixture payload."""
    c = JfbCollector()
    payload = _payload()

    async def _fake_get(url, params=None):
        return payload

    monkeypatch.setattr(c, "_get", _fake_get)
    return c


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_slug_is_ascii_and_stable():
    assert _slug("Wengen-Lauberhorn (Ziel)") == "wengen-lauberhorn-ziel"
    assert _slug("Grütschalp") == "grutschalp"
    assert _slug("Hollandiahütte SAC") == "hollandiahutte-sac"
    assert _slug("Kleine Scheidegg") == "kleine-scheidegg"


def test_reconstruct_timestamp_uses_todays_date():
    now = datetime(2026, 7, 14, 11, 36, tzinfo=timezone.utc)
    assert _reconstruct_timestamp("11:30", now) == datetime(
        2026, 7, 14, 11, 30, tzinfo=timezone.utc
    )


def test_reconstruct_timestamp_handles_midnight_rollover():
    """An obs at 23:50 fetched at 00:05 belongs to yesterday, not today."""
    now = datetime(2026, 7, 14, 0, 5, tzinfo=timezone.utc)
    assert _reconstruct_timestamp("23:50", now) == datetime(
        2026, 7, 13, 23, 50, tzinfo=timezone.utc
    )


def test_reconstruct_timestamp_rejects_garbage():
    now = datetime(2026, 7, 14, 11, 36, tzinfo=timezone.utc)
    assert _reconstruct_timestamp("", now) is None
    assert _reconstruct_timestamp("not-a-time", now) is None


# ---------------------------------------------------------------------------
# collect() — field mapping
# ---------------------------------------------------------------------------

async def test_knots_are_converted_to_kmh(collector):
    ms = {m.station_id: m for m in await collector.collect()}
    lauberhorn = ms["jfb-lauberhorn"]
    assert lauberhorn.wind_speed == pytest.approx(14 * KNOTS_TO_KMH)   # 25.93 km/h
    assert lauberhorn.wind_gust == pytest.approx(22 * KNOTS_TO_KMH)    # 40.74 km/h


async def test_wind_direction_is_normalised(collector):
    """360° must fold to 0 — the model rejects nothing here, but the map would draw wrong."""
    ms = {m.station_id: m for m in await collector.collect()}
    assert ms["jfb-lauberhorn"].wind_direction == 230
    assert ms["jfb-wengen-dorf"].wind_direction == 0


async def test_atmosphere_fields_map_directly(collector):
    ms = {m.station_id: m for m in await collector.collect()}
    grutschalp = ms["jfb-grutschalp"]
    assert grutschalp.temperature == 11.4
    assert grutschalp.humidity == 68
    assert grutschalp.pressure_qfe == 851.2


async def test_qff_is_never_synthesised(collector):
    """JFB reports QFE only. QFF is not derivable from it — it must stay None."""
    for m in await collector.collect():
        assert m.pressure_qff is None


async def test_unmappable_params_are_dropped(collector):
    """TD, DIFFTD and G1h carry no field on WeatherMeasurement and must not leak into one.

    G1h (31 kt) is the trap: it must not be mistaken for wind_gust, which is the
    10-minute peak (G10, 22 kt) everywhere else in the stack.
    """
    ms = {m.station_id: m for m in await collector.collect()}
    lauberhorn = ms["jfb-lauberhorn"]
    assert lauberhorn.wind_gust == pytest.approx(22 * KNOTS_TO_KMH)
    assert lauberhorn.wind_gust != pytest.approx(31 * KNOTS_TO_KMH)
    # The emitted model has no home for these at all.
    assert not hasattr(lauberhorn, "dew_point")
    assert not hasattr(lauberhorn, "wind_gust_1h")


async def test_wind_only_station_leaves_atmosphere_none(collector):
    ms = {m.station_id: m for m in await collector.collect()}
    wengen = ms["jfb-wengen-dorf"]
    assert wengen.wind_speed == pytest.approx(5 * KNOTS_TO_KMH)
    assert wengen.temperature is None
    assert wengen.humidity is None
    assert wengen.pressure_qfe is None


# ---------------------------------------------------------------------------
# collect() — station selection
# ---------------------------------------------------------------------------

async def test_meteoswiss_duplicates_are_excluded(collector):
    ids = {m.station_id for m in await collector.collect()}
    assert "jfb-interlaken" not in ids
    assert "jfb-jungfraujoch-sphinx" not in ids
    assert ids == {"jfb-lauberhorn", "jfb-wengen-dorf", "jfb-grutschalp"}


async def test_station_metadata_is_cached(collector):
    await collector.collect()
    stations = {s.station_id: s for s in await collector.get_stations()}
    assert stations["jfb-grutschalp"].name == "Grütschalp"
    assert stations["jfb-lauberhorn"].elevation == 2315
    assert stations["jfb-lauberhorn"].network == "jfb"


async def test_stale_stations_are_skipped(monkeypatch):
    """A reading older than 2 h must never feed a traffic-light decision."""
    c = JfbCollector()
    stale = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")

    async def _fake_get(url, params=None):
        return _payload(time_utc=stale)

    monkeypatch.setattr(c, "_get", _fake_get)
    assert await c.collect() == []


async def test_empty_response_yields_no_measurements(monkeypatch):
    c = JfbCollector()

    async def _fake_get(url, params=None):
        return {"meta": {}, "observationsByStation": []}

    monkeypatch.setattr(c, "_get", _fake_get)
    assert await c.collect() == []


async def test_current_datetime_is_always_sent(monkeypatch):
    """Without currentDateTime the API silently returns hours-old data — the single
    most important detail of this integration."""
    c = JfbCollector()
    seen: dict = {}

    async def _fake_get(url, params=None):
        seen["params"] = params
        return _payload()

    monkeypatch.setattr(c, "_get", _fake_get)
    await c.collect()

    sent = seen["params"]["currentDateTime"]
    parsed = datetime.strptime(sent, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 120
