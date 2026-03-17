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
from datetime import datetime, timedelta, timezone
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


@router.get("/data-bounds")
async def get_data_bounds(request: Request):
    """Return the earliest and latest timestamps that have any recorded data."""
    influx = _get_influx(request)
    bounds = influx.query_data_bounds()
    return {
        "earliest": bounds["earliest"].isoformat() if bounds["earliest"] else None,
        "latest": bounds["latest"].isoformat() if bounds["latest"] else None,
    }


@router.get("/replay")
async def get_replay(
    request: Request,
    hours: Optional[int] = Query(default=None, ge=1, le=168, description="Relative range in hours (1–168)"),
    start: Optional[str] = Query(default=None, description="ISO 8601 start datetime"),
    end: Optional[str] = Query(default=None, description="ISO 8601 end datetime"),
):
    """
    Return historical measurements for **all** stations within a time window,
    suitable for client-side replay.

    Either supply ``hours`` for a relative window ending now, or both ``start``
    and ``end`` for an absolute window (max 7 days).
    """
    influx = _get_influx(request)
    registry = _get_station_registry(request)
    now = datetime.now(timezone.utc)

    if hours is not None:
        end_dt = now
        start_dt = now - timedelta(hours=hours)
    elif start and end:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ISO 8601 date format")
        if (end_dt - start_dt).total_seconds() > 7 * 24 * 3600:
            raise HTTPException(status_code=400, detail="Date range too large (max 7 days)")
        if end_dt <= start_dt:
            raise HTTPException(status_code=400, detail="end must be after start")
    else:
        end_dt = now
        start_dt = now - timedelta(hours=24)

    raw = influx.query_history_all_stations(start_dt, end_dt)

    result: dict[str, Any] = {}
    for sid, measurements in raw.items():
        station = registry.get(sid)
        serialised = [
            {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in m.items()}
            for m in measurements
        ]
        result[sid] = {
            "station": {
                "station_id": sid,
                "name": station.name if station else sid,
                "network": station.network if station else "",
                "latitude": station.latitude if station else None,
                "longitude": station.longitude if station else None,
                "elevation": station.elevation if station else None,
                "canton": station.canton if station else None,
            },
            "measurements": serialised,
        }

    return {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "station_count": len(result),
        "data": result,
    }


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
