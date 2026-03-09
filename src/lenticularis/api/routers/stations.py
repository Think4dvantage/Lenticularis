"""
Stations API router — /api/stations

Endpoints:
  GET /api/stations               — list all stations with their latest measurement
  GET /api/stations/{station_id}  — single station metadata
  GET /api/stations/{station_id}/latest  — latest measurement for a station
  GET /api/stations/{station_id}/history — time-series for last N hours
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stations", tags=["stations"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class StationResponse(BaseModel):
    station_id: str
    name: str
    network: str
    latitude: float
    longitude: float
    elevation: Optional[int] = None
    canton: Optional[str] = None
    # Latest measurement snapshot (may be None if no data yet)
    latest: Optional[dict[str, Any]] = None


class MeasurementResponse(BaseModel):
    station_id: str
    data: Optional[dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_influx(request: Request):
    influx = getattr(request.app.state, "influx", None)
    if influx is None:
        raise HTTPException(status_code=503, detail="InfluxDB not available")
    return influx


def _get_station_registry(request: Request) -> dict:
    return getattr(request.app.state, "station_registry", {})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[StationResponse])
async def list_stations(request: Request):
    """
    Return all known stations with their most recent measurement snapshot.

    Stations come from the in-memory registry populated by collectors at startup
    and refreshed each collection run. Latest data is fetched from InfluxDB.
    """
    influx = _get_influx(request)
    registry: dict = _get_station_registry(request)

    # Fetch latest values for all stations in one InfluxDB query
    latest_all = influx.query_latest_all_stations()

    results: list[StationResponse] = []
    for sid, station in registry.items():
        latest = latest_all.get(sid)
        if latest:
            # Convert datetime to ISO string for JSON serialisation
            serialisable = {
                k: v.isoformat() if hasattr(v, "isoformat") else v
                for k, v in latest.items()
            }
        else:
            serialisable = None

        results.append(
            StationResponse(
                station_id=station.station_id,
                name=station.name,
                network=station.network,
                latitude=station.latitude,
                longitude=station.longitude,
                elevation=station.elevation,
                canton=station.canton,
                latest=serialisable,
            )
        )

    # Sort by network, then name for stable UI ordering
    results.sort(key=lambda s: (s.network, s.name))
    return results


@router.get("/{station_id}", response_model=StationResponse)
async def get_station(station_id: str, request: Request):
    """Return metadata for a single station."""
    registry = _get_station_registry(request)
    station = registry.get(station_id)
    if not station:
        raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found")

    influx = _get_influx(request)
    latest = influx.query_latest(station_id)
    if latest:
        latest = {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in latest.items()}

    return StationResponse(
        station_id=station.station_id,
        name=station.name,
        network=station.network,
        latitude=station.latitude,
        longitude=station.longitude,
        elevation=station.elevation,
        canton=station.canton,
        latest=latest,
    )


@router.get("/{station_id}/latest", response_model=MeasurementResponse)
async def get_latest(station_id: str, request: Request):
    """Return the most recent measurement for a station."""
    influx = _get_influx(request)
    data = influx.query_latest(station_id)
    if data:
        data = {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in data.items()}
    return MeasurementResponse(station_id=station_id, data=data)


@router.get("/{station_id}/history")
async def get_history(
    station_id: str,
    request: Request,
    hours: int = Query(default=24, ge=1, le=720, description="Number of hours of history to return (max 720 = 30 days)"),
):
    """Return time-series data for the last N hours (default 24, max 168)."""
    influx = _get_influx(request)
    rows = influx.query_history(station_id, hours=hours)
    # Serialise datetime objects
    serialised = [
        {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()}
        for row in rows
    ]
    return {"station_id": station_id, "hours": hours, "count": len(serialised), "data": serialised}
