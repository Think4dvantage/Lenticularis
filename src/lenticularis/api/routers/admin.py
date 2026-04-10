"""
Admin API router — /api/admin

Endpoints:
  GET  /api/admin/users              — list all users
  PUT  /api/admin/users/{id}         — update role / active status
  GET  /api/admin/collectors         — collector health (same as /api/health/collectors)
  PUT  /api/admin/collectors/{name}  — toggle / reschedule collector at runtime
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import require_admin
from lenticularis.database.db import get_db
from lenticularis.database.models import Organization, StationDedupOverride, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class UserAdminOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    is_active: bool
    has_password: bool
    created_at: str
    updated_at: Optional[str] = None
    org_id: Optional[str] = None

    model_config = {"from_attributes": True}


class UserAdminUpdate(BaseModel):
    role: Optional[Literal["pilot", "customer", "admin", "org_admin", "org_pilot"]] = None
    is_active: Optional[bool] = None
    org_id: Optional[str] = None  # set to "" to clear org assignment


class OrgCreate(BaseModel):
    slug: str
    name: str
    description: Optional[str] = None


class OrgOut(BaseModel):
    id: str
    slug: str
    name: str
    description: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


class CollectorUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None


def _serialise_user(u: User) -> UserAdminOut:
    return UserAdminOut(
        id=u.id,
        email=u.email,
        display_name=u.display_name,
        role=u.role,
        is_active=u.is_active,
        has_password=u.hashed_password is not None,
        created_at=u.created_at.isoformat() if u.created_at else "",
        updated_at=u.updated_at.isoformat() if u.updated_at else None,
        org_id=u.org_id,
    )


def _serialise_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in row.items()
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/users")
def list_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.execute(select(User).order_by(User.created_at)).scalars().all()
    return {
        "users": [_serialise_user(u) for u in users],
        "total": len(users),
    }


@router.put("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserAdminUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent admins from locking themselves out
    if user_id == current_user.id:
        if body.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
        if body.role is not None and body.role != "admin":
            raise HTTPException(status_code=400, detail="Cannot change your own role")

    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.org_id is not None:
        # Empty string clears the org assignment
        target.org_id = body.org_id if body.org_id else None

    target.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(target)
    return _serialise_user(target)


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------

@router.get("/orgs")
def list_orgs(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    orgs = db.execute(select(Organization).order_by(Organization.created_at)).scalars().all()
    return {
        "orgs": [
            OrgOut(
                id=o.id,
                slug=o.slug,
                name=o.name,
                description=o.description,
                created_at=o.created_at.isoformat() if o.created_at else "",
            )
            for o in orgs
        ],
        "total": len(orgs),
    }


@router.post("/orgs", status_code=201)
def create_org(
    body: OrgCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    import uuid as _uuid
    existing = db.execute(
        select(Organization).where(Organization.slug == body.slug)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Organisation slug already exists")
    org = Organization(
        id=str(_uuid.uuid4()),
        slug=body.slug.lower().strip(),
        name=body.name.strip(),
        description=body.description,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return OrgOut(
        id=org.id,
        slug=org.slug,
        name=org.name,
        description=org.description,
        created_at=org.created_at.isoformat() if org.created_at else "",
    )


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

@router.get("/collectors")
async def get_collectors(
    request: Request,
    current_user: User = Depends(require_admin),
):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    rows = scheduler.get_collector_health()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "collectors": [_serialise_row(row) for row in rows],
    }


@router.post("/collectors/{health_key:path}/trigger")
async def trigger_collector(
    health_key: str,
    request: Request,
    current_user: User = Depends(require_admin),
):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    try:
        await scheduler.trigger_collector_now(health_key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"triggered": health_key}


@router.put("/collectors/{health_key:path}")
async def update_collector(
    health_key: str,
    body: CollectorUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not available")

    try:
        row = scheduler.update_collector_runtime(
            health_key,
            enabled=body.enabled,
            interval_minutes=body.interval_minutes,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _serialise_row(row)


# ---------------------------------------------------------------------------
# Station dedup overrides
# ---------------------------------------------------------------------------

class DedupOverrideOut(BaseModel):
    id: str
    station_id_a: str
    station_id_b: str
    note: Optional[str] = None
    created_at: str


class DedupOverrideCreate(BaseModel):
    station_id_a: str
    station_id_b: str
    note: Optional[str] = None


@router.get("/station-dedup")
def list_dedup_overrides(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.execute(select(StationDedupOverride).order_by(StationDedupOverride.created_at)).scalars().all()
    return {
        "overrides": [
            DedupOverrideOut(
                id=r.id,
                station_id_a=r.station_id_a,
                station_id_b=r.station_id_b,
                note=r.note,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]
    }


@router.post("/station-dedup", status_code=201)
def create_dedup_override(
    body: DedupOverrideCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    import uuid as _uuid
    # Reject if the pair (in either order) already exists
    existing = db.execute(
        select(StationDedupOverride).where(
            ((StationDedupOverride.station_id_a == body.station_id_a) &
             (StationDedupOverride.station_id_b == body.station_id_b)) |
            ((StationDedupOverride.station_id_a == body.station_id_b) &
             (StationDedupOverride.station_id_b == body.station_id_a))
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="This pair already exists")

    override = StationDedupOverride(
        id=str(_uuid.uuid4()),
        station_id_a=body.station_id_a,
        station_id_b=body.station_id_b,
        note=body.note,
    )
    db.add(override)
    db.commit()
    db.refresh(override)

    # Rebuild display registry so the change takes effect immediately
    from lenticularis.api.main import rebuild_display_registry
    rebuild_display_registry(request.app.state)

    return DedupOverrideOut(
        id=override.id,
        station_id_a=override.station_id_a,
        station_id_b=override.station_id_b,
        note=override.note,
        created_at=override.created_at.isoformat() if override.created_at else "",
    )


@router.delete("/station-dedup/{override_id}", status_code=204)
def delete_dedup_override(
    override_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    override = db.get(StationDedupOverride, override_id)
    if override is None:
        raise HTTPException(status_code=404, detail="Override not found")
    db.delete(override)
    db.commit()

    # Rebuild display registry so the change takes effect immediately
    from lenticularis.api.main import rebuild_display_registry
    rebuild_display_registry(request.app.state)
