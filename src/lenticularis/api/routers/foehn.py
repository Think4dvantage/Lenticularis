"""
Föhn Tracker router for Lenticularis.

Detection logic lives in ``lenticularis.foehn_detection`` (shared with the
scheduler collector that writes results to InfluxDB).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from lenticularis.database.influx import InfluxClient
from lenticularis.foehn_detection import (
    ALL_STATION_IDS,
    PRESSURE,
    REGIONS,
    build_pressure,
    build_response,
    eval_region,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/foehn", tags=["foehn"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evaluate(latest: dict[str, dict]) -> tuple[list[dict], dict]:
    regions  = [eval_region(r, latest) for r in REGIONS]
    pressure = build_pressure(latest)
    return regions, pressure


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_foehn_status(request: Request) -> dict:
    """Evaluate all föhn regions and the pressure-gradient indicator (live data)."""
    influx: InfluxClient = request.app.state.influx
    latest = influx.query_latest_for_stations(ALL_STATION_IDS)
    regions, pressure = _evaluate(latest)
    return build_response(regions, pressure, assessed_at=datetime.now(timezone.utc).isoformat())


@router.get("/forecast")
async def get_foehn_forecast(
    request: Request,
    valid_time: str = Query(..., description="ISO 8601 UTC timestamp, e.g. 2026-03-22T11:00:00Z"),
) -> dict:
    """Evaluate föhn conditions from forecast data at a specific valid_time."""
    influx: InfluxClient = request.app.state.influx
    try:
        vt = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid valid_time: {valid_time!r}")

    latest = influx.query_forecast_snapshot_for_stations(ALL_STATION_IDS, vt)
    regions, pressure = _evaluate(latest)
    return build_response(
        regions, pressure,
        assessed_at=datetime.now(timezone.utc).isoformat(),
        extra={"is_forecast": True, "valid_time": vt.isoformat()},
    )


@router.get("/observation")
async def get_foehn_observation(
    request: Request,
    valid_time: str = Query(..., description="ISO 8601 UTC timestamp, e.g. 2026-03-19T12:00:00Z"),
) -> dict:
    """Evaluate föhn conditions from historical observed data at a specific time."""
    influx: InfluxClient = request.app.state.influx
    try:
        vt = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid valid_time: {valid_time!r}")

    latest = influx.query_observation_snapshot_for_stations(ALL_STATION_IDS, vt)
    regions, pressure = _evaluate(latest)
    return build_response(
        regions, pressure,
        assessed_at=datetime.now(timezone.utc).isoformat(),
        extra={"is_snapshot": True, "valid_time": vt.isoformat()},
    )


@router.get("/history")
async def get_foehn_history(
    request: Request,
    hours: int = 48,
    center_time: Optional[str] = Query(None, description="ISO 8601 UTC center; window is ±24h around this. Defaults to now (live)."),
) -> dict:
    """Return hourly pressure_qnh for OTL and JUN for the pressure-gradient chart."""
    influx: InfluxClient = request.app.state.influx
    station_ids = [PRESSURE["south_id"], PRESSURE["north_id"]]
    ct: Optional[datetime] = None
    if center_time:
        try:
            ct = datetime.fromisoformat(center_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid center_time: {center_time!r}")
    rows = influx.query_foehn_pressure_history(station_ids, hours=min(hours, 168), center_time=ct)
    return {
        "south_station_id": PRESSURE["south_id"],
        "north_station_id": PRESSURE["north_id"],
        "rows": [
            {
                "station_id":   r["station_id"],
                "timestamp":    r["timestamp"].isoformat() if r.get("timestamp") else None,
                "pressure_qnh": round(r["pressure_qnh"], 2) if r.get("pressure_qnh") is not None else None,
            }
            for r in rows
        ],
    }
