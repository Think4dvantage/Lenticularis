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

import re
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


def _init_record(source, init_date):
    """A row as returned by the _recent_forecast_init_dates lookup."""
    rec = MagicMock()
    rec.values = {"source": source, "init_date": init_date}
    return rec


def test_forecast_snapshot_filters_to_latest_init_date():
    """The pivot must be scoped to the newest run, exactly like query_forecast_replay."""
    client = _make_client()
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"]}
    )

    client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    flux = _flux_arg(client._slow_query_api.query)
    assert 'r.init_date == "2026-08-06T00"' in flux


def test_forecast_snapshot_does_not_use_last():
    """`last()` is what fragmented the result per-run and caused the stale pick."""
    client = _make_client()
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"]}
    )

    client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert "last()" not in _flux_arg(client._slow_query_api.query)


def test_forecast_snapshot_picks_newest_init_date_not_first_row():
    """
    The exact prod failure, reproduced: the stale run (171 deg) arrives first and would
    have won under the old 'most fields' tiebreak. The newest run (194 deg) must win.
    """
    client = _make_client()
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"]}
    )
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
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"]}
    )
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0),
        _record("holfuy-1808", "2026-08-02T18", "swissmeteo", 171.0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert result["holfuy-1808"]["wind_direction"] == 194.0


def test_forecast_snapshot_prefers_swissmeteo_over_open_meteo():
    client = _make_client()
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"], "open-meteo": ["2026-08-06T06"]}
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
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"]}
    )
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
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"]}
    )

    client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)
    snapshot_flux = _flux_arg(client._slow_query_api.query)

    client._slow_query_api.query.reset_mock()
    client.query_forecast_replay(_VT - timedelta(hours=6), _VT + timedelta(hours=6))
    replay_flux = _flux_arg(client._slow_query_api.query)

    clause = 'r.init_date == "2026-08-06T00"'
    assert clause in snapshot_flux
    assert clause in replay_flux


# ---------------------------------------------------------------------------
# Gap-fill across runs (v1.23.2).
#
# lsmfapi legitimately serves null frames at the ICON-CH1/CH2 seam — CH1's 06Z run
# populated only to h+18 while CH2 resumed at h+34, leaving 15 consecutive hours of
# tomorrow blank. The previous run covers exactly those hours, so a value is taken from
# the newest run that actually HAS it. All three readers share one rule so the arrows and
# the decision can never disagree.
# ---------------------------------------------------------------------------

def test_merge_prefers_newest_run_per_field():
    from lenticularis.database.influx import _merge_forecast_candidates

    merged = _merge_forecast_candidates([
        {"source": "swissmeteo", "init_date": "2026-08-06T00",
         "fields": {"wind_direction": 194.0, "wind_gust": 25.2}},
        {"source": "swissmeteo", "init_date": "2026-08-06T06",
         "fields": {"wind_direction": 109.0, "wind_gust": 16.2}},
    ])
    assert merged["wind_direction"] == 109.0
    assert merged["wind_gust"] == 16.2


def test_merge_backfills_a_null_frame_from_the_previous_run():
    """The exact prod gap: 06Z has no value for this hour, 00Z does."""
    from lenticularis.database.influx import _merge_forecast_candidates

    merged = _merge_forecast_candidates([
        {"source": "swissmeteo", "init_date": "2026-08-06T06",
         "fields": {"wind_direction": None, "wind_speed": None, "wind_gust": None}},
        {"source": "swissmeteo", "init_date": "2026-08-06T00",
         "fields": {"wind_direction": 194.0, "wind_speed": 8.8, "wind_gust": 25.2}},
    ])
    assert merged["wind_direction"] == 194.0
    assert merged["wind_gust"] == 25.2
    # The run that actually supplied the data is the one reported.
    assert merged["source"] == "swissmeteo"


def test_merge_fills_only_the_gaps_never_overwrites_a_newer_value():
    from lenticularis.database.influx import _merge_forecast_candidates

    merged = _merge_forecast_candidates([
        {"source": "swissmeteo", "init_date": "2026-08-06T06",
         "fields": {"wind_direction": 109.0, "wind_gust": None}},
        {"source": "swissmeteo", "init_date": "2026-08-06T00",
         "fields": {"wind_direction": 194.0, "wind_gust": 25.2}},
    ])
    assert merged["wind_direction"] == 109.0   # newer run keeps precedence
    assert merged["wind_gust"] == 25.2         # older run fills only the hole


