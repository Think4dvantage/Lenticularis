"""
Statistics API router.

Sub-domains
-----------
/api/stats/rulesets/*   — ruleset decision statistics (auth required)
/api/stats/weather/*    — weather data statistics (auth required)
/api/stats/service/*    — service / operational statistics (auth required)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import get_current_user
from lenticularis.database.db import get_db
from lenticularis.database.models import RuleSet, User
from lenticularis.services import stats as ruleset_stats
from lenticularis.services import weather_stats

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])


def _influx(request: Request):
    influx = getattr(request.app.state, "influx", None)
    if influx is None:
        raise HTTPException(status_code=503, detail="InfluxDB not available")
    return influx


def _registry(request: Request) -> dict:
    return getattr(request.app.state, "station_registry", {})


# ---------------------------------------------------------------------------
# Ruleset stats — overview for ALL of the caller's rulesets
# ---------------------------------------------------------------------------

@router.get("/rulesets/overview")
def get_rulesets_overview(
    hours: int = Query(720, ge=24, le=8760),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Aggregate stats across all of the caller's rulesets in one response:
    per_ruleset comparison, aggregate totals, hourly pattern, flyable days.
    """
    rulesets = db.scalars(
        select(RuleSet).where(RuleSet.owner_id == current_user.id)
    ).all()

    if not rulesets:
        return {
            "period_hours": hours,
            "per_ruleset": [],
            "aggregate": {"green_pct": 0.0, "orange_pct": 0.0, "red_pct": 0.0, "total": 0,
                          "green_count": 0, "orange_count": 0, "red_count": 0},
            "hourly_pattern": [{"hour": h, "green_pct": 0.0, "orange_pct": 0.0, "red_pct": 0.0, "total": 0}
                               for h in range(24)],
            "flyable_days": [],
        }

    rulesets_info = [{"id": rs.id, "name": rs.name, "site_type": rs.site_type} for rs in rulesets]
    return ruleset_stats.all_rulesets_overview(_influx(request), rulesets_info, hours)


# ---------------------------------------------------------------------------
# Weather stats — extremes leaderboard (no station selection)
# ---------------------------------------------------------------------------

@router.get("/weather/extremes")
def get_weather_extremes(
    period: str = Query("now", description="now | today | yesterday | last_week | tomorrow | date:YYYY-MM-DD"),
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """
    Return the extreme station per weather field for the requested period.

    - ``now``          — latest snapshot (fast)
    - ``today``        — UTC midnight → now (observations)
    - ``yesterday``    — yesterday UTC (observations)
    - ``last_week``    — rolling 7 days (observations)
    - ``tomorrow``     — next 24 h (forecast data)
    - ``date:YYYY-MM-DD`` — specific date (observations)
    """
    if period.startswith("date:"):
        # Validate date format
        try:
            from datetime import datetime as _dt
            _dt.strptime(period[5:], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format — use date:YYYY-MM-DD")
    elif period not in ("now", "today", "yesterday", "last_week", "tomorrow"):
        raise HTTPException(status_code=400, detail=f"Unknown period: {period!r}")

    return weather_stats.weather_extremes(_influx(request), _registry(request), period)


@router.get("/weather/station-freshness")
def get_station_freshness(
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """Freshness info for every station seen in the last 24 h."""
    return weather_stats.station_freshness(_influx(request))


@router.get("/weather/network-coverage")
def get_network_coverage(
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """Per-network station counts (total / fresh / stale)."""
    return weather_stats.network_coverage(_influx(request))


# ---------------------------------------------------------------------------
# Service stats
# ---------------------------------------------------------------------------

@router.get("/service/summary")
def get_service_summary(
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """High-level counts: users, rulesets, public rulesets, clones, collectors."""
    total_users = db.scalar(select(func.count(User.id))) or 0
    total_rulesets = db.scalar(select(func.count(RuleSet.id))) or 0
    public_rulesets = db.scalar(
        select(func.count(RuleSet.id)).where(RuleSet.is_public == True)  # noqa: E712
    ) or 0
    total_clones = db.scalar(select(func.sum(RuleSet.clone_count))) or 0

    scheduler = getattr(request.app.state, "scheduler", None)
    collector_count = 0
    healthy_collectors = 0
    if scheduler and hasattr(scheduler, "get_collector_health"):
        health_rows = scheduler.get_collector_health()
        collector_count = len(health_rows)
        healthy_collectors = sum(
            1 for r in health_rows
            if r.get("status") in ("ok", "ok_no_data", "scheduled", "running")
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "users": {"total": total_users},
        "rulesets": {
            "total": total_rulesets,
            "public": public_rulesets,
            "total_clones": int(total_clones),
        },
        "collectors": {
            "total": collector_count,
            "healthy": healthy_collectors,
        },
    }


@router.get("/service/influx")
def get_service_influx(
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """InfluxDB storage statistics: record counts, daily ingestion, disk size."""
    influx = _influx(request)
    weather_count   = influx.query_measurement_count("weather_data", days=365)
    forecast_count  = influx.query_measurement_count("weather_forecast", days=30)
    weather_daily   = influx.query_daily_ingestion("weather_data", days=30)
    forecast_daily  = influx.query_daily_ingestion("weather_forecast", days=30)
    storage_bytes   = influx.query_storage_bytes()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weather_records_365d": weather_count,
        "forecast_records_30d": forecast_count,
        "storage_bytes": storage_bytes,
        "weather_daily": weather_daily,
        "forecast_daily": forecast_daily,
    }
