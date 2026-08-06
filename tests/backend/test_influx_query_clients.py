"""
Regression test: query_forecast_snapshot_for_stations must use the slow (60s)
InfluxDB query client, not the default 10s one.

Context: this query is now called from two places under real-world concurrent
load (GET /api/foehn/forecast and rules/evaluator.py's run_forecast_evaluation_at,
specs/007-replay-aware-ruleset-decisions) and was observed timing out on the
default 10s client in production, causing spurious no-data fail-safe results.
query_forecast_replay already uses the slow client for the same reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from lenticularis.config import InfluxDBConfig
from lenticularis.database.influx import InfluxClient


def _make_client() -> InfluxClient:
    cfg = InfluxDBConfig(url="http://localhost:8086", token="test", org="test", bucket="test")
    client = InfluxClient(cfg)
    client._query_api = MagicMock(query=MagicMock(return_value=[]))
    client._slow_query_api = MagicMock(query=MagicMock(return_value=[]))
    return client


def test_forecast_snapshot_uses_slow_query_client():
    """
    The heavy pivot must stay on the slow (60s) client — it timed out on the 10s one
    under concurrent replay load (v1.22.2).

    Since v1.23.1 this method is a two-step, like query_forecast_replay: a cheap
    ``_latest_forecast_init_dates()`` lookup on the *fast* client, then the pivot on the
    slow one. So the fast client is legitimately used — but never for the pivot.
    """
    client = _make_client()
    client.query_forecast_snapshot_for_stations(["s1"], datetime.now(timezone.utc))

    client._slow_query_api.query.assert_called_once()
    assert "pivot(" in _flux_arg(client._slow_query_api.query)

    # The fast client only ever runs the narrow init_date lookup.
    client._query_api.query.assert_called_once()
    fast_flux = _flux_arg(client._query_api.query)
    assert "pivot(" not in fast_flux
    assert 'keep(columns: ["source", "init_date"])' in fast_flux


# ---------------------------------------------------------------------------
# Regression: contains(value:, set:) is 135x slower than an OR-chain of == against
# weather_forecast / weather_forecast_thermal, whose per-hour init_date tag
# fragments them into a huge number of series that contains() cannot use the tag
# index to skip. Measured live: 9.3s (contains()) vs 69ms (OR-chain). Fixed 2026-08-03.
# ---------------------------------------------------------------------------

def _flux_arg(mock_query) -> str:
    args, kwargs = mock_query.call_args
    return args[0] if args else kwargs["query"]


def test_forecast_snapshot_uses_or_chain_not_contains():
    client = _make_client()
    client.query_forecast_snapshot_for_stations(["s1", "s2"], datetime.now(timezone.utc))
    flux = _flux_arg(client._slow_query_api.query)
    assert "contains(" not in flux
    assert 'r.station_id == "s1"' in flux
    assert 'r.station_id == "s2"' in flux


def test_thermal_forecast_snapshot_uses_or_chain_not_contains():
    client = _make_client()
    client.query_thermal_forecast_snapshot_for_stations(["s1", "s2"], datetime.now(timezone.utc))
    flux = _flux_arg(client._slow_query_api.query)
    assert "contains(" not in flux
    assert 'r.station_id == "s1"' in flux
    assert 'r.station_id == "s2"' in flux


def test_foehn_pressure_history_forecast_leg_uses_or_chain_not_contains():
    client = _make_client()
    # center_time in the future forces the forecast leg (weather_forecast) to run.
    future = datetime.now(timezone.utc) + timedelta(hours=6)
    client.query_foehn_pressure_history(["s1", "s2"], hours=4, center_time=future)
    # Both the observed and forecast legs run through _query_api (aggregateWindow
    # queries aren't snapshot lookups, so they stay on the fast client) — inspect
    # every call made, since either leg could run first.
    flux_calls = [c.args[0] if c.args else c.kwargs["query"] for c in client._query_api.query.call_args_list]
    assert flux_calls, "expected at least one query on the forecast leg"
    assert all("contains(" not in flux for flux in flux_calls)
    assert any('r.station_id == "s1"' in flux for flux in flux_calls)


# ---------------------------------------------------------------------------
# Regression (v1.23.1): query_forecast_snapshot_for_stations served a FOUR-DAY-OLD
# model run while the map's wind arrows showed the current one — so a rule set's
# traffic light contradicted the arrow the pilot was looking at.
#
# Cause: init_date is a *tag*, so each model run is its own series. `|> last()`
# returned one record per run (not the newest run), and the Python tiebreak kept
# whichever entry had the most fields — all runs carry the same fields, so a strict
# `>` never replaced the first row seen, and tables arrive in ascending tag order.
# Net effect: the OLDEST retained run won.
# ---------------------------------------------------------------------------

_VT = datetime(2026, 8, 7, 14, 0, tzinfo=timezone.utc)


def _record(station_id, init_date, source, wind_direction, offset_min=0):
    rec = MagicMock()
    rec.values = {
        "station_id": station_id,
        "init_date": init_date,
        "source": source,
        "network": "holfuy",
        "model": "icon-ch",
        "wind_direction": wind_direction,
    }
    rec.get_time = MagicMock(return_value=_VT + timedelta(minutes=offset_min))
    return rec


def _table(records):
    t = MagicMock()
    t.records = records
    return t


def test_forecast_snapshot_filters_to_latest_init_date():
    """The pivot must be scoped to the newest run, exactly like query_forecast_replay."""
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(return_value={"swissmeteo": "2026-08-06T00"})

    client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    flux = _flux_arg(client._slow_query_api.query)
    assert 'r.init_date == "2026-08-06T00"' in flux


def test_forecast_snapshot_does_not_use_last():
    """`last()` is what fragmented the result per-run and caused the stale pick."""
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(return_value={"swissmeteo": "2026-08-06T00"})

    client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert "last()" not in _flux_arg(client._slow_query_api.query)


def test_forecast_snapshot_picks_newest_init_date_not_first_row():
    """
    The exact prod failure, reproduced: the stale run (171 deg) arrives first and would
    have won under the old 'most fields' tiebreak. The newest run (194 deg) must win.
    """
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(return_value={"swissmeteo": "2026-08-06T00"})
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-02T18", "swissmeteo", 171.0),
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert result["holfuy-1808"]["wind_direction"] == 194.0
    assert result["holfuy-1808"]["init_date"] == "2026-08-06T00"


def test_forecast_snapshot_newest_init_date_wins_regardless_of_row_order():
    """Same assertion with the rows reversed — selection must not depend on arrival order."""
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(return_value={"swissmeteo": "2026-08-06T00"})
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0),
        _record("holfuy-1808", "2026-08-02T18", "swissmeteo", 171.0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert result["holfuy-1808"]["wind_direction"] == 194.0


def test_forecast_snapshot_prefers_swissmeteo_over_open_meteo():
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": "2026-08-06T00", "open-meteo": "2026-08-06T06"}
    )
    # open-meteo has the NEWER init_date, but swissmeteo is the preferred source.
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-06T06", "open-meteo", 20.0),
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert result["holfuy-1808"]["wind_direction"] == 194.0
    assert result["holfuy-1808"]["source"] == "swissmeteo"


def test_forecast_snapshot_prefers_reading_closest_to_valid_time():
    """Within one run, the ±30 min window can hold two hourly points."""
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(return_value={"swissmeteo": "2026-08-06T00"})
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 150.0, offset_min=-30),
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0, offset_min=0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert result["holfuy-1808"]["wind_direction"] == 194.0


def test_snapshot_and_replay_select_the_same_init_date():
    """
    The property the pilot actually cares about: the arrow (replay) and the decision
    (snapshot) must never come from different model runs.
    """
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(return_value={"swissmeteo": "2026-08-06T00"})

    client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)
    snapshot_flux = _flux_arg(client._slow_query_api.query)

    client._slow_query_api.query.reset_mock()
    client.query_forecast_replay(_VT - timedelta(hours=6), _VT + timedelta(hours=6))
    replay_flux = _flux_arg(client._slow_query_api.query)

    clause = 'r.init_date == "2026-08-06T00"'
    assert clause in snapshot_flux
    assert clause in replay_flux


def test_forecast_snapshot_naive_valid_time_is_treated_as_utc():
    client = _make_client()
    client._latest_forecast_init_dates = MagicMock(return_value={"swissmeteo": "2026-08-06T00"})
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT.replace(tzinfo=None))

    assert result["holfuy-1808"]["wind_direction"] == 194.0
