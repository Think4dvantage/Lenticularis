"""
Föhn Tracker router for Lenticularis.

Detection logic lives in ``lenticularis.foehn_detection`` (shared with the
scheduler collector that writes results to InfluxDB).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import get_current_user, get_current_user_optional
from lenticularis.database.db import get_db
from lenticularis.database.influx import InfluxClient
from lenticularis.database.models import User, UserFoehnConfig
from lenticularis.foehn_detection import (
    build_all_pressures,
    build_response,
    eval_region,
    get_foehn_config_dict,
    get_pressure_pairs,
    get_all_station_ids_from_config,
    get_all_pressure_pairs_from_config,
    get_required_lookback_hours,
    pressure_pairs_from_config,
    regions_from_config,
    set_foehn_config,
    reset_foehn_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/foehn", tags=["foehn"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_user_config(user: Optional[User], db: Session) -> Optional[dict]:
    """Return the user's stored föhn config, or None if they have none."""
    if user is None:
        return None
    row = db.get(UserFoehnConfig, user.id)
    if row is None:
        return None
    try:
        return json.loads(row.config_json)
    except Exception:
        return None


def _evaluate(
    latest: dict[str, dict],
    user_config: Optional[dict] = None,
    historical: Optional[dict[int, dict[str, dict]]] = None,
) -> tuple[list[dict], list[dict]]:
    regions   = [eval_region(r, latest, historical) for r in regions_from_config(user_config)]
    pressures = build_all_pressures(latest, pressure_pairs_from_config(user_config))
    return regions, pressures


# ---------------------------------------------------------------------------
# User config endpoints
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_user_foehn_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return the authenticated user's föhn config, or the system defaults."""
    config = _load_user_config(current_user, db)
    return config if config is not None else get_foehn_config_dict()


