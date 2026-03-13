"""
Health API router — /api/health

Endpoints:
  GET /api/health/collectors — scheduler and collector run health snapshot
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/health", tags=["health"])


def _serialise_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in row.items()
    }


@router.get("/collectors")
async def get_collectors_health(request: Request):
    """Return last-run health state for each configured collector."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or not hasattr(scheduler, "get_collector_health"):
        raise HTTPException(status_code=503, detail="Scheduler not available")

    rows = scheduler.get_collector_health()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "collectors": [_serialise_row(row) for row in rows],
    }
