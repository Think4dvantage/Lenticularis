"""
Rule sets CRUD router.

Endpoints
---------
GET    /api/rulesets                  — list own rule sets
POST   /api/rulesets                  — create rule set
GET    /api/rulesets/gallery          — public rule sets from other pilots
GET    /api/rulesets/{id}             — get detail (with conditions)
PUT    /api/rulesets/{id}             — update metadata
DELETE /api/rulesets/{id}             — delete
PUT    /api/rulesets/{id}/conditions  — replace full condition list
POST   /api/rulesets/{id}/clone       — clone a public rule set
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import get_current_user
from lenticularis.database.db import get_db
from lenticularis.database.models import RuleCondition, RuleSet, User
from lenticularis.models.rules import (
    ConditionsReplaceRequest,
    RuleSetCreate,
    RuleSetDetail,
    RuleSetOut,
    RuleSetUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rulesets", tags=["rulesets"])


def _get_own_ruleset(ruleset_id: str, current_user: User, db: Session) -> RuleSet:
    rs = db.get(RuleSet, ruleset_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="Rule set not found")
    if rs.owner_id != current_user.id:
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
# Create
# ---------------------------------------------------------------------------

@router.post("", response_model=RuleSetOut, status_code=status.HTTP_201_CREATED)
def create_ruleset(
    body: RuleSetCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rs = RuleSet(
        id=str(uuid.uuid4()),
        owner_id=current_user.id,
        **body.model_dump(),
    )
    db.add(rs)
    db.commit()
    db.refresh(rs)
    logger.info("RuleSet created: %s by %s", rs.id, current_user.id)
    return rs


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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
            **cond.model_dump(),
        ))

    rs.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rs)
    return rs


# ---------------------------------------------------------------------------
# Clone a public rule set
# ---------------------------------------------------------------------------

@router.post("/{ruleset_id}/clone", response_model=RuleSetOut, status_code=status.HTTP_201_CREATED)
def clone_ruleset(
    ruleset_id: str,
    current_user: User = Depends(get_current_user),
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
