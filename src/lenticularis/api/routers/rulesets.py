"""
Rule sets CRUD router.

Endpoints
---------
GET    /api/rulesets                  — list own rule sets
POST   /api/rulesets                  — create rule set
GET    /api/rulesets/gallery          — public rule sets from other pilots
GET    /api/rulesets/presets          — admin-curated preset templates (all pilots)
GET    /api/rulesets/{id}             — get detail (with conditions)
PUT    /api/rulesets/{id}             — update metadata
DELETE /api/rulesets/{id}             — delete
PUT    /api/rulesets/{id}/conditions  — replace full condition list
PUT    /api/rulesets/{id}/landings    — replace landing links
PUT    /api/rulesets/{id}/webcams     — replace webcam list
PUT    /api/rulesets/{id}/set_preset  — toggle is_preset flag (admin only)
POST   /api/rulesets/{id}/clone       — clone a public rule set
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import get_current_user, require_admin, require_pilot
from lenticularis.database.db import get_db
from lenticularis.database.models import LaunchLandingLink, Organization, RuleCondition, RuleSet, RuleSetWebcam, User
from lenticularis.models.rules import (
    ConditionsReplaceRequest,
    EvaluationResult,
    LandingLinksRequest,
    RuleSetCreate,
    RuleSetDetail,
    RuleSetOut,
    RuleSetUpdate,
    WebcamsReplaceRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rulesets", tags=["rulesets"])


def _get_own_ruleset(ruleset_id: str, current_user: User, db: Session) -> RuleSet:
    rs = db.get(RuleSet, ruleset_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    is_owner = rs.owner_id == current_user.id
    is_org_admin = (
        current_user.role == "org_admin"
        and current_user.org_id is not None
        and rs.org_id == current_user.org_id
    )
    if not (is_owner or is_org_admin):
        raise HTTPException(status_code=403, detail="Not your rule set")
    return rs


# ---------------------------------------------------------------------------
# List own rule sets
# ---------------------------------------------------------------------------

@router.get("", response_model=list[RuleSetOut])
def list_rulesets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(RuleSet)
        .where(RuleSet.owner_id == current_user.id)
        .where(RuleSet.org_id.is_(None))
        .order_by(RuleSet.created_at.desc())
    ).scalars().all()
    return rows


# ---------------------------------------------------------------------------
# Gallery — public rule sets from other pilots
# ---------------------------------------------------------------------------

@router.get("/gallery", response_model=list[RuleSetOut])
def gallery(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(RuleSet)
        .where(RuleSet.is_public == True, RuleSet.owner_id != current_user.id)
        .order_by(RuleSet.clone_count.desc(), RuleSet.created_at.desc())
    ).scalars().all()
    return rows


# ---------------------------------------------------------------------------
# Presets — admin-curated templates shown in the new-ruleset form
# ---------------------------------------------------------------------------

@router.get("/presets", response_model=list[RuleSetDetail])
def list_presets(
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(RuleSet)
        .where(RuleSet.is_preset == True)
        .order_by(RuleSet.name)
    ).scalars().all()
    return rows


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.post("", response_model=RuleSetOut, status_code=status.HTTP_201_CREATED)
def create_ruleset(
    body: RuleSetCreate,
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    # Resolve org_id: explicit org_slug beats the role-based default
    org_id: str | None = None
    if body.org_slug:
        org = db.execute(
            select(Organization).where(Organization.slug == body.org_slug)
        ).scalar_one_or_none()
        if org and (
            current_user.role == "admin"
            or (current_user.role == "org_admin" and current_user.org_id == org.id)
        ):
            org_id = org.id
    elif current_user.role == "org_admin":
        org_id = current_user.org_id

    rs = RuleSet(
        id=str(uuid.uuid4()),
        owner_id=current_user.id,
        org_id=org_id,
        **body.model_dump(exclude={"org_slug"}),
    )
    db.add(rs)
    db.commit()
    db.refresh(rs)
    logger.info("RuleSet created: %s by %s (org_id=%s)", rs.id, current_user.id, org_id)
    return rs


# ---------------------------------------------------------------------------
# Evaluate — compute current GREEN/ORANGE/RED decision from live station data
# ---------------------------------------------------------------------------

@router.get("/{ruleset_id}/evaluate", response_model=EvaluationResult)
def evaluate_ruleset(
    ruleset_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rs = db.get(RuleSet, ruleset_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    if rs.owner_id != current_user.id and not rs.is_public:
        raise HTTPException(status_code=403, detail="Not your rule set")

    if not rs.conditions:
        return EvaluationResult(
            decision="green",
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            condition_results=[],
            no_data_stations=[],
        )

    from lenticularis.rules.evaluator import run_evaluation, write_decision

    influx = request.app.state.influx
    result = run_evaluation(rs, influx)
    write_decision(rs, result, influx)
    logger.info("Evaluated ruleset %s → %s (no-data: %s)", rs.id, result["decision"], result["no_data_stations"])

    # Evaluate linked landing rulesets and attach to result
    if rs.landing_links:
        landing_decisions = []
        for link in rs.landing_links:
            landing_rs = db.get(RuleSet, link.landing_ruleset_id)
            if landing_rs is None:
                continue
            if landing_rs.conditions:
                ld_result = run_evaluation(landing_rs, influx)
            else:
                ld_result = {"decision": "green"}
            landing_decisions.append({
                "ruleset_id": landing_rs.id,
                "name": landing_rs.name,
                "decision": ld_result["decision"],
            })
        result["landing_decisions"] = landing_decisions
        if landing_decisions:
            from lenticularis.rules.evaluator import _worst
            result["best_landing_decision"] = _worst([ld["decision"] for ld in landing_decisions])

    return result


# ---------------------------------------------------------------------------
# Decision history — past evaluation results from InfluxDB
# ---------------------------------------------------------------------------

@router.get("/{ruleset_id}/history")
def get_history(
    ruleset_id: str,
    hours: int = 24,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import json as _json
    rs = db.get(RuleSet, ruleset_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    if rs.owner_id != current_user.id and not rs.is_public:
        raise HTTPException(status_code=403, detail="Not your rule set")

    influx = request.app.state.influx
    raw = influx.query_decision_history(ruleset_id, hours)

    active_window_hours: float | None = None
    _annotate = rs.lat is not None and rs.lon is not None
    if _annotate:
        active_window_hours = 1.0
        from lenticularis.utils.sunrise import is_in_active_window as _in_window

    data = []
    for row in raw:
        try:
            cond_results = _json.loads(row["condition_results_json"]) if row.get("condition_results_json") else []
        except Exception:
            cond_results = []
        entry = {
            "timestamp": row["timestamp"],
            "decision": row["decision"],
            "condition_results": cond_results,
        }
        if _annotate:
            try:
                dt = datetime.fromisoformat(row["timestamp"])
                entry["in_active_window"] = _in_window(dt, rs.lat, rs.lon, active_window_hours)
            except Exception:
                entry["in_active_window"] = True
        else:
            entry["in_active_window"] = True
        data.append(entry)

    return {"data": data, "active_window_hours": active_window_hours}


# ---------------------------------------------------------------------------
# Decision forecast — ephemeral forecast evaluation
# ---------------------------------------------------------------------------

@router.get("/{ruleset_id}/forecast")
def get_forecast(
    ruleset_id: str,
    hours: int = 24,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rs = db.get(RuleSet, ruleset_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    if rs.owner_id != current_user.id and not rs.is_public:
        raise HTTPException(status_code=403, detail="Not your rule set")

    if not rs.conditions:
        return {"steps": [], "active_window_hours": None}

    from lenticularis.rules.evaluator import run_forecast_evaluation

    influx = request.app.state.influx
    active_window_hours: float | None = 1.0 if (rs.lat is not None and rs.lon is not None) else None
    steps = run_forecast_evaluation(
        rs,
        influx,
        horizon_hours=hours,
        lat=rs.lat,
        lon=rs.lon,
        active_window_hours=active_window_hours or 1.0,
    )
    return {"steps": steps, "active_window_hours": active_window_hours}


# ---------------------------------------------------------------------------
# Detail (with conditions)
# ---------------------------------------------------------------------------

@router.get("/{ruleset_id}", response_model=RuleSetDetail)
def get_ruleset(
    ruleset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rs = db.get(RuleSet, ruleset_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    # Allow reading own rule sets and public ones
    if rs.owner_id != current_user.id and not rs.is_public:
        raise HTTPException(status_code=403, detail="Not your rule set")
    return rs


# ---------------------------------------------------------------------------
# Update metadata
# ---------------------------------------------------------------------------

@router.put("/{ruleset_id}", response_model=RuleSetOut)
def update_ruleset(
    ruleset_id: str,
    body: RuleSetUpdate,
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    rs = _get_own_ruleset(ruleset_id, current_user, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rs, field, value)
    rs.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rs)
    return rs


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.delete("/{ruleset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ruleset(
    ruleset_id: str,
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    rs = _get_own_ruleset(ruleset_id, current_user, db)
    db.delete(rs)
    db.commit()


# ---------------------------------------------------------------------------
# Replace conditions (full replace — simpler than row-level CRUD)
# ---------------------------------------------------------------------------

@router.put("/{ruleset_id}/conditions", response_model=RuleSetDetail)
def replace_conditions(
    ruleset_id: str,
    body: ConditionsReplaceRequest,
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    rs = _get_own_ruleset(ruleset_id, current_user, db)

    # Delete all existing conditions
    for c in list(rs.conditions):
        db.delete(c)

    # Insert new ones
    for i, cond in enumerate(body.conditions):
        db.add(RuleCondition(
            id=str(uuid.uuid4()),
            ruleset_id=rs.id,
            sort_order=i,
            **cond.model_dump(exclude={'sort_order'}),
        ))

    rs.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rs)
    return rs


# ---------------------------------------------------------------------------
# Replace landing links for a launch ruleset
# ---------------------------------------------------------------------------

@router.put("/{ruleset_id}/landings", response_model=RuleSetOut)
def replace_landings(
    ruleset_id: str,
    body: LandingLinksRequest,
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    rs = _get_own_ruleset(ruleset_id, current_user, db)

    # Bulk-delete existing links (avoids ORM collection/cascade confusion)
    db.query(LaunchLandingLink).filter(
        LaunchLandingLink.launch_ruleset_id == rs.id
    ).delete(synchronize_session=False)

    # Insert new ones (validate each landing ruleset exists and belongs to the user)
    for landing_id in body.landing_ids:
        landing = db.get(RuleSet, landing_id)
        if landing is None or landing.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail=f"Landing ruleset {landing_id} not found")
        db.add(LaunchLandingLink(
            id=str(uuid.uuid4()),
            launch_ruleset_id=rs.id,
            landing_ruleset_id=landing_id,
        ))

    rs.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rs)
    return rs


# ---------------------------------------------------------------------------
# Replace webcams
# ---------------------------------------------------------------------------

@router.put("/{ruleset_id}/webcams", response_model=RuleSetDetail)
def replace_webcams(
    ruleset_id: str,
    body: WebcamsReplaceRequest,
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    rs = _get_own_ruleset(ruleset_id, current_user, db)

    db.query(RuleSetWebcam).filter(
        RuleSetWebcam.ruleset_id == rs.id
    ).delete(synchronize_session=False)

    for i, wc in enumerate(body.webcams):
        db.add(RuleSetWebcam(
            id=str(uuid.uuid4()),
            ruleset_id=rs.id,
            sort_order=i,
            url=wc.url,
            label=wc.label or None,
        ))

    rs.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rs)
    return rs


# ---------------------------------------------------------------------------
# Toggle is_preset flag (admin only)
# ---------------------------------------------------------------------------

@router.put("/{ruleset_id}/set_preset", response_model=RuleSetOut)
def set_preset(
    ruleset_id: str,
    is_preset: bool,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rs = db.get(RuleSet, ruleset_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    rs.is_preset = is_preset
    rs.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rs)
    logger.info("RuleSet %s is_preset set to %s by admin %s", rs.id, is_preset, current_user.id)
    return rs


# ---------------------------------------------------------------------------
# Clone a public rule set
# ---------------------------------------------------------------------------

@router.post("/{ruleset_id}/clone", response_model=RuleSetOut, status_code=status.HTTP_201_CREATED)
def clone_ruleset(
    ruleset_id: str,
    current_user: User = Depends(require_pilot),
    db: Session = Depends(get_db),
):
    source = db.get(RuleSet, ruleset_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    if not source.is_public and source.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Rule set is not public")

    new_id = str(uuid.uuid4())
    clone = RuleSet(
        id=new_id,
        owner_id=current_user.id,
        name=f"{source.name} (copy)",
        description=source.description,
        lat=source.lat,
        lon=source.lon,
        altitude_m=source.altitude_m,
        combination_logic=source.combination_logic,
        is_public=False,
        clone_count=0,
        cloned_from_id=source.id,
    )
    db.add(clone)

    for c in source.conditions:
        db.add(RuleCondition(
            id=str(uuid.uuid4()),
            ruleset_id=new_id,
            station_id=c.station_id,
            station_b_id=c.station_b_id,
            field=c.field,
            operator=c.operator,
            value_a=c.value_a,
            value_b=c.value_b,
            result_colour=c.result_colour,
            sort_order=c.sort_order,
            group_id=c.group_id,
        ))

    source.clone_count += 1
    db.commit()
    db.refresh(clone)
    logger.info("RuleSet %s cloned to %s by %s", source.id, new_id, current_user.id)
    return clone