def test_merge_all_null_yields_empty_so_the_hour_is_omitted():
    from lenticularis.database.influx import _merge_forecast_candidates

    assert _merge_forecast_candidates([
        {"source": "swissmeteo", "init_date": "2026-08-06T06",
         "fields": {"wind_direction": None}},
    ]) == {}
    assert _merge_forecast_candidates([]) == {}


def test_merge_preferred_source_outranks_a_newer_other_source():
    from lenticularis.database.influx import _merge_forecast_candidates

    merged = _merge_forecast_candidates([
        {"source": "open-meteo", "init_date": "2026-08-06T12",
         "fields": {"wind_direction": 20.0}},
        {"source": "swissmeteo", "init_date": "2026-08-06T00",
         "fields": {"wind_direction": 194.0}},
    ])
    assert merged["wind_direction"] == 194.0
    assert merged["source"] == "swissmeteo"


def test_merge_falls_back_to_other_source_for_a_field_swissmeteo_lacks():
    from lenticularis.database.influx import _merge_forecast_candidates

    merged = _merge_forecast_candidates([
        {"source": "open-meteo", "init_date": "2026-08-06T12",
         "fields": {"wind_direction": 20.0, "precipitation": 1.4}},
        {"source": "swissmeteo", "init_date": "2026-08-06T00",
         "fields": {"wind_direction": 194.0, "precipitation": None}},
    ])
    assert merged["wind_direction"] == 194.0
    assert merged["precipitation"] == 1.4


def test_snapshot_backfills_the_gap_end_to_end():
    client = _make_client()
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T06", "2026-08-06T00"]}
    )
    # 06Z row carries no wind (the null frame); 00Z has the real values.
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-06T06", "swissmeteo", None),
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT)

    assert result["holfuy-1808"]["wind_direction"] == 194.0


def test_all_three_readers_share_one_init_date_filter():
    """
    Arrows, the decision fallback, and the precomputed horizon must consider the same runs.
    Verified by asserting all three Flux strings carry both recent init_dates.
    """
    client = _make_client()
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T06", "2026-08-06T00"]}
    )

    client.query_forecast_snapshot_for_stations(["s1"], _VT)
    snapshot_flux = _flux_arg(client._slow_query_api.query)

    client._slow_query_api.query.reset_mock()
    client.query_forecast_replay(_VT - timedelta(hours=6), _VT + timedelta(hours=6))
    replay_flux = _flux_arg(client._slow_query_api.query)

    client.query_forecast_for_stations(["s1"], 120)
    horizon_flux = _flux_arg(client._query_api.query)

    for clause in ('r.init_date == "2026-08-06T06"', 'r.init_date == "2026-08-06T00"'):
        assert clause in snapshot_flux
        assert clause in replay_flux
        assert clause in horizon_flux


def test_run_fallback_depth_is_bounded():
    """Scanning every retained run is what made these queries time out before v1.16."""
    from lenticularis.database.influx import FORECAST_RUN_FALLBACK_DEPTH

    client = _make_client()
    many = [f"2026-08-0{d}T{h:02d}" for d in (4, 5, 6) for h in (0, 6, 12, 18)]
    client._query_api.query = MagicMock(return_value=[_table([
        _init_record("swissmeteo", d) for d in many
    ])])

    recent = client._recent_forecast_init_dates()

    assert len(recent["swissmeteo"]) == FORECAST_RUN_FALLBACK_DEPTH
    assert recent["swissmeteo"] == sorted(many, reverse=True)[:FORECAST_RUN_FALLBACK_DEPTH]


def test_forecast_snapshot_naive_valid_time_is_treated_as_utc():
    client = _make_client()
    client._recent_forecast_init_dates = MagicMock(
        return_value={"swissmeteo": ["2026-08-06T00"]}
    )
    client._slow_query_api.query = MagicMock(return_value=[_table([
        _record("holfuy-1808", "2026-08-06T00", "swissmeteo", 194.0),
    ])])

    result = client.query_forecast_snapshot_for_stations(["holfuy-1808"], _VT.replace(tzinfo=None))

    assert result["holfuy-1808"]["wind_direction"] == 194.0


