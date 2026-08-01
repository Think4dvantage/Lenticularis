"""
API tests for GET /api/rulesets/{id}/evaluate — specs/007-replay-aware-ruleset-decisions.

Covers:
  FR-002  at_time (no forecast) — unchanged observed-data path
  FR-003  at_time + forecast=true — new single-timestamp forecast path
  FR-004  linked landing rulesets evaluated in the SAME mode as the primary rule set
          (previously always live — this is the regression test for that fix)
  R3      omitting `forecast` entirely preserves today's at_time-only behaviour
"""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from lenticularis.database.models import LaunchLandingLink, RuleCondition, RuleSet, User


class _CountingInflux:
    """Tracks which query method each evaluation path actually used."""

    def __init__(self, observed: dict | None = None, forecast: dict | None = None):
        self._observed = observed or {}
        self._forecast = forecast or {}
        self.live_calls = 0
        self.observed_calls = 0
        self.forecast_calls = 0

    def query_latest_for_stations(self, station_ids):
        self.live_calls += 1
        return {sid: self._observed[sid] for sid in station_ids if sid in self._observed}

    def query_latest_virtual(self, member_ids):
        return None

    def query_observation_snapshot_for_stations(self, station_ids, at_time):
        self.observed_calls += 1
        return {sid: self._observed[sid] for sid in station_ids if sid in self._observed}

    def query_forecast_snapshot_for_stations(self, station_ids, valid_time):
        self.forecast_calls += 1
        return {sid: self._forecast[sid] for sid in station_ids if sid in self._forecast}

    class _write_api:  # noqa: N801 — swallow InfluxDB writes silently for the live path
        @staticmethod
        def write(**kwargs):
            pass

    class _cfg:
        bucket = "test"
        org = "test"


def _session(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()


def _mk_user(db, uid):
    u = User(id=uid, email=f"{uid}@x.com", display_name=uid,
              hashed_password=None, role="pilot", is_active=True)
    db.add(u)
    db.commit()
    return u


def _mk_ruleset(db, owner_id, rs_id, *, site_type="launch", station="st1", threshold=25.0):
    rs = RuleSet(
        id=rs_id, owner_id=owner_id, name=f"Site {rs_id}", lat=46.0, lon=7.0,
        site_type=site_type, combination_logic="worst_wins",
    )
    db.add(rs)
    db.add(RuleCondition(
        id=f"{rs_id}-c1", ruleset_id=rs_id, station_id=station,
        field="wind_speed", operator=">", value_a=threshold,
        result_colour="orange", sort_order=0,
    ))
    db.commit()
    return rs


_AT_TIME = "2026-08-01T10:00:00Z"


async def test_at_time_without_forecast_uses_observed_path(test_app, client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", station="st1")
    db.close()

    influx = _CountingInflux(observed={"st1": {"wind_speed": 30.0}})
    test_app.state.influx = influx

    r = await client.get(
        f"/api/rulesets/rs1/evaluate?at_time={_AT_TIME}",
        headers=make_token("owner"),
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "orange"
    assert influx.observed_calls == 1
    assert influx.forecast_calls == 0
    assert influx.live_calls == 0


async def test_at_time_with_forecast_uses_forecast_path(test_app, client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", station="st1")
    db.close()

    influx = _CountingInflux(forecast={"st1": {"wind_speed": 30.0}})
    test_app.state.influx = influx

    r = await client.get(
        f"/api/rulesets/rs1/evaluate?at_time={_AT_TIME}&forecast=true",
        headers=make_token("owner"),
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "orange"
    assert influx.forecast_calls == 1
    assert influx.observed_calls == 0
    assert influx.live_calls == 0


async def test_forecast_param_ignored_without_at_time(test_app, client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", station="st1")
    db.close()

    influx = _CountingInflux(observed={"st1": {"wind_speed": 30.0}})
    test_app.state.influx = influx

    r = await client.get("/api/rulesets/rs1/evaluate?forecast=true", headers=make_token("owner"))
    assert r.status_code == 200
    assert influx.live_calls == 1
    assert influx.observed_calls == 0
    assert influx.forecast_calls == 0


async def test_landing_halo_evaluated_in_same_mode_as_launch_site(test_app, client, db_engine, make_token):
    """
    Regression test for the pre-existing defect (FR-004): before this fix, a linked
    landing ruleset was always evaluated live even when the launch site was evaluated
    with at_time — the halo would disagree with the landing site's own marker.
    """
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "launch1", station="st1")
    _mk_ruleset(db, "owner", "landing1", site_type="landing", station="st2")
    db.add(LaunchLandingLink(launch_ruleset_id="launch1", landing_ruleset_id="landing1"))
    db.commit()
    db.close()

    influx = _CountingInflux(
        observed={"st1": {"wind_speed": 30.0}, "st2": {"wind_speed": 30.0}},
    )
    test_app.state.influx = influx

    r = await client.get(
        f"/api/rulesets/launch1/evaluate?at_time={_AT_TIME}",
        headers=make_token("owner"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "orange"
    assert body["landing_decisions"][0]["decision"] == "orange"
    # Both the launch site and its landing halo went through the observed-at_time path —
    # neither touched the live query.
    assert influx.observed_calls == 2
    assert influx.live_calls == 0
