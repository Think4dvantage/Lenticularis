"""
Open-Meteo forecast collector.

Fetches hourly ICON-seamless forecasts (MeteoSwiss ICON-CH1/CH2 for Switzerland)
from the Open-Meteo public API (no authentication required).

API reference: https://open-meteo.com/en/docs
MeteoSwiss ICON: https://open-meteo.com/en/docs/meteoswiss-api

Model selection
---------------
``icon_seamless`` blends ICON-CH1-EPS (1 km, 0–33 h) and ICON-CH2-EPS (2 km,
33–120 h) — the same models available on the MeteoSwiss STAC API, presented
as a single continuous time series.  For Switzerland this gives the best
available resolution without requiring GRIB2 parsing.

Data retention
--------------
Every model run is stored under its own ``init_time`` tag so that historical
runs are never overwritten.  The query layer picks the latest ``init_time``
per ``valid_time`` for live evaluation, but all runs are available for
forecast accuracy analysis.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lenticularis.collectors.forecast_base import BaseForecastCollector
from lenticularis.models.weather import ForecastPoint

_OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo variable names → ForecastPoint fields
_HOURLY_VARS = [
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "precipitation",
]


class ForecastOpenMeteoCollector(BaseForecastCollector):
    """
    Fetches ICON-seamless hourly forecasts from Open-Meteo for a single station
    lat/lon point.
    """

    SOURCE = "open-meteo"
    MODEL = "icon-seamless"

    async def collect_for_station(
        self,
        station_id: str,
        network: str,
        lat: float,
        lon: float,
        horizon_hours: int = 120,
    ) -> list[ForecastPoint]:
        # Open-Meteo maximum is 16 days; we cap at horizon_hours
        forecast_days = min((horizon_hours // 24) + 1, 16)

        data = await self._get(
            _OPENMETEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(_HOURLY_VARS),
                "wind_speed_unit": "kmh",
                "forecast_days": forecast_days,
                "models": "icon_seamless",
                "timezone": "UTC",
            },
        )

        hourly = data.get("hourly", {})
        times: list[str] = hourly.get("time", [])
        if not times:
            self.logger.warning("No hourly data in Open-Meteo response for %s", station_id)
            return []

        # init_time: round current UTC time down to the nearest full hour
        # (Open-Meteo doesn't expose the model run time directly; this approximates it)
        now_utc = datetime.now(timezone.utc)
        init_time = now_utc.replace(minute=0, second=0, microsecond=0)
        cutoff = init_time + timedelta(hours=horizon_hours)

        points: list[ForecastPoint] = []
        for i, t_str in enumerate(times):
            valid_time = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
            if valid_time > cutoff:
                break

            def _get_val(key: str):
                vals = hourly.get(key, [])
                return vals[i] if i < len(vals) else None

            wind_dir_raw = _get_val("wind_direction_10m")

            points.append(ForecastPoint(
                station_id=station_id,
                network=network,
                source=self.SOURCE,
                model=self.MODEL,
                init_time=init_time,
                valid_time=valid_time,
                wind_speed=_get_val("wind_speed_10m"),
                wind_gust=_get_val("wind_gusts_10m"),
                wind_direction=int(wind_dir_raw) if wind_dir_raw is not None else None,
                temperature=_get_val("temperature_2m"),
                humidity=_get_val("relative_humidity_2m"),
                pressure_qnh=_get_val("surface_pressure"),
                precipitation=_get_val("precipitation"),
            ))

        self.logger.debug(
            "Open-Meteo: collected %d forecast points for %s (init %s)",
            len(points), station_id, init_time.isoformat(),
        )
        return points
