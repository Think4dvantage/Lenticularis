"""
Pure-logic tests for the rule evaluator.

Uses SimpleNamespace objects to avoid any database or InfluxDB dependency —
the evaluator's core logic is stateless and accepts duck-typed objects.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lenticularis.rules.evaluator import _evaluate_from_station_data


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_conditions_returns_green():
    decision, results = _evaluate_from_station_data(_rs([]), {})
    assert decision == "green"
    assert results == []


def test_unmatched_condition_returns_green():
    # wind_speed = 10, condition fires only when > 25 → no trigger → green
    cond = _cond("s1", "wind_speed", ">", 25.0, "orange")
    station_data = {"s1": {"wind_speed": 10.0}}
    decision, _ = _evaluate_from_station_data(_rs([cond]), station_data)
    assert decision == "green"


def test_matched_condition_applies_colour():
    cond = _cond("s1", "wind_speed", ">", 25.0, "orange")
    station_data = {"s1": {"wind_speed": 30.0}}
    decision, results = _evaluate_from_station_data(_rs([cond]), station_data)
    assert decision == "orange"
    assert results[0]["matched"] is True
    assert results[0]["actual_value"] == pytest.approx(30.0)


def test_worst_wins_picks_most_restrictive():
    conds = [
        _cond("s1", "wind_speed", ">", 20.0, "orange"),
        _cond("s1", "wind_gust", ">", 25.0, "red"),
    ]
    station_data = {"s1": {"wind_speed": 25.0, "wind_gust": 30.0}}
    decision, _ = _evaluate_from_station_data(_rs(conds, combination_logic="worst_wins"), station_data)
    assert decision == "red"


def test_majority_vote_picks_most_common():
    # 2 green triggers, 1 orange trigger → green wins
    conds = [
        _cond("s1", "wind_speed", "<", 30.0, "green"),
        _cond("s1", "wind_gust", "<", 35.0, "green"),
        _cond("s1", "temperature", ">", 5.0, "orange"),
    ]
    station_data = {"s1": {"wind_speed": 20.0, "wind_gust": 25.0, "temperature": 10.0}}
    decision, _ = _evaluate_from_station_data(_rs(conds, combination_logic="majority_vote"), station_data)
    assert decision == "green"


def test_no_data_for_station_condition_not_matched():
    cond = _cond("s1", "wind_speed", ">", 5.0, "red")
    # s1 not in station_data → condition cannot match → green (benefit of the doubt)
    decision, results = _evaluate_from_station_data(_rs([cond]), {})
    assert decision == "green"
    assert results[0]["matched"] is False
    assert results[0]["actual_value"] is None


def test_opportunity_site_all_triggered_returns_green():
    # opportunity site: ALL conditions must trigger for green
    conds = [
        _cond("s1", "wind_speed", ">", 10.0, "green"),
        _cond("s1", "wind_direction", "between", 180.0, "green", value_b=270.0),
    ]
    station_data = {"s1": {"wind_speed": 15.0, "wind_direction": 220.0}}
    decision, _ = _evaluate_from_station_data(
        _rs(conds, site_type="opportunity", combination_logic="worst_wins"), station_data
    )
    assert decision == "green"


def test_opportunity_site_partial_trigger_returns_red():
    conds = [
        _cond("s1", "wind_speed", ">", 10.0, "green"),   # triggers
        _cond("s1", "wind_gust", "<", 5.0, "green"),     # does NOT trigger (gust=20)
    ]
    station_data = {"s1": {"wind_speed": 15.0, "wind_gust": 20.0}}
    decision, _ = _evaluate_from_station_data(_rs(conds, site_type="opportunity"), station_data)
    assert decision == "red"


def test_and_group_requires_all_members():
    # group "g1": both conditions must match; only first matches → group does not trigger
    conds = [
        _cond("s1", "wind_speed", ">", 10.0, "orange", group_id="g1"),  # matches
        _cond("s1", "wind_gust", "<", 5.0, "orange", group_id="g1"),    # does not match
    ]
    station_data = {"s1": {"wind_speed": 15.0, "wind_gust": 20.0}}
    decision, results = _evaluate_from_station_data(_rs(conds), station_data)
    assert decision == "green"
    for r in results:
        assert r["group_all_matched"] is False


def test_and_group_all_matched_contributes_worst_colour():
    conds = [
        _cond("s1", "wind_speed", ">", 10.0, "orange", group_id="g1"),
        _cond("s1", "wind_gust", ">", 15.0, "red", group_id="g1"),
    ]
    station_data = {"s1": {"wind_speed": 15.0, "wind_gust": 20.0}}
    decision, results = _evaluate_from_station_data(_rs(conds), station_data)
    # Both match → group triggers with worst colour (red)
    assert decision == "red"
    for r in results:
        assert r["group_all_matched"] is True


def test_direction_range_wraps_through_north():
    # Arc 315–45 (NW through N to NE) — should match heading 0 (north)
    cond = _cond("s1", "wind_direction", "in_direction_range", 315.0, "orange", value_b=45.0)
    station_data = {"s1": {"wind_direction": 0.0}}
    decision, _ = _evaluate_from_station_data(_rs([cond]), station_data)
    assert decision == "orange"


def test_pressure_field_maps_to_qff():
    cond = _cond("s1", "pressure", ">", 1000.0, "green")
    station_data = {"s1": {"pressure_qff": 1013.0}}
    decision, _ = _evaluate_from_station_data(_rs([cond]), station_data)
    assert decision == "green"


# ---------------------------------------------------------------------------
# GREEN conditions are requirements (spec 004)
# ---------------------------------------------------------------------------

def test_green_requirement_unmet_returns_red():
    # GREEN confirmation: direction must be in the usable arc. Data present but
    # the threshold fails → requirement unmet → red (not benefit-of-the-doubt green).
    cond = _cond("s1", "wind_direction", "in_direction_range", 270.0, "green", value_b=90.0)
    station_data = {"s1": {"wind_direction": 180.0}}  # S — outside 270→90 arc
    decision, results = _evaluate_from_station_data(_rs([cond]), station_data)
    assert decision == "red"
    assert results[0]["matched"] is False


def test_green_requirement_no_data_returns_red():
    # D3: a GREEN requirement whose station is offline fails safe to red.
    cond = _cond("s1", "wind_speed", "<", 25.0, "green")
    decision, results = _evaluate_from_station_data(_rs([cond]), {})
    assert decision == "red"
    assert results[0]["actual_value"] is None


def test_green_requirement_met_returns_green():
    cond = _cond("s1", "wind_speed", "<", 25.0, "green")
    station_data = {"s1": {"wind_speed": 12.0}}
    decision, _ = _evaluate_from_station_data(_rs([cond]), station_data)
    assert decision == "green"


def test_green_requirement_applies_to_landing():
    cond = _cond("s1", "wind_speed", "<", 25.0, "green")
    station_data = {"s1": {"wind_speed": 40.0}}
    decision, _ = _evaluate_from_station_data(_rs([cond], site_type="landing"), station_data)
    assert decision == "red"


def test_all_green_group_unmet_returns_red():
    # All-green AND group: one member fails → group unmet → red (D2).
    conds = [
        _cond("s1", "wind_speed", "<", 25.0, "green", group_id="g1"),   # matches
        _cond("s1", "wind_gust", "<", 30.0, "green", group_id="g1"),    # fails (gust=45)
    ]
    station_data = {"s1": {"wind_speed": 12.0, "wind_gust": 45.0}}
    decision, _ = _evaluate_from_station_data(_rs(conds), station_data)
    assert decision == "red"


def test_mixed_group_unmet_stays_green():
    # Mixed group (worst = orange, not a green requirement) not fully met → silent → green (D2).
    conds = [
        _cond("s1", "wind_speed", "<", 25.0, "green", group_id="g1"),   # matches
        _cond("s1", "wind_gust", ">", 100.0, "orange", group_id="g1"),  # fails
    ]
    station_data = {"s1": {"wind_speed": 12.0, "wind_gust": 20.0}}
    decision, _ = _evaluate_from_station_data(_rs(conds), station_data)
    assert decision == "green"


def test_exception_only_calm_returns_green():
    # FR-004 regression: a rule set with only RED/ORANGE exceptions, none triggered,
    # still defaults to green (benefit of the doubt survives for exception-only sets).
    conds = [
        _cond("s1", "wind_gust", ">", 50.0, "red"),
        _cond("s1", "wind_speed", ">", 35.0, "orange"),
    ]
    station_data = {"s1": {"wind_speed": 10.0, "wind_gust": 15.0}}
    decision, _ = _evaluate_from_station_data(_rs(conds), station_data)
    assert decision == "green"


def test_opportunity_unmet_green_unaffected_by_requirement_rule():
    # FR-006: opportunity already forces red when not all units trigger; the new
    # launch/landing rule must not double-count or alter that path.
    conds = [
        _cond("s1", "wind_speed", ">", 10.0, "green"),   # triggers
        _cond("s1", "wind_gust", "<", 5.0, "green"),     # fails
    ]
    station_data = {"s1": {"wind_speed": 15.0, "wind_gust": 20.0}}
    decision, _ = _evaluate_from_station_data(
        _rs(conds, site_type="opportunity"), station_data
    )
    assert decision == "red"
