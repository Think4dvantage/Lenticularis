"""
Wind forecast grid router — /api/wind-forecast

Serves the Switzerland wind forecast grid at 8 altitude levels
(500 m – 5 000 m ASL) for the wind-forecast map page.

Endpoint
--------
GET /api/wind-forecast/grid?date=YYYY-MM-DD&level_m=1500

Returns a compact payload:
  {
    "date": "2026-04-12",
    "level_m": 1500,
    "level_hpa": 850,
    "grid": [{"lat": 45.8, "lon": 5.9}, ...],
    "frames": [
      {"t": "2026-04-12T05:00:00+00:00", "ws": [...], "wd": [...], "rh": [...]},
      ...
    ]
  }

The ``grid`` array is sorted lat-descending, lon-ascending (north-to-south,
west-to-east) and is built dynamically from whatever grid points are stored in
InfluxDB.  This makes the endpoint source-agnostic: it works for both the
Open-Meteo 0.25° grid (171 points) and the lsmfapi ICON-CH1 ~10 km grid
(1 272 points) without any router changes.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from lenticularis.models.weather import ALTITUDE_TO_HPA

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wind-forecast", tags=["wind-forecast"])

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

    Data is sourced from the ``wind_forecast_grid`` InfluxDB measurement.
    The grid is built dynamically from whatever points are present — no
    hardcoded grid assumption.
    """
    t0 = datetime.now(timezone.utc)
    logger.info(
        "[Lenti:wind-forecast] GET /grid date=%s level_m=%d",
        date_str, level_m,
    )

    if level_m not in ALTITUDE_TO_HPA:
        raise HTTPException(
            status_code=422,
            detail=f"level_m must be one of {_VALID_LEVEL_M}",
        )
    level_hpa = ALTITUDE_TO_HPA[level_m]

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

    influx = request.app.state.influx
    rows = influx.query_forecast_grid(start_dt, end_dt, level_hpa)

    logger.info(
        "[Lenti:wind-forecast] InfluxDB returned %d rows for %s @ %dhPa (%.0f ms)",
        len(rows), target_date.isoformat(), level_hpa,
        (datetime.now(timezone.utc) - t0).total_seconds() * 1000,
    )

    if not rows:
        logger.warning(
            "[Lenti:wind-forecast] No grid data found for %s @ %dm",
            target_date.isoformat(), level_m,
        )
        return {
            "date":      target_date.isoformat(),
            "level_m":   level_m,
            "level_hpa": level_hpa,
            "grid":      [],
            "frames":    [],
        }

    # Build canonical grid from whatever points are in InfluxDB.
    # Keyed by grid_id (string tag) to avoid float-precision issues on lat/lon.
    grid_by_id: dict[str, tuple[float, float]] = {}
    for row in rows:
        gid = row.get("grid_id")
        lat = row.get("lat")
        lon = row.get("lon")
        if gid and lat is not None and lon is not None:
            grid_by_id[gid] = (lat, lon)

    # lat desc, lon asc — canonical visual order (north-to-south, west-to-east)
    canonical = sorted(grid_by_id.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    grid_dicts = [{"lat": lat, "lon": lon} for _, (lat, lon) in canonical]
    grid_id_index = {gid: i for i, (gid, _) in enumerate(canonical)}
    n_grid = len(canonical)

    by_time: dict[str, tuple[list, list, list]] = {}
    for row in rows:
        gid = row.get("grid_id")
        if not gid:
            continue
        idx = grid_id_index.get(gid)
        if idx is None:
            continue
        vt = row["valid_time"]
        if vt not in by_time:
            by_time[vt] = ([None] * n_grid, [None] * n_grid, [None] * n_grid)
        ws = row.get("wind_speed")
        wd = row.get("wind_direction")
        rh = row.get("humidity")
        by_time[vt][0][idx] = round(ws, 1) if ws is not None else None
        by_time[vt][1][idx] = wd
        by_time[vt][2][idx] = rh

    frames = [
        {"t": vt, "ws": ws_arr, "wd": wd_arr, "rh": rh_arr}
        for vt, (ws_arr, wd_arr, rh_arr) in sorted(by_time.items())
    ]

    elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    logger.info(
        "[Lenti:wind-forecast] Built %d frames × %d grid points for %s @ %dm in %.0f ms",
        len(frames), n_grid, target_date.isoformat(), level_m, elapsed_ms,
    )

    return {
        "date":      target_date.isoformat(),
        "level_m":   level_m,
        "level_hpa": level_hpa,
        "grid":      grid_dicts,
        "frames":    frames,
    }