@router.put("/config")
async def put_user_foehn_config(
    data: dict,
    set_as_default: bool = Query(False, description="Admin only: also overwrite the system-wide default config"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Save the authenticated user's föhn config overrides.

    If ``set_as_default=true`` and the caller is an admin, the config is also
    written as the system-wide default (equivalent to the old admin panel).
    """
    row = db.get(UserFoehnConfig, current_user.id)
    if row:
        row.config_json = json.dumps(data)
        row.updated_at  = datetime.now(timezone.utc)
    else:
        row = UserFoehnConfig(
            user_id=current_user.id,
            config_json=json.dumps(data),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(row)
    db.commit()
    logger.info("User %s saved custom foehn config", current_user.id)

    if set_as_default:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Only admins can overwrite the system default")
        set_foehn_config(data)
        logger.info("Admin %s overwrote system-wide foehn default", current_user.id)
        return {"ok": True, "default_updated": True}

    return {"ok": True}


@router.delete("/config")
async def delete_user_foehn_config(
    set_as_default: bool = Query(False, description="Admin only: also reset the system-wide default to hardcoded values"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete the user's config override, reverting to system defaults.

    If ``set_as_default=true`` and the caller is an admin, also resets the
    system-wide default back to the hardcoded values.
    """
    row = db.get(UserFoehnConfig, current_user.id)
    if row:
        db.delete(row)
        db.commit()
        logger.info("User %s reset foehn config to defaults", current_user.id)

    if set_as_default:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Only admins can reset the system default")
        reset_foehn_config()
        logger.info("Admin %s reset system-wide foehn default to hardcoded values", current_user.id)
        return {"ok": True, "default_reset": True}

    return {"ok": True}


# ---------------------------------------------------------------------------
# Evaluation endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_foehn_status(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> dict:
    """Evaluate all föhn regions and pressure-gradient indicators (live data).

    When called with a valid Bearer token the user's saved config is applied.
    Historical snapshots are pre-fetched for any delta/trend conditions.
    """
    influx: InfluxClient = request.app.state.influx
    user_config  = _load_user_config(current_user, db)
    station_ids  = get_all_station_ids_from_config(user_config)
    latest       = influx.query_latest_for_stations(station_ids)

    # Pre-fetch historical snapshots for any delta conditions
    historical: dict[int, dict] = {}
    for h in get_required_lookback_hours(user_config):
        vt = datetime.now(timezone.utc) - timedelta(hours=h)
        historical[h] = influx.query_observation_snapshot_for_stations(station_ids, vt)
        logger.debug("foehn /status: fetched %dh-ago snapshot (%d stations)", h, len(historical[h]))

    regions, pressures = _evaluate(latest, user_config, historical or None)
    return build_response(regions, pressures, assessed_at=datetime.now(timezone.utc).isoformat())


@router.get("/forecast")
async def get_foehn_forecast(
    request: Request,
    valid_time: str = Query(..., description="ISO 8601 UTC timestamp, e.g. 2026-03-22T11:00:00Z"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> dict:
    """Evaluate föhn conditions from forecast data at a specific valid_time."""
    influx: InfluxClient = request.app.state.influx
    try:
        vt = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid valid_time: {valid_time!r}")

    user_config = _load_user_config(current_user, db)
    station_ids = get_all_station_ids_from_config(user_config)
    latest      = influx.query_forecast_snapshot_for_stations(station_ids, vt)
    regions, pressures = _evaluate(latest, user_config)
    return build_response(
        regions, pressures,
        assessed_at=datetime.now(timezone.utc).isoformat(),
        extra={"is_forecast": True, "valid_time": vt.isoformat()},
    )


@router.get("/observation")
async def get_foehn_observation(
    request: Request,
    valid_time: str = Query(..., description="ISO 8601 UTC timestamp, e.g. 2026-03-19T12:00:00Z"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> dict:
    """Evaluate föhn conditions from historical observed data at a specific time."""
    influx: InfluxClient = request.app.state.influx
    try:
        vt = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid valid_time: {valid_time!r}")

    user_config = _load_user_config(current_user, db)
    station_ids = get_all_station_ids_from_config(user_config)
    latest      = influx.query_observation_snapshot_for_stations(station_ids, vt)
    regions, pressures = _evaluate(latest, user_config)
    return build_response(
        regions, pressures,
        assessed_at=datetime.now(timezone.utc).isoformat(),
        extra={"is_snapshot": True, "valid_time": vt.isoformat()},
    )


@router.get("/history")
async def get_foehn_history(
    request: Request,
    hours: int = 48,
    center_time: Optional[str] = Query(
        None,
        description="ISO 8601 UTC center; window is ±hours/2 around this. Defaults to now.",
    ),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> dict:
    """Return hourly QFF pressure for all pressure-pair stations for the gradient chart.

    Includes global pressure pairs plus any per-region pairs from the user's config.
    """
    influx: InfluxClient = request.app.state.influx
    user_config = _load_user_config(current_user, db) if current_user else None

    # Collect all pairs: global + per-region (deduped)
    all_pairs = get_all_pressure_pairs_from_config(user_config)

    station_ids = list({sid for pair in all_pairs for sid in (pair["south_id"], pair["north_id"])})

    ct: Optional[datetime] = None
    if center_time:
        try:
            ct = datetime.fromisoformat(center_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid center_time: {center_time!r}")

    rows = influx.query_foehn_pressure_history(station_ids, hours=min(hours, 168), center_time=ct)
    return {
        "pairs": [
            {
                "key":              pair.get("key", pair["south_id"]),
                "south_station_id": pair["south_id"],
                "north_station_id": pair["north_id"],
                "south_label":      pair.get("south_label", pair["south_id"]),
                "north_label":      pair.get("north_label", pair["north_id"]),
            }
            for pair in all_pairs
        ],
        "rows": [
            {
                "station_id":   r["station_id"],
                "timestamp":    r["timestamp"].isoformat() if r.get("timestamp") else None,
                "pressure_qff": round(r["pressure_qff"], 2) if r.get("pressure_qff") is not None else None,
            }
            for r in rows
        ],
    }
