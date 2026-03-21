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
from lenticularis.database.models import User
from lenticularis.foehn_detection import (
    get_foehn_config_dict,
    set_foehn_config,
    reset_foehn_config,
)

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

    model_config = {"from_attributes": True}


class UserAdminUpdate(BaseModel):
    role: Optional[Literal["pilot", "customer", "admin"]] = None
    is_active: Optional[bool] = None


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

    target.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(target)
    return _serialise_user(target)


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
# Föhn config
# ---------------------------------------------------------------------------

@router.get("/foehn-config")
def get_foehn_config_endpoint(
    current_user: User = Depends(require_admin),
):
    return get_foehn_config_dict()


@router.put("/foehn-config")
def update_foehn_config(
    body: dict,
    current_user: User = Depends(require_admin),
):
    try:
        set_foehn_config(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return get_foehn_config_dict()


@router.delete("/foehn-config")
def delete_foehn_config(
    current_user: User = Depends(require_admin),
):
    reset_foehn_config()
    return get_foehn_config_dict()
