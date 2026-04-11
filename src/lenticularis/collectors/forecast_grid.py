"""
Wind forecast grid collector.

Fetches ICON-seamless hourly wind forecasts from Open-Meteo for a regular
0.25° geographic grid covering Switzerland (45.8–47.8°N, 5.9–10.6°E).
Data is collected at 8 pressure levels corresponding to common paragliding
altitudes (500 m – 5000 m ASL).

Unlike BaseForecastCollector (which operates per weather station), this
collector owns its own fixed grid and writes to the separate
``wind_forecast_grid`` InfluxDB measurement via ``write_forecast_grid()``.

Batch API
---------
Open-Meteo accepts comma-separated lat/lon arrays, returning a JSON array
rather than a single object.  This lets us fetch all ~171 grid points in
4 batch calls instead of 171 sequential calls — a major speedup.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from math import ceil

import httpx

from lenticularis.models.weather import ALTITUDE_TO_HPA, GridForecastPoint

logger = logging.getLogger(__name__)

_OPENMETEO_URL_FREE       = "https://api.open-meteo.com/v1/forecast"
_OPENMETEO_URL_COMMERCIAL = "https://customer-api.open-meteo.com/v1/forecast"

# 0.25° grid over Switzerland
_GRID_LATS: list[float] = [round(45.8 + 0.25 * i, 2) for i in range(9)]   # 45.80–47.80
_GRID_LONS: list[float] = [round(5.9  + 0.25 * i, 2) for i in range(19)]  # 5.90–10.60
GRID_POINTS: list[tuple[float, float]] = [
    (lat, lon) for lat in _GRID_LATS for lon in _GRID_LONS
]  # 171 points

# Pressure-level variable pairs requested from Open-Meteo
_LEVEL_VARS: list[tuple[int, int, str, str]] = [
    # (level_hpa, level_m, speed_var, dir_var)
    (950, 500,  "wind_speed_950hPa", "wind_direction_950hPa"),
    (900, 1000, "wind_speed_900hPa", "wind_direction_900hPa"),
    (850, 1500, "wind_speed_850hPa", "wind_direction_850hPa"),
    (800, 2000, "wind_speed_800hPa", "wind_direction_800hPa"),
    (750, 2500, "wind_speed_750hPa", "wind_direction_750hPa"),
    (700, 3000, "wind_speed_700hPa", "wind_direction_700hPa"),
    (600, 4000, "wind_speed_600hPa", "wind_direction_600hPa"),
    (500, 5000, "wind_speed_500hPa", "wind_direction_500hPa"),
]
_ALL_HOURLY_VARS = [v for _, _, sv, dv in _LEVEL_VARS for v in (sv, dv)]

# Open-Meteo batch API limit: max 50 lat/lon pairs per request (server-side, not rate-limit based)
_BATCH_SIZE = 50
# 429 retry delays (seconds)
_429_DELAYS = [10, 30, 60]


class ForecastGridCollector:
    """
    Collects wind forecasts for a fixed 0.25° Switzerland grid at 8 altitude levels.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key
        self._url = _OPENMETEO_URL_COMMERCIAL if api_key else _OPENMETEO_URL_FREE
        self._http_client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> None:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=60.0,
                headers={"User-Agent": "lenticularis/1.4 (https://lenti.cloud)"},
            )

    async def _get_list(self, url: str, params: dict) -> list[dict]:
        """HTTP GET that always returns a JSON list (Open-Meteo batch response)."""
        await self._ensure_client()
        assert self._http_client is not None
        attempt = 0
        while True:
            try:
                response = await self._http_client.get(url, params=params)
                if response.status_code == 429 and attempt < len(_429_DELAYS):
                    delay = _429_DELAYS[attempt]
                    logger.warning(
                        "HTTP 429 fetching grid batch — retry %d/%d in %ds",
                        attempt + 1, len(_429_DELAYS), delay,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, list) else [data]
            except httpx.TimeoutException as exc:
                logger.error("Timeout fetching grid batch: %s", exc)
                raise
            except httpx.HTTPStatusError as exc:
                logger.error("HTTP %s fetching grid batch: %s", exc.response.status_code, exc)
                raise
            except httpx.HTTPError as exc:
                logger.error("HTTP error fetching grid batch: %s", exc)
                raise

    async def collect_all(self, horizon_hours: int = 120) -> list[GridForecastPoint]:
        """
        Fetch wind forecasts for all 171 grid points in batches.

        Returns one GridForecastPoint per grid cell × altitude level × hourly step.
        """
        forecast_days = min((horizon_hours // 24) + 1, 16)
        now_utc = datetime.now(timezone.utc)
        init_time = now_utc.replace(minute=0, second=0, microsecond=0)
        cutoff = init_time + timedelta(hours=horizon_hours)

        n_batches = ceil(len(GRID_POINTS) / _BATCH_SIZE)
        logger.info(
            "[Lenti:grid-collector] Starting grid forecast collection — "
            "%d grid points, %d altitude levels, %d batches, horizon=%dh",
            len(GRID_POINTS), len(_LEVEL_VARS), n_batches, horizon_hours,
        )

        all_points: list[GridForecastPoint] = []
        t0 = datetime.now(timezone.utc)

        for batch_idx in range(n_batches):
            batch = GRID_POINTS[batch_idx * _BATCH_SIZE : (batch_idx + 1) * _BATCH_SIZE]
            lats = [str(p[0]) for p in batch]
            lons = [str(p[1]) for p in batch]

            logger.debug(
                "[Lenti:grid-collector] Fetching batch %d/%d (%d points)",
                batch_idx + 1, n_batches, len(batch),
            )
            try:
                params: dict = {
                    "latitude":        ",".join(lats),
                    "longitude":       ",".join(lons),
                    "hourly":          ",".join(_ALL_HOURLY_VARS),
                    "wind_speed_unit": "kmh",
                    "forecast_days":   forecast_days,
                    "models":          "icon_seamless",
                    "timezone":        "UTC",
                }
                if self._api_key:
                    params["apikey"] = self._api_key
                results = await self._get_list(self._url, params=params)
            except Exception as exc:
                logger.error(
                    "[Lenti:grid-collector] Batch %d/%d failed: %s — skipping",
                    batch_idx + 1, n_batches, exc,
                )
                continue

            batch_points = 0
            for item_idx, item in enumerate(results):
                if item_idx >= len(batch):
                    break
                lat, lon = batch[item_idx]
                grid_id = f"{lat:.2f}_{lon:.2f}"
                hourly = item.get("hourly", {})
                times: list[str] = hourly.get("time", [])

                for i, t_str in enumerate(times):
                    valid_time = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
                    if valid_time > cutoff:
                        break

                    for level_hpa, level_m, speed_var, dir_var in _LEVEL_VARS:
                        speed_vals = hourly.get(speed_var, [])
                        dir_vals   = hourly.get(dir_var, [])
                        ws  = speed_vals[i] if i < len(speed_vals) else None
                        wd_raw = dir_vals[i] if i < len(dir_vals) else None
                        wd  = int(wd_raw) if wd_raw is not None else None

                        all_points.append(GridForecastPoint(
                            grid_id=grid_id,
                            lat=lat,
                            lon=lon,
                            level_hpa=level_hpa,
                            level_m=level_m,
                            init_time=init_time,
                            valid_time=valid_time,
                            wind_speed=float(ws) if ws is not None else None,
                            wind_direction=wd,
                        ))
                        batch_points += 1

            logger.info(
                "[Lenti:grid-collector] Batch %d/%d done — %d points collected so far",
                batch_idx + 1, n_batches, len(all_points),
            )

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        logger.info(
            "[Lenti:grid-collector] Collection complete — %d total points in %.1fs",
            len(all_points), elapsed,
        )
        return all_points

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
