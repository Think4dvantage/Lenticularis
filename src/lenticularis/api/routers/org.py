"""
Organisation dashboard router — /api/org/{slug}

Endpoints
---------
GET /api/org/{slug}/status     — public: current worst-colour status
GET /api/org/{slug}/dashboard  — org members only: status + conditions + 24h history
GET /api/org/{slug}/rulesets   — org admins only: list org rulesets
"""
from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import (
    get_current_user_optional,
    require_org_admin,
    require_org_member,
)
from lenticularis.database.db import get_db
from lenticularis.database.models import Organization, RuleSet, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/org", tags=["org"])

_COLOUR_RANK = {"green": 0, "orange": 1, "red": 2}


def _worst(colours: list[str]) -> str:
    if not colours:
        return "green"
    return max(colours, key=lambda c: _COLOUR_RANK.get(c, 0))


def _get_org_or_404(slug: str, db: Session) -> Organization:
    org = db.execute(
        select(Organization).where(Organization.slug == slug)
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


def _assert_same_org(user: User, org: Organization) -> None:
    if user.role == "admin":
        return  # system admin can access any org
    if user.org_id != org.id:
        raise HTTPException(status_code=403, detail="Access denied — not a member of this organisation")


# ---------------------------------------------------------------------------
# Public status — just the colour, no auth needed
# ---------------------------------------------------------------------------

@router.get("/{slug}/status")
def org_status(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the current worst-colour decision across all org rulesets. Public endpoint."""
    org = _get_org_or_404(slug, db)

    rulesets = db.execute(
        select(RuleSet).where(RuleSet.org_id == org.id)
    ).scalars().all()

    if not rulesets:
        return {"color": "green", "updated_at": datetime.now(timezone.utc).isoformat(), "no_rulesets": True}

    influx = request.app.state.influx
    ruleset_ids = [rs.id for rs in rulesets]

    # Query latest decision for each ruleset from InfluxDB (fast path)
    history = influx.query_decision_history_multi(ruleset_ids, hours=2)
    colours: list[str] = []
    latest_ts: str | None = None

    for rs_id in ruleset_ids:
        rows = history.get(rs_id, [])
        if rows:
            last = rows[-1]
            colours.append(last["decision"] or "green")
            if latest_ts is None or (last["timestamp"] and last["timestamp"] > latest_ts):
                latest_ts = last["timestamp"]

    # Fall back to live evaluation if no cached decisions exist
    if not colours:
        from lenticularis.rules.evaluator import run_evaluation, write_decision
        virtual_members = getattr(request.app.state, "virtual_members", {})
        for rs in rulesets:
            if rs.conditions:
                result = run_evaluation(rs, influx, virtual_members)
                write_decision(rs, result, influx)
                colours.append(result["decision"])
        latest_ts = datetime.now(timezone.utc).isoformat()

    return {
        "color": _worst(colours),
        "updated_at": latest_ts or datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Member dashboard — colour + conditions + 24h history
# ---------------------------------------------------------------------------

@router.get("/{slug}/dashboard")
def org_dashboard(
    slug: str,
    request: Request,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Full dashboard data for authenticated org members."""
    org = _get_org_or_404(slug, db)
    _assert_same_org(current_user, org)

    rulesets = db.execute(
        select(RuleSet).where(RuleSet.org_id == org.id)
    ).scalars().all()

    if not rulesets:
        return {
            "color": "green",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "no_rulesets": True,
            "evaluations": [],
            "history": [],
        }

    influx = request.app.state.influx
    from lenticularis.rules.evaluator import run_evaluation, run_forecast_evaluation, write_decision
    virtual_members = getattr(request.app.state, "virtual_members", {})

    evaluations = []
    colours: list[str] = []

    for rs in rulesets:
        if not rs.conditions:
            eval_result = {"decision": "green", "condition_results": [], "no_data_stations": []}
        else:
            eval_result = run_evaluation(rs, influx, virtual_members)
            write_decision(rs, eval_result, influx)

        colours.append(eval_result["decision"])

        display_registry = getattr(request.app.state, "display_registry", None) or getattr(request.app.state, "station_registry", {})
        condition_results = eval_result.get("condition_results", [])
        for cr in condition_results:
            sid = cr.get("station_id", "")
            station = display_registry.get(sid)
            cr["station_name"] = station.name if station else sid

        evaluations.append({
            "ruleset_id": rs.id,
            "name": rs.name,
            "decision": eval_result["decision"],
            "condition_results": condition_results,
            "no_data_stations": eval_result.get("no_data_stations", []),
        })

    # 24h decision history for the strip
    ruleset_ids = [rs.id for rs in rulesets]
    raw_history = influx.query_decision_history_multi(ruleset_ids, hours=24)

    # Flatten + sort all history points (worst-colour per timestamp across all rulesets)
    history_flat: list[dict] = []
    for rs_id, rows in raw_history.items():
        for row in rows:
            history_flat.append({
                "timestamp": row["timestamp"],
                "decision": row["decision"] or "green",
            })
    history_flat.sort(key=lambda x: x["timestamp"] or "")

    # 24h forecast — worst-colour across all rulesets per valid_time step
    forecast_by_vt: dict[str, str] = {}
    for rs in rulesets:
        if not rs.conditions:
            continue
        try:
            steps = run_forecast_evaluation(rs, influx, horizon_hours=24)
            for step in steps:
                vt = step["valid_time"]
                forecast_by_vt[vt] = _worst([forecast_by_vt.get(vt, "green"), step["decision"]])
        except Exception:
            logger.exception("Forecast evaluation failed for ruleset %s", rs.id)
    forecast_flat = [
        {"valid_time": vt, "decision": col}
        for vt, col in sorted(forecast_by_vt.items())
    ]

    return {
        "color": _worst(colours),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "org_name": org.name,
        "evaluations": evaluations,
        "history": history_flat,
        "forecast": forecast_flat,
    }


# ---------------------------------------------------------------------------
# Org rulesets list — org admin only
# ---------------------------------------------------------------------------

@router.get("/{slug}/rulesets")
def org_rulesets(
    slug: str,
    current_user: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """List all rulesets belonging to this organisation (org_admin only)."""
    org = _get_org_or_404(slug, db)
    _assert_same_org(current_user, org)

    rulesets = db.execute(
        select(RuleSet)
        .where(RuleSet.org_id == org.id)
        .order_by(RuleSet.created_at.desc())
    ).scalars().all()

    return [
        {
            "id": rs.id,
            "name": rs.name,
            "description": rs.description,
            "site_type": rs.site_type,
            "lat": rs.lat,
            "lon": rs.lon,
            "altitude_m": rs.altitude_m,
            "combination_logic": rs.combination_logic,
            "is_public": rs.is_public,
            "condition_count": len(rs.conditions),
            "clone_count": 0,
            "created_at": rs.created_at.isoformat() if rs.created_at else None,
        }
        for rs in rulesets
    ]
