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


@router.get("/mcp")
async def get_mcp_health(request: Request):
    """Public MCP server status: enabled flag, verified station count and usage counters."""
    handle = getattr(request.app.state, "mcp", None)
    registry = getattr(request.app.state, "mcp_registry", None)
    if handle is None or registry is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "verified_networks": sorted(registry.verified_networks),
        "verified_stations": len(registry.stations),
        "rate_limit_per_minute": handle.cfg.rate_limit_per_minute,
        "tracked_callers": len(handle.limiter),
        **handle.usage.snapshot(),
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