# ---------------------------------------------------------------------------
# specs/010-mcp-server — query_history_range + keep_init_date
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
_T1 = datetime(2026, 10, 2, tzinfo=timezone.utc)


def test_history_range_aggregates_per_field_not_a_blanket_mean():
    client = _make_client()
    client.query_history_range(
        ["a"], _T0, _T1, "1h", ["wind_speed", "wind_gust", "precipitation", "wind_direction", "snow_depth"]
    )
    flux = _flux_arg(client._query_api.query)
    # one stream per aggregate; gusts must be max (a mean would understate peaks)
    assert "fn: max" in flux and "fn: sum" in flux and "fn: last" in flux and "fn: mean" in flux
    streams = re.findall(r"s\d = base.*?(?=\ns\d = base|\n\nunion)", flux, re.S)
    max_stream = [s for s in streams if "fn: max" in s][0]
    assert 'r._field == "wind_gust"' in max_stream and "wind_speed" not in max_stream
    sum_stream = [s for s in streams if "fn: sum" in s][0]
    assert 'r._field == "precipitation"' in sum_stream


def test_history_range_pools_members_per_field_with_or_chain_and_explicit_range():
    client = _make_client()
    client.query_history_range(["a", "b"], _T0, _T1, "30m", ["wind_speed"])
    flux = _flux_arg(client._query_api.query)
    assert "contains(" not in flux
    assert 'r.station_id == "a" or r.station_id == "b"' in flux
    assert 'group(columns: ["_field"])' in flux
    assert "range(start: 2026-10-01T00:00:00Z, stop: 2026-10-02T00:00:00Z)" in flux


def test_history_range_rejects_injection_via_window_and_fields():
    import pytest

    client = _make_client()
    with pytest.raises(ValueError):
        client.query_history_range(["a"], _T0, _T1, '1h) |> drop()', ["wind_speed"])
    client.query_history_range(["a"], _T0, _T1, "1h", ['wind_speed" or true or "', "temperature"])
    flux = _flux_arg(client._query_api.query)
    assert "or true" not in flux and 'r._field == "temperature"' in flux
    # station ids are escaped, not trusted
    client.query_history_range(['x"] |> drop() //'], _T0, _T1, "1h", ["temperature"])
    assert '\\"' in _flux_arg(client._query_api.query)


def test_history_range_returns_empty_on_error_and_no_members():
    client = _make_client()
    assert client.query_history_range([], _T0, _T1, "1h", ["temperature"]) == []
    client._query_api.query.side_effect = RuntimeError("boom")
    assert client.query_history_range(["a"], _T0, _T1, "1h", ["temperature"]) == []


def _fc_record(init_date):
    rec = MagicMock()
    rec.values = {"station_id": "s1", "network": "meteoswiss", "source": "swissmeteo", "model": "icon-ch1",
                  "init_date": init_date, "wind_speed": 10.0}
    rec.get_time = MagicMock(return_value=_VT)
    return rec


def test_forecast_for_stations_init_date_dropped_by_default_kept_on_request():
    client = _make_client()
    client._query_api.query.side_effect = None
    client._query_api.query.return_value = [_table([_init_record("swissmeteo", "2026-10-08T04")])]
    # first call resolves candidate runs, second returns the pivoted rows
    client._query_api.query.side_effect = [
        [_table([_init_record("swissmeteo", "2026-10-08T04")])],
        [_table([_fc_record("2026-10-08T04")])],
        [_table([_init_record("swissmeteo", "2026-10-08T04")])],
        [_table([_fc_record("2026-10-08T04")])],
    ]
    default = client.query_forecast_for_stations(["s1"], 24)
    kept = client.query_forecast_for_stations(["s1"], 24, keep_init_date=True)
    row_default = next(iter(default["s1"].values()))
    row_kept = next(iter(kept["s1"].values()))
    assert "init_date" not in row_default and row_default["wind_speed"] == 10.0
    assert row_kept["init_date"] == "2026-10-08T04"
