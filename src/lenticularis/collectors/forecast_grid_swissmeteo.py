"""
SwissMeteo wind forecast grid collector.

Fetches ICON-CH1/CH2 wind forecasts from the lsmfapi container for a fixed
set of altitude levels covering Switzerland.  One HTTP request per altitude
level; all levels fetched in parallel (lsmfapi runs in the same Docker network,
no rate limiting).

Data is stored in the ``wind_forecast_grid`` InfluxDB measurement — same
schema as the Open-Meteo grid collector — so the wind-forecast map works
without frontend changes.

``grid_id`` is formatted as ``"{lat:.4f}_{lon:.4f}"`` (4 decimal places) to
uniquely identify lsmfapi's native ICON-CH1 ~10 km grid points, which are not
aligned to a regular 0.25° Open-Meteo grid.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from lenticularis.models.weather import ALTITUDE_TO_HPA, GridForecastPoint

logger = logging.getLogger(__name__)

_LEVELS_M: list[int] = list(ALTITUDE_TO_HPA.keys())


class ForecastGridSwissMeteoCollector:
    """Collects wind forecast grid from lsmfapi for all altitude levels in parallel."""

    def __init__(self, base_url: str = "https://lsmfapi-dev.lg4.ch") -> None:
        self._base_url = base_url.rstrip("/")
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> None:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=60.0,
                headers={"User-Agent": "lenticularis/1.4 (https://lenti.cloud)"},
            )

    async def _fetch_level(self, level_m: int) -> list[GridForecastPoint]:
        assert self._http_client is not None
        url = f"{self._base_url}/api/forecast/grid"
        try:
            response = await self._http_client.get(url, params={"level_m": level_m})
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            logger.error("Timeout fetching SwissMeteo grid level_m=%d: %s", level_m, exc)
            raise
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP %s fetching SwissMeteo grid level_m=%d: %s", exc.response.status_code, level_m, exc)
            raise
        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching SwissMeteo grid level_m=%d: %s", level_m, exc)
            raise

        init_time_str = data.get("init_time")
        if not init_time_str:
            logger.warning("No init_time in SwissMeteo grid response for level_m=%d", level_m)
            return []

        init_time = datetime.fromisoformat(
            init_time_str.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        level_hpa = ALTITUDE_TO_HPA[level_m]
        grid = data.get("grid", [])
        frames = data.get("frames", [])
        points: list[GridForecastPoint] = []

        for frame in frames:
            vt_str = frame.get("valid_time")
            if not vt_str:
                continue
            valid_time = datetime.fromisoformat(
                vt_str.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            ws_arr = frame.get("ws", [])
            wd_arr = frame.get("wd", [])
            rh_arr = frame.get("rh", [])

            for i, pt in enumerate(grid):
                lat = pt["lat"]
                lon = pt["lon"]
                ws = ws_arr[i] if i < len(ws_arr) else None
                wd_raw = wd_arr[i] if i < len(wd_arr) else None
                rh = rh_arr[i] if i < len(rh_arr) else None

                points.append(GridForecastPoint(
                    grid_id=f"{lat:.4f}_{lon:.4f}",
                    lat=lat,
                    lon=lon,
                    level_hpa=level_hpa,
                    level_m=level_m,
                    init_time=init_time,
                    valid_time=valid_time,
                    wind_speed=float(ws) if ws is not None else None,
                    wind_direction=int(round(wd_raw)) if wd_raw is not None else None,
                    humidity=float(rh) if rh is not None else None,
                ))

        logger.debug(
            "SwissMeteo grid: %d points for level_m=%d (init %s)",
            len(points), level_m, init_time_str,
        )
        return points

    async def collect_all(self, horizon_hours: int = 120) -> list[GridForecastPoint]:
        await self._ensure_client()
        logger.info(
            "[Lenti:grid-collector] SwissMeteo grid: fetching %d levels in parallel",
            len(_LEVELS_M),
        )
        results = await asyncio.gather(
            *[self._fetch_level(lm) for lm in _LEVELS_M],
            return_exceptions=True,
        )
        all_points: list[GridForecastPoint] = []
        for level_m, result in zip(_LEVELS_M, results):
            if isinstance(result, Exception):
                logger.error(
                    "[Lenti:grid-collector] SwissMeteo grid level_m=%d failed: %s",
                    level_m, result,
                )
            else:
                all_points.extend(result)

        logger.info(
            "[Lenti:grid-collector] SwissMeteo grid: %d total points collected",
            len(all_points),
        )
        return all_points

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
