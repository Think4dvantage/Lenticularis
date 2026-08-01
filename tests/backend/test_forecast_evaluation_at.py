"""
Tests for run_forecast_evaluation_at (specs/007-replay-aware-ruleset-decisions).

Uses SimpleNamespace duck-typing for the rule set/conditions (mirrors
test_rules_evaluator.py) and a minimal fake InfluxClient exposing only
query_forecast_snapshot_for_stations, since that is the only Influx method
this function calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from lenticularis.rules.evaluator import _evaluate_from_station_data, run_forecast_evaluation_at


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rs(conditions, site_type="launch", combination_logic="worst_wins"):
    return SimpleNamespace(
        id="rs-test",
        owner_id="owner",
        site_type=site_type,
        combination_logic=combination_logic,
        conditions=conditions,
    )


def _cond(
    station_id,
    field,
    operator,
    value_a,
    result_colour,
    *,
    value_b=None,
    group_id=None,
    station_b_id=None,
):
    return SimpleNamespace(
        id=f"{station_id}-{field}",
        station_id=station_id,
        station_b_id=station_b_id,
        field=field,
        operator=operator,
        value_a=value_a,
        value_b=value_b,
        result_colour=result_colour,
        group_id=group_id,
        sort_order=0,
    )


class _FakeForecastInflux:
    """Returns a fixed snapshot regardless of the requested station_ids/valid_time."""

    def __init__(self, snapshot: dict):
        self._snapshot = snapshot
        self.last_call = None

    def query_forecast_snapshot_for_stations(self, station_ids, valid_time):
        self.last_call = (list(station_ids), valid_time)
        return self._snapshot


_VALID_TIME = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_matches_direct_evaluate_from_station_data():
    cond = _cond("s1", "wind_speed", ">", 25.0, "orange")
    station_data = {"s1": {"wind_speed": 30.0}}
    influx = _FakeForecastInflux(station_data)

    result = run_forecast_evaluation_at(_rs([cond]), influx, _VALID_TIME)
    expected_decision, expected_results = _evaluate_from_station_data(_rs([cond]), station_data)

    assert result["decision"] == expected_decision == "orange"
    assert result["condition_results"] == expected_results
    assert result["no_data_stations"] == []


def test_evaluated_at_echoes_valid_time():
    influx = _FakeForecastInflux({})
    result = run_forecast_evaluation_at(_rs([]), influx, _VALID_TIME)
    assert result["evaluated_at"] == _VALID_TIME.isoformat()


def test_missing_station_forecast_is_recorded_as_no_data():
    cond = _cond("s1", "wind_speed", ">", 25.0, "orange")
    influx = _FakeForecastInflux({})  # snapshot has nothing for s1

    result = run_forecast_evaluation_at(_rs([cond]), influx, _VALID_TIME)

    assert result["no_data_stations"] == ["s1"]
    assert result["decision"] == "green"  # exception-style orange condition, benefit of the doubt


def test_unmet_green_requirement_with_no_forecast_data_fails_safe_to_red():
    # GREEN is a requirement for launch/landing sites (v1.20.0) — an unconfirmable
    # requirement (no data) fails safe to red, same as run_evaluation_at.
    cond = _cond("s1", "wind_speed", "<", 25.0, "green")
    influx = _FakeForecastInflux({})

    result = run_forecast_evaluation_at(_rs([cond], site_type="launch"), influx, _VALID_TIME)

    assert result["no_data_stations"] == ["s1"]
    assert result["decision"] == "red"


def test_opportunity_site_unmet_unit_stays_red():
    cond = _cond("s1", "wind_speed", ">", 5.0, "green")
    station_data = {"s1": {"wind_speed": 1.0}}  # does not trigger
    influx = _FakeForecastInflux(station_data)

    result = run_forecast_evaluation_at(_rs([cond], site_type="opportunity"), influx, _VALID_TIME)

    assert result["decision"] == "red"


def test_queries_influx_once_with_all_station_ids():
    conds = [
        _cond("s1", "wind_speed", ">", 5.0, "orange"),
        _cond("s2", "wind_gust", ">", 5.0, "orange", station_b_id=None),
    ]
    influx = _FakeForecastInflux({"s1": {"wind_speed": 1.0}, "s2": {"wind_gust": 1.0}})

    run_forecast_evaluation_at(_rs(conds), influx, _VALID_TIME)

    assert influx.last_call is not None
    called_ids, called_time = influx.last_call
    assert set(called_ids) == {"s1", "s2"}
    assert called_time == _VALID_TIME
