"""
Named condition groups — specs/003-condition-group-names.

Every risk in this feature is silent, so each is pinned here:
  NFR-001  the migration must not move anyone's decisions
  R3       a save that omits `groups` must fail loudly, not wipe names
  R2/FR-011 an empty group is inert — and the evaluator must keep deriving
            groups from conditions, or it reaches _worst([]) and raises
  R5/FR-008 a clone gets its own groups, independent of the source
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from lenticularis.database.db import _backfill_condition_groups
from lenticularis.database.models import ConditionGroup, RuleCondition, RuleSet, User
from lenticularis.rules.evaluator import _evaluate_from_station_data


def _session(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()


def _mk_user(db, uid, role="pilot"):
    u = User(id=uid, email=f"{uid}@x.com", display_name=uid,
             hashed_password=None, role=role, is_active=True)
    db.add(u)
    db.commit()
    return u


def _mk_ruleset(db, owner_id, rs_id, **kw):
    rs = RuleSet(id=rs_id, owner_id=owner_id, name=f"Site {rs_id}",
                 lat=46.0, lon=7.0, combination_logic="worst_wins", **kw)
    db.add(rs)
    db.commit()
    return rs


def _mk_cond(db, rs_id, cid, *, group_id=None, station="st1", threshold=10.0):
    c = RuleCondition(
        id=cid, ruleset_id=rs_id, station_id=station, field="wind_speed",
        operator=">", value_a=threshold, result_colour="red",
        sort_order=0, group_id=group_id,
    )
    db.add(c)
    db.commit()
    return c


# ---------------------------------------------------------------------------
# NFR-001 / R4 — the backfill must not move decisions
# ---------------------------------------------------------------------------

def test_backfill_creates_one_unnamed_group_per_distinct_group_id(db_engine):
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    _mk_cond(db, "rs1", "c1", group_id="g-A")
    _mk_cond(db, "rs1", "c2", group_id="g-A")     # same group
    _mk_cond(db, "rs1", "c3", group_id="g-B")
    _mk_cond(db, "rs1", "c4", group_id=None)      # standalone — no group
    db.close()

    with db_engine.connect() as conn:
        _backfill_condition_groups(conn)

    db = _session(db_engine)
    groups = db.query(ConditionGroup).all()
    assert {g.id for g in groups} == {"g-A", "g-B"}
    assert all(g.name is None for g in groups), "backfilled groups are unnamed, never invented"
    assert all(g.ruleset_id == "rs1" for g in groups)
    db.close()


def test_backfill_is_idempotent(db_engine):
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    _mk_cond(db, "rs1", "c1", group_id="g-A")
    db.close()

    with db_engine.connect() as conn:
        _backfill_condition_groups(conn)
        _backfill_condition_groups(conn)

    db = _session(db_engine)
    assert db.query(ConditionGroup).count() == 1
    db.close()


def test_backfill_reuses_group_id_so_conditions_are_untouched(db_engine):
    """
    The id is reused as the row's primary key precisely so no rule_conditions row
    changes — that is what makes decisions provably identical across the migration.
    """
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    _mk_cond(db, "rs1", "c1", group_id="g-A")
    db.close()

    with db_engine.connect() as conn:
        before = conn.execute(text("SELECT id, group_id FROM rule_conditions")).fetchall()
        _backfill_condition_groups(conn)
        after = conn.execute(text("SELECT id, group_id FROM rule_conditions")).fetchall()

    assert before == after, "no condition row may be rewritten by the migration"


def test_decisions_identical_across_migration(db_engine):
    """The whole point of NFR-001: evaluate, migrate, evaluate again, compare."""
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    _mk_cond(db, "rs1", "c1", group_id="g-A", threshold=10.0)
    _mk_cond(db, "rs1", "c2", group_id="g-A", threshold=99.0)   # group won't all-match
    _mk_cond(db, "rs1", "c3", group_id=None, threshold=10.0)    # standalone triggers
    db.close()

    station_data = {"st1": {"wind_speed": 50.0}}

    db = _session(db_engine)
    rs = db.get(RuleSet, "rs1")
    before, _ = _evaluate_from_station_data(rs, station_data)
    db.close()

    with db_engine.connect() as conn:
        _backfill_condition_groups(conn)

    db = _session(db_engine)
    rs = db.get(RuleSet, "rs1")
    after, _ = _evaluate_from_station_data(rs, station_data)
    db.close()

    assert before == after == "red"


def test_deleting_ruleset_removes_its_groups(db_engine):
    """SQLite enforces no FKs here — the ORM cascade is what actually cleans up."""
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    db.add(ConditionGroup(id="g-A", ruleset_id="rs1", name="Föhn", sort_order=0))
    db.commit()

    rs = db.get(RuleSet, "rs1")
    db.delete(rs)
    db.commit()

    assert db.query(ConditionGroup).count() == 0
    db.close()


# ---------------------------------------------------------------------------
# R2 / FR-011 / FR-012 — empty and single-condition groups
# ---------------------------------------------------------------------------

def test_empty_group_is_inert(db_engine):
    """
    A group with no conditions must change nothing. This holds because the
    evaluator buckets by group_id off the conditions, so an empty group never
    becomes a bucket. If someone refactors it to iterate group rows instead, this
    fails — which is the point: that version reaches _worst([]) and raises.
    """
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    _mk_cond(db, "rs1", "c1", group_id=None, threshold=10.0)
    db.close()

    station_data = {"st1": {"wind_speed": 50.0}}

    db = _session(db_engine)
    rs = db.get(RuleSet, "rs1")
    without, _ = _evaluate_from_station_data(rs, station_data)
    db.close()

    db = _session(db_engine)
    db.add(ConditionGroup(id="g-empty", ruleset_id="rs1", name="Not filled in yet", sort_order=0))
    db.commit()
    rs = db.get(RuleSet, "rs1")
    assert len(rs.condition_groups) == 1, "the empty group really is attached"
    with_empty, _ = _evaluate_from_station_data(rs, station_data)
    db.close()

    assert with_empty == without, "an empty group must not affect the decision"


def test_empty_group_does_not_make_opportunity_red(db_engine):
    """
    total_units = len(standalone) + len(groups); an opportunity site needs every
    unit to trigger. An empty group must not inflate that count.
    """
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1", site_type="opportunity")
    _mk_cond(db, "rs1", "c1", group_id=None, threshold=10.0)
    db.add(ConditionGroup(id="g-empty", ruleset_id="rs1", name=None, sort_order=0))
    db.commit()
    rs = db.get(RuleSet, "rs1")
    decision, _ = _evaluate_from_station_data(rs, {"st1": {"wind_speed": 50.0}})
    db.close()

    assert decision == "red", "the one real condition triggers → opportunity is green-gated"


def test_single_condition_group_matches_standalone(db_engine):
    """FR-012: grouping one condition changes presentation, never the outcome."""
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "solo")
    _mk_cond(db, "solo", "c1", group_id=None, threshold=10.0)
    _mk_ruleset(db, "u1", "grouped")
    _mk_cond(db, "grouped", "c2", group_id="g-1", threshold=10.0)
    db.add(ConditionGroup(id="g-1", ruleset_id="grouped", name="Wind", sort_order=0))
    db.commit()

    station_data = {"st1": {"wind_speed": 50.0}}
    solo, _ = _evaluate_from_station_data(db.get(RuleSet, "solo"), station_data)
    grouped, _ = _evaluate_from_station_data(db.get(RuleSet, "grouped"), station_data)
    db.close()

    assert solo == grouped == "red"


def test_group_name_surfaces_in_results_and_never_for_standalone(db_engine):
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    _mk_cond(db, "rs1", "c1", group_id="g-1", threshold=10.0)
    _mk_cond(db, "rs1", "c2", group_id=None, threshold=10.0)
    db.add(ConditionGroup(id="g-1", ruleset_id="rs1", name="Föhn risk", sort_order=0))
    db.commit()

    _decision, results = _evaluate_from_station_data(db.get(RuleSet, "rs1"), {"st1": {"wind_speed": 50.0}})
    db.close()

    by_id = {r["condition_id"]: r for r in results}
    assert by_id["c1"]["group_name"] == "Föhn risk"
    assert by_id["c2"]["group_name"] is None, "standalone conditions have no group name"


def test_unnamed_group_yields_none_not_empty_string(db_engine):
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    _mk_cond(db, "rs1", "c1", group_id="g-1", threshold=10.0)
    db.add(ConditionGroup(id="g-1", ruleset_id="rs1", name=None, sort_order=0))
    db.commit()
    _d, results = _evaluate_from_station_data(db.get(RuleSet, "rs1"), {"st1": {"wind_speed": 50.0}})
    db.close()
    assert results[0]["group_name"] is None


# ---------------------------------------------------------------------------
# R3 — fail closed rather than wipe names
# ---------------------------------------------------------------------------

def test_conditions_replace_request_rejects_dangling_group_id():
    from lenticularis.models.rules import ConditionsReplaceRequest

    with pytest.raises(Exception) as exc:
        ConditionsReplaceRequest(
            conditions=[{
                "station_id": "st1", "field": "wind_speed", "operator": ">",
                "value_a": 10.0, "result_colour": "red", "group_id": "g-missing",
            }],
            groups=[],
        )
    assert "g-missing" in str(exc.value)


def test_conditions_replace_request_rejects_duplicate_group_ids():
    from lenticularis.models.rules import ConditionsReplaceRequest

    with pytest.raises(Exception) as exc:
        ConditionsReplaceRequest(
            conditions=[],
            groups=[{"id": "g-1"}, {"id": "g-1"}],
        )
    assert "duplicate" in str(exc.value).lower()


def test_conditions_replace_request_accepts_matching_groups():
    from lenticularis.models.rules import ConditionsReplaceRequest

    body = ConditionsReplaceRequest(
        conditions=[{
            "station_id": "st1", "field": "wind_speed", "operator": ">",
            "value_a": 10.0, "result_colour": "red", "group_id": "g-1",
        }],
        groups=[{"id": "g-1", "name": "Wind", "sort_order": 0}],
    )
    assert body.groups[0].name == "Wind"


def test_conditions_replace_request_allows_empty_group():
    """A group with no conditions is valid — that is the point of first-class groups."""
    from lenticularis.models.rules import ConditionsReplaceRequest

    body = ConditionsReplaceRequest(
        conditions=[],
        groups=[{"id": "g-empty", "name": "Planned"}],
    )
    assert len(body.groups) == 1


# ---------------------------------------------------------------------------
# API round-trip — groups persist, and a dangling reference is refused
# ---------------------------------------------------------------------------

def _cond_payload(group_id=None, threshold=10.0):
    return {
        "station_id": "st1", "field": "wind_speed", "operator": ">",
        "value_a": threshold, "result_colour": "red", "group_id": group_id,
    }


async def test_groups_round_trip_through_save_and_reload(client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    db.close()

    r = await client.put(
        "/api/rulesets/rs1/conditions",
        json={
            "conditions": [_cond_payload("g-1"), _cond_payload("g-1")],
            "groups": [{"id": "g-1", "name": "Föhn risk", "sort_order": 0}],
        },
        headers=make_token("u1", "pilot"),
    )
    assert r.status_code == 200

    r = await client.get("/api/rulesets/rs1", headers=make_token("u1", "pilot"))
    detail = r.json()
    assert [g["name"] for g in detail["condition_groups"]] == ["Föhn risk"]


async def test_saving_twice_keeps_the_same_group_id(client, db_engine, make_token):
    """
    The editor sends the same client-minted group id back on every save. Without
    flushing deletes before inserts, the re-insert collides with the row on disk.
    """
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    db.close()

    body = {
        "conditions": [_cond_payload("g-1")],
        "groups": [{"id": "g-1", "name": "Wind", "sort_order": 0}],
    }
    r1 = await client.put("/api/rulesets/rs1/conditions", json=body, headers=make_token("u1", "pilot"))
    assert r1.status_code == 200
    body["groups"][0]["name"] = "Wind (renamed)"
    r2 = await client.put("/api/rulesets/rs1/conditions", json=body, headers=make_token("u1", "pilot"))
    assert r2.status_code == 200, "re-saving the same group id must not trip the primary key"

    r = await client.get("/api/rulesets/rs1", headers=make_token("u1", "pilot"))
    assert [g["name"] for g in r.json()["condition_groups"]] == ["Wind (renamed)"]


async def test_dangling_group_id_is_refused_and_nothing_is_written(client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    db.close()

    # Establish a good state first.
    await client.put(
        "/api/rulesets/rs1/conditions",
        json={"conditions": [_cond_payload("g-1")],
              "groups": [{"id": "g-1", "name": "Keep me", "sort_order": 0}]},
        headers=make_token("u1", "pilot"),
    )

    r = await client.put(
        "/api/rulesets/rs1/conditions",
        json={"conditions": [_cond_payload("g-nope")], "groups": []},
        headers=make_token("u1", "pilot"),
    )
    assert r.status_code == 422

    r = await client.get("/api/rulesets/rs1", headers=make_token("u1", "pilot"))
    assert [g["name"] for g in r.json()["condition_groups"]] == ["Keep me"], (
        "a rejected save must leave the existing groups untouched"
    )


async def test_empty_group_survives_a_save(client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "u1")
    _mk_ruleset(db, "u1", "rs1")
    db.close()

    r = await client.put(
        "/api/rulesets/rs1/conditions",
        json={"conditions": [], "groups": [{"id": "g-empty", "name": "Planned", "sort_order": 0}]},
        headers=make_token("u1", "pilot"),
    )
    assert r.status_code == 200

    r = await client.get("/api/rulesets/rs1", headers=make_token("u1", "pilot"))
    assert [g["name"] for g in r.json()["condition_groups"]] == ["Planned"]


# ---------------------------------------------------------------------------
# R5 / FR-008 — a clone gets its own groups
# ---------------------------------------------------------------------------

async def test_clone_gets_independent_groups(client, db_engine, make_token):
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_user(db, "cloner")
    _mk_ruleset(db, "owner", "src", is_public=True)
    _mk_cond(db, "src", "c1", group_id="g-1")
    db.add(ConditionGroup(id="g-1", ruleset_id="src", name="Original name", sort_order=0))
    db.commit()
    db.close()

    r = await client.post("/api/rulesets/src/clone", headers=make_token("cloner", "pilot"))
    assert r.status_code == 201
    clone_id = r.json()["id"]

    db = _session(db_engine)
    clone_groups = db.query(ConditionGroup).filter_by(ruleset_id=clone_id).all()
    assert len(clone_groups) == 1
    assert clone_groups[0].name == "Original name", "names travel with the copy"
    assert clone_groups[0].id != "g-1", "the clone must NOT share the source's group row"

    clone_conds = db.query(RuleCondition).filter_by(ruleset_id=clone_id).all()
    assert clone_conds[0].group_id == clone_groups[0].id, "conditions point at the clone's own group"
    db.close()


async def test_renaming_source_group_does_not_rename_the_clone(client, db_engine, make_token):
    """The exact failure that copying group_id verbatim would have caused."""
    db = _session(db_engine)
    _mk_user(db, "owner")
    _mk_user(db, "cloner")
    _mk_ruleset(db, "owner", "src", is_public=True)
    _mk_cond(db, "src", "c1", group_id="g-1")
    db.add(ConditionGroup(id="g-1", ruleset_id="src", name="Before", sort_order=0))
    db.commit()
    db.close()

    r = await client.post("/api/rulesets/src/clone", headers=make_token("cloner", "pilot"))
    clone_id = r.json()["id"]

    db = _session(db_engine)
    src_group = db.get(ConditionGroup, "g-1")
    src_group.name = "After"
    db.commit()

    clone_group = db.query(ConditionGroup).filter_by(ruleset_id=clone_id).one()
    assert clone_group.name == "Before", "the clone's name must be independent of the source"
    db.close()
