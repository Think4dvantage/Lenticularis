"""
Public rule set map — specs/002-public-rulesets.

Covers the decisions that fail silently if they regress:
  D2  a visitor never learns who owns a rule set
  D4  curation alone is not enough — the owner must have published it
  D1  500 m proximity suppression for signed-in viewers
  FR-010  a rule set with no data is omitted, never shown green
  NFR-002 cost does not scale with the number of rule sets
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from lenticularis.database.models import RuleCondition, RuleSet, User
from lenticularis.services.public_map import invalidate_public_map_cache


@pytest.fixture(autouse=True)
def _clear_public_map_cache():
    """The cache is module-level and would otherwise leak between tests."""
    invalidate_public_map_cache()
    yield
    invalidate_public_map_cache()


class _Influx:
    """Influx stub with controllable per-station data and a call counter."""

    def __init__(self, data: dict[str, dict] | None = None):
        self._data = data or {}
        self.batch_calls = 0

    def query_latest_for_stations(self, station_ids):
        self.batch_calls += 1
        return {sid: self._data[sid] for sid in station_ids if sid in self._data}

    def query_latest_virtual(self, member_ids):
        return None


def _session(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()


def _mk_user(db, uid, role="pilot"):
    u = User(id=uid, email=f"{uid}@x.com", display_name=f"Name {uid}",
             hashed_password=None, role=role, is_active=True)
    db.add(u)
    db.commit()
    return u


def _mk_ruleset(db, owner_id, rs_id, *, lat=46.0, lon=7.0, is_public=False,
                is_showcase=False, site_type="launch", station="st1", threshold=100.0):
    rs = RuleSet(
        id=rs_id, owner_id=owner_id, name=f"Site {rs_id}", lat=lat, lon=lon,
        site_type=site_type, combination_logic="worst_wins",
        is_public=is_public, is_showcase=is_showcase,
    )
    db.add(rs)
    db.add(RuleCondition(
        id=f"{rs_id}-c1", ruleset_id=rs_id, station_id=station,
        field="wind_speed", operator=">", value_a=threshold,
        result_colour="red", sort_order=0,
    ))
    db.commit()
    return rs


# ---------------------------------------------------------------------------
# D4 — curation requires the owner's consent
# ---------------------------------------------------------------------------

async def test_anonymous_map_requires_showcase_and_public(test_app, client, db_engine):
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "both", is_public=True, is_showcase=True)
    _mk_ruleset(db, "owner", "curated-only", is_public=False, is_showcase=True)
    _mk_ruleset(db, "owner", "published-only", is_public=True, is_showcase=False)
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r = await client.get("/api/public/rulesets/map")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert ids == {"both"}, "only showcase AND public may appear anonymously"


async def test_curated_but_unpublished_never_appears(test_app, client, db_engine):
    """The owner un-published; curation survives but visibility must not."""
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", is_public=False, is_showcase=True)
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r = await client.get("/api/public/rulesets/map")
    assert r.json()["data"] == []


async def test_set_showcase_on_unpublished_returns_409(client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "admin1", role="admin")
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", is_public=False)
    db.close()

    r = await client.put(
        "/api/rulesets/rs1/set_showcase?is_showcase=true",
        headers=make_token("admin1", "admin"),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


async def test_set_showcase_on_published_succeeds(client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "admin1", role="admin")
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", is_public=True)
    db.close()

    r = await client.put(
        "/api/rulesets/rs1/set_showcase?is_showcase=true",
        headers=make_token("admin1", "admin"),
    )
    assert r.status_code == 200
    assert r.json()["is_showcase"] is True


async def test_uncurating_unpublished_is_always_allowed(client, db_engine, make_token):
    """Withdrawing curation must never be blocked, whatever the publish state."""
    db = _session(db_engine)
    _mk_user(db, "admin1", role="admin")
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", is_public=False, is_showcase=True)
    db.close()

    r = await client.put(
        "/api/rulesets/rs1/set_showcase?is_showcase=false",
        headers=make_token("admin1", "admin"),
    )
    assert r.status_code == 200
    assert r.json()["is_showcase"] is False


async def test_set_showcase_requires_admin(client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "pilot1", role="pilot")
    _mk_ruleset(db, "pilot1", "rs1", is_public=True)
    db.close()

    r = await client.put(
        "/api/rulesets/rs1/set_showcase?is_showcase=true",
        headers=make_token("pilot1", "pilot"),
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# FR-010 — no data means omitted, not green
# ---------------------------------------------------------------------------

async def test_no_data_ruleset_is_omitted_not_green(test_app, client, db_engine):
    """
    The evaluator returns green when nothing triggers, including when there is no
    data ("unknown = benefit of the doubt").  Defensible for a pilot who can see
    no_data_stations; a lie to a visitor.  It must be omitted entirely.
    """
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "has-data", is_public=True, is_showcase=True, station="st1")
    _mk_ruleset(db, "owner", "no-data", is_public=True, is_showcase=True, station="st-missing")
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r = await client.get("/api/public/rulesets/map")
    data = r.json()["data"]
    ids = {m["id"] for m in data}
    assert ids == {"has-data"}
    assert "no-data" not in ids, "a rule set with no data must not be shown at all"
    assert all(m["decision"] == "green" for m in data)


async def test_opportunity_only_shown_when_green(test_app, client, db_engine):
    db = _session(db_engine)
    _mk_user(db, "owner")
    # threshold 1.0 with wind 5.0 → condition matches → red → hidden
    _mk_ruleset(db, "owner", "opp-red", is_public=True, is_showcase=True,
                site_type="opportunity", threshold=1.0)
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r = await client.get("/api/public/rulesets/map")
    assert r.json()["data"] == []


# ---------------------------------------------------------------------------
# D2 / NFR-001 — owner identity never leaves the building
# ---------------------------------------------------------------------------

async def test_anonymous_payload_exposes_no_owner_fields(test_app, client, db_engine):
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", is_public=True, is_showcase=True)
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r = await client.get("/api/public/rulesets/map")
    marker = r.json()["data"][0]
    assert set(marker) == {"id", "name", "lat", "lon", "site_type", "decision"}
    body = r.text
    assert "owner" not in body
    assert "Name owner" not in body


# ---------------------------------------------------------------------------
# NFR-002 — cost must not scale with rule set count
# ---------------------------------------------------------------------------

async def test_one_influx_call_regardless_of_ruleset_count(test_app, client, db_engine):
    db = _session(db_engine)
    _mk_user(db, "owner")
    for i in range(12):
        _mk_ruleset(db, "owner", f"rs{i}", is_public=True, is_showcase=True,
                    station=f"st{i}", lat=46.0 + i, lon=7.0)
    db.close()

    influx = _Influx({f"st{i}": {"wind_speed": 5.0} for i in range(12)})
    test_app.state.influx = influx

    r = await client.get("/api/public/rulesets/map")
    assert len(r.json()["data"]) == 12
    assert influx.batch_calls == 1, (
        f"expected one batched query for 12 rule sets, got {influx.batch_calls} "
        "— this is the unauthenticated N+1 the feature exists to avoid"
    )


async def test_anonymous_map_is_cached(test_app, client, db_engine):
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", is_public=True, is_showcase=True)
    db.close()

    influx = _Influx({"st1": {"wind_speed": 5.0}})
    test_app.state.influx = influx

    await client.get("/api/public/rulesets/map")
    await client.get("/api/public/rulesets/map")
    assert influx.batch_calls == 1, "second visitor must be served from cache"


async def test_empty_build_is_not_cached(test_app, client, db_engine):
    """A transient Influx failure must not blank the map for the whole TTL."""
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_ruleset(db, "owner", "rs1", is_public=True, is_showcase=True)
    db.close()

    influx = _Influx({})           # no data at all → 0 markers
    test_app.state.influx = influx
    r1 = await client.get("/api/public/rulesets/map")
    assert r1.json()["data"] == []

    influx._data = {"st1": {"wind_speed": 5.0}}   # data returns
    r2 = await client.get("/api/public/rulesets/map")
    assert len(r2.json()["data"]) == 1, "empty payload must not have been cached"


# ---------------------------------------------------------------------------
# D1 — 500 m proximity suppression (signed-in only)
# ---------------------------------------------------------------------------

def test_proximity_threshold_boundary():
    """
    The 500 m boundary itself, tested directly — driving it through HTTP would
    depend on the haversine earth-radius constant to three decimal places.
    """
    from types import SimpleNamespace

    from lenticularis.services.public_map import PROXIMITY_SUPPRESS_M, _too_close

    assert PROXIMITY_SUPPRESS_M == 500.0

    rs = SimpleNamespace(lat=46.0, lon=7.0)
    # 1 degree of latitude ≈ 111.3 km, so these straddle 500 m with margin.
    assert _too_close(rs, [(46.0040, 7.0)]) is True    # ≈ 445 m — inside
    assert _too_close(rs, [(46.0050, 7.0)]) is False   # ≈ 557 m — outside
    assert _too_close(rs, []) is False                 # nothing of my own → nothing to suppress


async def test_proximity_suppresses_nearby_and_shows_distant(test_app, client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "me")
    _mk_user(db, "other")
    _mk_ruleset(db, "me", "mine", lat=46.0, lon=7.0)
    _mk_ruleset(db, "other", "near", lat=46.0036, lon=7.0, is_public=True)    # ~400 m
    _mk_ruleset(db, "other", "far", lat=46.0090, lon=7.0, is_public=True)     # ~1000 m
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r = await client.get("/api/rulesets/public-map", headers=make_token("me", "pilot"))
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert "near" not in ids, "a rule set inside 500 m of my own must be suppressed"
    assert "far" in ids


async def test_own_rulesets_never_appear_in_public_map(test_app, client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "me")
    _mk_ruleset(db, "me", "mine", lat=46.0, lon=7.0, is_public=True)
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r = await client.get("/api/rulesets/public-map", headers=make_token("me", "pilot"))
    assert r.json()["data"] == [], "my own rule sets are drawn by the authenticated path"


async def test_viewers_do_not_share_a_cache_entry(test_app, client, db_engine, make_token):
    """Suppression is per-viewer; sharing a cache entry would leak one viewer's view to another."""
    db = _session(db_engine)
    _mk_user(db, "near-user")
    _mk_user(db, "far-user")
    _mk_user(db, "other")
    _mk_ruleset(db, "near-user", "near-own", lat=46.0, lon=7.0)
    _mk_ruleset(db, "far-user", "far-own", lat=48.0, lon=9.0)
    _mk_ruleset(db, "other", "target", lat=46.0, lon=7.0, is_public=True)
    db.close()

    test_app.state.influx = _Influx({"st1": {"wind_speed": 5.0}})

    r_near = await client.get("/api/rulesets/public-map", headers=make_token("near-user", "pilot"))
    r_far = await client.get("/api/rulesets/public-map", headers=make_token("far-user", "pilot"))

    assert {m["id"] for m in r_near.json()["data"]} == set(), "target is co-located with my own"
    assert {m["id"] for m in r_far.json()["data"]} == {"target"}, "far viewer must still see it"


async def test_public_map_requires_auth(client):
    r = await client.get("/api/rulesets/public-map")
    assert r.status_code == 401
