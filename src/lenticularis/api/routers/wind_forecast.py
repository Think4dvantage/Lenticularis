"""
Wind forecast grid router — /api/wind-forecast

Serves the 0.25° Switzerland grid wind forecast at 8 altitude levels
(500 m – 5 000 m ASL) for the wind-forecast map page.

Endpoint
--------
GET /api/wind-forecast/grid?date=YYYY-MM-DD&level_m=1500

Returns a compact payload:
  {
    "date": "2026-04-12",
    "level_m": 1500,
    "level_hpa": 850,
    "grid": [{"lat": 45.8, "lon": 5.9}, ...],      # 171 canonical grid points
    "frames": [
      {"t": "2026-04-12T05:00:00+00:00", "ws": [...], "wd": [...]},
      ...
    ]
  }

The ``grid`` array is ordered by lat descending, lon ascending (north-to-south,
west-to-east) — matching the visual layout of the map. Each frame has parallel
``ws`` (wind_speed km/h) and ``wd`` (wind_direction °) arrays indexed to
``grid``.  Missing values are ``null``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from lenticularis.collectors.forecast_grid import GRID_POINTS
from lenticularis.models.weather import ALTITUDE_TO_HPA

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wind-forecast", tags=["wind-forecast"])

# Canonical grid order (lat desc, lon asc) — same order every response
_CANONICAL_GRID = sorted(GRID_POINTS, key=lambda p: (-p[0], p[1]))
_CANONICAL_GRID_DICTS = [{"lat": lat, "lon": lon} for lat, lon in _CANONICAL_GRID]
# Lookup: (lat, lon) → position index
_GRID_INDEX: dict[tuple[float, float], int] = {
    (lat, lon): i for i, (lat, lon) in enumerate(_CANONICAL_GRID)
}

_VALID_LEVEL_M = sorted(ALTITUDE_TO_HPA.keys())


@router.get("/grid")
async def get_wind_forecast_grid(
    request: Request,
    date_str: str = Query(
        None,
        alias="date",
        description="Forecast date (YYYY-MM-DD UTC). Defaults to tomorrow.",
    ),
    level_m: int = Query(
        1500,
        description=f"Altitude in metres ASL. One of {_VALID_LEVEL_M}.",
    ),
) -> dict:
    """
    Return hourly grid wind forecasts for one day at one altitude level.

    Data is sourced from the ``wind_forecast_grid`` InfluxDB measurement,
    written by the ForecastGridCollector every 60 minutes.
    """
    t0 = datetime.now(timezone.utc)
    logger.info(
        "[Lenti:wind-forecast] GET /grid date=%s level_m=%d",
        date_str, level_m,
    )

    # --- validate level_m ---------------------------------------------------
    if level_m not in ALTITUDE_TO_HPA:
        raise HTTPException(
            status_code=422,
            detail=f"level_m must be one of {_VALID_LEVEL_M}",
        )
    level_hpa = ALTITUDE_TO_HPA[level_m]

    # --- resolve date -------------------------------------------------------
    try:
        if date_str:
            target_date = date.fromisoformat(date_str)
        else:
            target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")

    start_dt = datetime(target_date.year, target_date.month, target_date.day,
                        tzinfo=timezone.utc)
    end_dt   = start_dt + timedelta(days=1)

    # --- query InfluxDB -----------------------------------------------------
    influx = request.app.state.influx
    rows = influx.query_forecast_grid(start_dt, end_dt, level_hpa)

    logger.info(
        "[Lenti:wind-forecast] InfluxDB returned %d rows for %s @ %dhPa (%.0f ms)",
        len(rows), target_date.isoformat(), level_hpa,
        (datetime.now(timezone.utc) - t0).total_seconds() * 1000,
    )

    if not rows:
        logger.warning(
            "[Lenti:wind-forecast] No grid data found for %s @ %dm — "
            "grid collector may not have run yet",
            target_date.isoformat(), level_m,
        )
        return {
            "date":     target_date.isoformat(),
            "level_m":  level_m,
            "level_hpa": level_hpa,
            "grid":     _CANONICAL_GRID_DICTS,
            "frames":   [],
        }

    # --- build compact frame structure --------------------------------------
    # Group rows by valid_time; build parallel ws/wd arrays per canonical grid index
    n_grid = len(_CANONICAL_GRID)

    by_time: dict[str, tuple[list, list]] = {}
    for row in rows:
        vt = row["valid_time"]
        if vt not in by_time:
            by_time[vt] = ([None] * n_grid, [None] * n_grid)
        lat = row.get("lat")
        lon = row.get("lon")
        if lat is None or lon is None:
            continue
        idx = _GRID_INDEX.get((round(lat, 2), round(lon, 2)))
        if idx is None:
            continue
        ws = row.get("wind_speed")
        wd = row.get("wind_direction")
        by_time[vt][0][idx] = round(ws, 1) if ws is not None else None
        by_time[vt][1][idx] = wd  # already int|None from query layer

    frames = [
        {"t": vt, "ws": ws_arr, "wd": wd_arr}
        for vt, (ws_arr, wd_arr) in sorted(by_time.items())
    ]

    elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    logger.info(
        "[Lenti:wind-forecast] Built %d frames for %s @ %dm in %.0f ms total",
        len(frames), target_date.isoformat(), level_m, elapsed_ms,
    )

    return {
        "date":      target_date.isoformat(),
        "level_m":   level_m,
        "level_hpa": level_hpa,
        "grid":      _CANONICAL_GRID_DICTS,
        "frames":    frames,
    }
