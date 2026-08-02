"""
Regression coverage for the 2026-08-02 fix across the THREE decision blocks that
duplicate `_evaluate_from_station_data`'s logic rather than delegating to it:
`run_evaluation`, `run_evaluation_at`, `run_forecast_evaluation`.

`_evaluate_from_station_data` itself is covered in test_rules_evaluator.py.
`run_forecast_evaluation_at` and `run_history_backfill` delegate to
`_evaluate_from_station_data` and need no separate coverage.

Each test uses a minimal fake InfluxClient exposing only the one method the
function under test actually calls (mirrors test_forecast_evaluation_at.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from lenticularis.rules.evaluator import run_evaluation, run_evaluation_at, run_forecast_evaluation


def _rs(conditions, site_type="launch", combination_logic="worst_wins"):
    return SimpleNamespace(
        id="rs-test", owner_id="owner", site_type=site_type,
        combination_logic=combination_logic, conditions=conditions,
    )


def _cond(station_id, field, operator, value_a, result_colour, *, value_b=None, group_id=None, station_b_id=None):
    return SimpleNamespace(
        id=f"{station_id}-{field}-{group_id}", station_id=station_id, station_b_id=station_b_id,
        field=field, operator=operator, value_a=value_a, value_b=value_b,
        result_colour=result_colour, group_id=group_id, sort_order=0,
    )


def _conditions():
    return [
        _cond("s1", "wind_direction", "in_direction_range", 90.0, "green", value_b=180.0, group_id="g1"),
        _cond("s1", "wind_speed", "<", 15.0, "green", group_id="g1"),
        _cond("s1", "wind_direction", "in_direction_range", 202.5, "orange", value_b=90.0, group_id="g2"),
        _cond("s1", "wind_speed", "<", 5.0, "orange", group_id="g2"),
    ]


async def test_run_evaluation_unmet_green_does_not_override_matched_group():
    class FakeInflux:
        def query_latest_for_stations(self, station_ids):
            return {"s1": {"wind_direction": 9.0, "wind_speed": 1.0}}

        def query_latest_virtual(self, members):
            return None

    result = run_evaluation(_rs(_conditions()), FakeInflux())
    assert result["decision"] == "orange"


async def test_run_evaluation_at_unmet_green_does_not_override_matched_group():
    class FakeInflux:
        def query_observation_snapshot_for_stations(self, station_ids, at_time):
            return {"s1": {"wind_direction": 9.0, "wind_speed": 1.0, "timestamp": at_time}}

    result = run_evaluation_at(_rs(_conditions()), FakeInflux(), datetime.now(timezone.utc))
    assert result["decision"] == "orange"


async def test_run_forecast_evaluation_unmet_green_does_not_override_matched_group():
    vt_iso = datetime.now(timezone.utc).isoformat()

    class FakeInflux:
        def query_forecast_for_stations(self, station_ids, horizon_hours):
            return {"s1": {vt_iso: {"wind_direction": 9.0, "wind_speed": 1.0}}}

    steps = run_forecast_evaluation(_rs(_conditions()), FakeInflux(), horizon_hours=1)
    assert len(steps) == 1
    assert steps[0]["decision"] == "orange"
