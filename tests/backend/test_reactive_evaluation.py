"""
Tests for reactive ruleset evaluation (specs/009-reactive-ruleset-evaluation).

Four concerns, matching the spec's user stories:

* US1 — the station → rule set reverse lookup and the in-flight coalescing guard
* US2 — the precomputed forecast horizon (write shape, measurement routing, Flux)
* US2b — the router reading a precomputed decision, and falling back when there isn't one
* US3 — the removed scheduler job, and notifications surviving the move

Pure-logic parts use SimpleNamespace duck-typing (``06-testing-conventions.md``); the
reverse-lookup tests need a real session because they exercise a SQL query.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from lenticularis.database.influx import (
    MEASUREMENT_DECISIONS,
    MEASUREMENT_DECISIONS_FORECAST,
)
from lenticularis.database.models import RuleCondition, RuleSet, User
from lenticularis.rules.reactive import (
    affected_ruleset_ids,
    expand_station_cluster,
    release,
    try_claim,
)

_VALID_TIME = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def session(db_engine):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _make_ruleset(db, ruleset_id: str, station_ids: list[str], owner_id: str = "owner-1"):
    """Persist a rule set with one condition per station id (none if the list is empty)."""
    if db.get(User, owner_id) is None:
        db.add(User(id=owner_id, email=f"{owner_id}@example.com", display_name="O", role="pilot"))
    db.add(RuleSet(
        id=ruleset_id, owner_id=owner_id, name=ruleset_id,
        lat=46.6, lon=7.9, site_type="launch", combination_logic="worst_wins",
    ))
    for i, station_id in enumerate(station_ids):
        db.add(RuleCondition(
            id=f"{ruleset_id}-c{i}", ruleset_id=ruleset_id, station_id=station_id,
            field="wind_speed", operator="<", value_a=25.0, result_colour="green",
            sort_order=i,
        ))
    db.commit()


def _rs(conditions, ruleset_id="rs-test", site_type="launch"):
    return SimpleNamespace(
        id=ruleset_id, owner_id="owner", site_type=site_type,
        combination_logic="worst_wins", conditions=conditions,
    )


def _cond(station_id, result_colour="green"):
    return SimpleNamespace(
        id=f"{station_id}-c", station_id=station_id, station_b_id=None,
        field="wind_speed", operator="<", value_a=25.0, value_b=None,
        result_colour=result_colour, group_id=None, sort_order=0,
    )


# ---------------------------------------------------------------------------
# US1 — reverse lookup (FR-001, FR-003, FR-004, FR-009)
# ---------------------------------------------------------------------------

def test_reverse_lookup_finds_ruleset_by_direct_station(session):
    _make_ruleset(session, "rs-a", ["meteoswiss-INT"])
    assert affected_ruleset_ids(session, {"meteoswiss-INT"}) == {"rs-a"}


def test_reverse_lookup_ignores_unrelated_stations(session):
    """FR-004: a rule set with no dependency on the changed data is not returned."""
    _make_ruleset(session, "rs-a", ["meteoswiss-INT"])
    _make_ruleset(session, "rs-b", ["holfuy-1234"])
    assert affected_ruleset_ids(session, {"holfuy-1234"}) == {"rs-b"}


def test_reverse_lookup_matches_station_b_id(session):
    """pressure_delta conditions reference a second station — it is a dependency too."""
    _make_ruleset(session, "rs-a", ["s1"])
    cond = session.get(RuleCondition, "rs-a-c0")
    cond.station_b_id = "s2"
    session.commit()
    assert affected_ruleset_ids(session, {"s2"}) == {"rs-a"}


def test_member_station_update_finds_ruleset_referencing_canonical(session):
    """
    FR-003 — the highest-value case.

    A pilot can only pick a canonical station id in the editor, so a rule set references
    the canonical id. When a *physical member* of that virtual cluster is the station that
    reported, the rule set must still be found.
    """
    _make_ruleset(session, "rs-a", ["meteoswiss-INT"])
    virtual_members = {"meteoswiss-INT": ["meteoswiss-INT", "holfuy-999"]}

    assert affected_ruleset_ids(session, {"holfuy-999"}, virtual_members) == {"rs-a"}


def test_canonical_update_finds_ruleset_referencing_a_member(session):
    """
    The reverse direction of FR-003.

    Canonicality is priority-ranked, so adding a higher-priority station near an existing
    one moves the canonical id and leaves older conditions pointing at what is now a member.
    Whole-cluster expansion keeps those rule sets reactive.
    """
    _make_ruleset(session, "rs-a", ["holfuy-999"])
    virtual_members = {"meteoswiss-INT": ["meteoswiss-INT", "holfuy-999"]}

    assert affected_ruleset_ids(session, {"meteoswiss-INT"}, virtual_members) == {"rs-a"}


def test_zero_condition_ruleset_is_never_affected(session):
    """FR-009: no conditions means no dependency row, so it can never be triggered."""
    _make_ruleset(session, "rs-empty", [])
    assert affected_ruleset_ids(session, {"meteoswiss-INT"}) == set()


def test_empty_station_set_returns_no_rulesets(session):
    _make_ruleset(session, "rs-a", ["meteoswiss-INT"])
    assert affected_ruleset_ids(session, set()) == set()


def test_expand_station_cluster_is_identity_without_virtual_members():
    assert expand_station_cluster({"a", "b"}) == {"a", "b"}
    assert expand_station_cluster({"a"}, {}) == {"a"}


def test_expand_station_cluster_pulls_in_whole_cluster():
    virtual_members = {"canon": ["canon", "m1", "m2"]}
    assert expand_station_cluster({"m1"}, virtual_members) == {"canon", "m1", "m2"}


# ---------------------------------------------------------------------------
# US1 — in-flight coalescing (FR-007)
# ---------------------------------------------------------------------------

def test_try_claim_refuses_a_second_concurrent_claim():
    assert try_claim("rs-x") is True
    try:
        assert try_claim("rs-x") is False
    finally:
        release("rs-x")


def test_claim_is_reusable_after_release():
    assert try_claim("rs-y") is True
    release("rs-y")
    assert try_claim("rs-y") is True
    release("rs-y")


def test_claims_are_independent_per_ruleset():
    assert try_claim("rs-p") is True
    assert try_claim("rs-q") is True
    release("rs-p")
    release("rs-q")


def test_release_of_unclaimed_id_is_a_noop():
    release("never-claimed")  # must not raise


# ---------------------------------------------------------------------------
# US2 — forecast horizon write (FR-002a) + the D1 adapter
# ---------------------------------------------------------------------------

class _CapturingInflux:
    """Minimal stand-in capturing the points write_decisions_batch produces."""

    def __init__(self):
        self.points: list = []
        self._cfg = SimpleNamespace(bucket="b", org="o")
        outer = self

        class _WriteApi:
            def write(self, bucket=None, org=None, record=None):
                outer.points.extend(record if isinstance(record, list) else [record])

        self._write_api = _WriteApi()

    def measurements(self) -> list[str]:
        return [p._name for p in self.points]


def _steps_to_batch(steps):
    """The adapter under test, as used by _evaluate_rulesets_forecast_sync."""
    return [(s["valid_time"], s["decision"], s["condition_results"]) for s in steps]


def test_adapter_maps_forecast_step_dicts_to_batch_tuples():
    """
    D1: run_forecast_evaluation returns dicts, write_decisions_batch consumes tuples.
    The order of the tuple is (timestamp_iso, decision, condition_results).
    """
    steps = [
        {"valid_time": "2026-08-06T12:00:00+00:00", "decision": "green", "condition_results": [{"a": 1}]},
        {"valid_time": "2026-08-06T13:00:00+00:00", "decision": "red", "condition_results": []},
    ]
    batch = _steps_to_batch(steps)

    assert batch == [
        ("2026-08-06T12:00:00+00:00", "green", [{"a": 1}]),
        ("2026-08-06T13:00:00+00:00", "red", []),
    ]


def test_write_decisions_batch_targets_forecast_measurement_when_asked():
    from lenticularis.rules.evaluator import write_decisions_batch

    influx = _CapturingInflux()
    batch = [("2026-08-06T12:00:00+00:00", "green", [])]

    write_decisions_batch(_rs([]), batch, influx, measurement=MEASUREMENT_DECISIONS_FORECAST)

    assert influx.measurements() == [MEASUREMENT_DECISIONS_FORECAST]


def test_write_decisions_batch_still_defaults_to_observed_measurement():
    """T005 regression guard — the history-backfill caller passes no measurement."""
    from lenticularis.rules.evaluator import write_decisions_batch

    influx = _CapturingInflux()
    write_decisions_batch(_rs([]), [("2026-08-06T12:00:00+00:00", "green", [])], influx)

    assert influx.measurements() == [MEASUREMENT_DECISIONS]
    assert MEASUREMENT_DECISIONS == "rule_decisions"


def test_write_decisions_batch_writes_one_point_per_horizon_step():
    from lenticularis.rules.evaluator import write_decisions_batch

    influx = _CapturingInflux()
    steps = [
        {
            "valid_time": (_VALID_TIME + timedelta(hours=h)).isoformat(),
            "decision": "green",
            "condition_results": [],
        }
        for h in range(24)
    ]
    write_decisions_batch(
        _rs([]), _steps_to_batch(steps), influx, measurement=MEASUREMENT_DECISIONS_FORECAST
    )

    assert len(influx.points) == 24


# ---------------------------------------------------------------------------
# US2 — the read query (D2: explicit stop:, no contains())
# ---------------------------------------------------------------------------

class _FluxCapturingClient:
    """Captures the Flux string instead of running it."""

    def __init__(self):
        self.flux = None

    def query(self, flux, org=None):
        self.flux = flux
        return []


def _influx_for_flux_capture():
    from lenticularis.database.influx import InfluxClient

    client = InfluxClient.__new__(InfluxClient)
    client._cfg = SimpleNamespace(bucket="test-bucket", org="test-org")
    client._query_api = _FluxCapturingClient()
    return client


def test_forecast_decision_query_passes_explicit_stop():
    """
    D2: Flux defaults range() stop to now(), and every point read here is in the future.
    Without an explicit stop the query silently returns nothing.
    """
    influx = _influx_for_flux_capture()
    influx.query_forecast_decisions_for_ruleset(
        "rs-a", _VALID_TIME - timedelta(minutes=30), _VALID_TIME + timedelta(minutes=30)
    )

    flux = influx._query_api.flux
    assert "stop:" in flux
    assert "2026-08-06T12:30:00Z" in flux


def test_forecast_decision_query_uses_equality_not_contains():
    """v1.22.6: contains() against a high-cardinality measurement is catastrophically slow."""
    influx = _influx_for_flux_capture()
    influx.query_forecast_decisions_for_ruleset("rs-a", _VALID_TIME, _VALID_TIME)

    flux = influx._query_api.flux
    assert 'r.ruleset_id == "rs-a"' in flux
    assert "contains(" not in flux
    assert MEASUREMENT_DECISIONS_FORECAST in flux


# ---------------------------------------------------------------------------
# US2b — router reads precomputed, falls back on a miss
# ---------------------------------------------------------------------------

class _PrecomputedInflux:
    def __init__(self, rows):
        self._rows = rows
        self.called_with = None

    def query_forecast_decisions_for_ruleset(self, ruleset_id, start, end):
        self.called_with = (ruleset_id, start, end)
        return self._rows


def _stored_row(valid_time, decision, condition_results):
    return {
        "valid_time": valid_time.isoformat(),
        "decision": decision,
        "condition_results_json": json.dumps(condition_results),
    }


def test_precomputed_decision_is_returned_without_evaluating_live():
    from lenticularis.api.routers.rulesets import _evaluate_at

    influx = _PrecomputedInflux([_stored_row(_VALID_TIME, "orange", [])])
    result = _evaluate_at(_rs([_cond("s1")]), influx, _VALID_TIME, forecast=True, virtual_members={})

    assert result["decision"] == "orange"
    # The live path calls query_forecast_snapshot_for_stations, which this fake does not
    # even define — reaching it would raise AttributeError.
    assert influx.called_with[0] == "rs-test"


def test_precomputed_decision_picks_the_nearest_stored_hour():
    from lenticularis.api.routers.rulesets import _evaluate_at

    influx = _PrecomputedInflux([
        _stored_row(_VALID_TIME - timedelta(minutes=25), "green", []),
        _stored_row(_VALID_TIME + timedelta(minutes=5), "red", []),
    ])
    result = _evaluate_at(_rs([_cond("s1")]), influx, _VALID_TIME, forecast=True, virtual_members={})

    assert result["decision"] == "red"


def test_precomputed_decision_restores_condition_results():
    from lenticularis.api.routers.rulesets import _evaluate_at

    stored = [{"condition_id": "c1", "station_id": "s1", "actual_value": 12.0, "matched": True}]
    influx = _PrecomputedInflux([_stored_row(_VALID_TIME, "green", stored)])
    result = _evaluate_at(_rs([_cond("s1")]), influx, _VALID_TIME, forecast=True, virtual_members={})

    assert result["condition_results"] == stored
    assert result["no_data_stations"] == []


def test_precomputed_decision_derives_no_data_stations():
    """A station whose every condition came back valueless is reported as no-data."""
    from lenticularis.api.routers.rulesets import _evaluate_at

    stored = [
        {"condition_id": "c1", "station_id": "s1", "actual_value": None, "matched": False},
        {"condition_id": "c2", "station_id": "s2", "actual_value": 10.0, "matched": True},
    ]
    influx = _PrecomputedInflux([_stored_row(_VALID_TIME, "red", stored)])
    result = _evaluate_at(_rs([_cond("s1")]), influx, _VALID_TIME, forecast=True, virtual_members={})

    assert result["no_data_stations"] == ["s1"]


def test_cache_miss_falls_back_to_live_forecast_evaluation():
    """
    specs/009 §7.2 — the live path must survive. A rule set created between two forecast
    collector runs has nothing stored and must still resolve.
    """
    from lenticularis.api.routers.rulesets import _evaluate_at

    class _MissThenLive(_PrecomputedInflux):
        def __init__(self):
            super().__init__([])
            self.live_called = False

        def query_forecast_snapshot_for_stations(self, station_ids, valid_time):
            self.live_called = True
            return {"s1": {"wind_speed": 30.0}}

    influx = _MissThenLive()
    result = _evaluate_at(
        _rs([_cond("s1", result_colour="orange")]), influx, _VALID_TIME,
        forecast=True, virtual_members={},
    )

    assert influx.live_called is True
    assert result["decision"] in {"green", "orange", "red"}


def test_both_paths_produce_the_same_response_keys():
    from lenticularis.api.routers.rulesets import _evaluate_at

    class _Live(_PrecomputedInflux):
        def __init__(self):
            super().__init__([])

        def query_forecast_snapshot_for_stations(self, station_ids, valid_time):
            return {"s1": {"wind_speed": 30.0}}

    hit = _evaluate_at(
        _rs([_cond("s1")]), _PrecomputedInflux([_stored_row(_VALID_TIME, "green", [])]),
        _VALID_TIME, forecast=True, virtual_members={},
    )
    miss = _evaluate_at(_rs([_cond("s1")]), _Live(), _VALID_TIME, forecast=True, virtual_members={})

    assert set(hit.keys()) == set(miss.keys())


def test_naive_at_time_is_treated_as_utc():
    """FastAPI may hand the router a naive datetime; the bracket must still be sane."""
    from lenticularis.api.routers.rulesets import _evaluate_at

    influx = _PrecomputedInflux([_stored_row(_VALID_TIME, "green", [])])
    naive = _VALID_TIME.replace(tzinfo=None)
    result = _evaluate_at(_rs([_cond("s1")]), influx, naive, forecast=True, virtual_members={})

    assert result["decision"] == "green"
    _, start, end = influx.called_with
    assert start.tzinfo is not None and end.tzinfo is not None


def test_observed_at_time_mode_never_consults_the_forecast_cache():
    """forecast=False must keep using observed data — the cache is forecast-only."""
    from lenticularis.api.routers.rulesets import _evaluate_at

    class _Observed(_PrecomputedInflux):
        def __init__(self):
            super().__init__([_stored_row(_VALID_TIME, "red", [])])

        def query_observation_snapshot_for_stations(self, station_ids, at_time):
            return {"s1": {"wind_speed": 1.0}}

    influx = _Observed()
    result = _evaluate_at(_rs([_cond("s1")]), influx, _VALID_TIME, forecast=False, virtual_members={})

    assert influx.called_with is None
    assert result["decision"] == "green"


# ---------------------------------------------------------------------------
# US3 — no regression (FR-005, FR-006)
# ---------------------------------------------------------------------------

def test_fixed_interval_ruleset_job_is_gone():
    """FR-005: the 10-minute poll is retired, not merely disabled."""
    import inspect

    from lenticularis import scheduler as scheduler_module

    source = inspect.getsource(scheduler_module)
    assert "collector_ruleset_evaluator" not in source
    assert not hasattr(scheduler_module.CollectorScheduler, "_run_ruleset_evaluator")
    assert hasattr(scheduler_module.CollectorScheduler, "evaluate_rulesets")
    assert hasattr(scheduler_module.CollectorScheduler, "evaluate_rulesets_forecast")


def test_maybe_notify_still_suppresses_an_unchanged_decision():
    """
    FR-006 — the D4 risk.

    _maybe_notify lives on the scheduler and is still reachable from the reactive path.
    An unchanged decision must not re-notify, no matter how often evaluation now runs.
    """
    from lenticularis.scheduler import CollectorScheduler

    sched = CollectorScheduler.__new__(CollectorScheduler)
    sched._cfg = SimpleNamespace(smtp=SimpleNamespace(enabled=True))
    rs = SimpleNamespace(
        id="rs-a", owner_id="o", notify_on="red", last_notified_decision="red",
    )

    # Same decision as last notified → returns before touching SMTP config or the DB.
    sched._maybe_notify(rs, "red", db=None)
    assert rs.last_notified_decision == "red"


def test_maybe_notify_ignores_a_colour_the_user_did_not_subscribe_to():
    from lenticularis.scheduler import CollectorScheduler

    sched = CollectorScheduler.__new__(CollectorScheduler)
    sched._cfg = SimpleNamespace(smtp=SimpleNamespace(enabled=True))
    rs = SimpleNamespace(id="rs-a", owner_id="o", notify_on="green", last_notified_decision=None)

    sched._maybe_notify(rs, "red", db=None)
    assert rs.last_notified_decision is None


def test_observed_and_forecast_decisions_use_separate_measurements():
    """specs/009 §2.5 — a future valid_time decision must never collide with observed history."""
    assert MEASUREMENT_DECISIONS != MEASUREMENT_DECISIONS_FORECAST
    assert MEASUREMENT_DECISIONS_FORECAST == "rule_decisions_forecast"
