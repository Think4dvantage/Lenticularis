"""
SwissMeteo forecast container collector.

Fetches ICON-CH1/CH2 ensemble forecasts from the Lenticularis SwissMeteo
forecast container (lsmfapi).  The container exposes two endpoints:

  - /api/forecast/station   → surface forecast (flat fields with _min/_max)
  - /api/forecast/altitude-winds → per-level wind profile (flat fields with _min/_max)

Surface forecast is stored in the existing ``weather_forecast`` InfluxDB
measurement (``probable`` values only, matching the Open-Meteo convention).

Altitude wind profiles are stored in the new ``station_wind_profile``
measurement (full ensemble: probable / min / max).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from lenticularis.collectors.forecast_base import BaseForecastCollector
from lenticularis.models.weather import ForecastPoint, StationWindProfilePoint


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _f(d: dict, key: str) -> Optional[float]:
    v = d.get(key)
    return float(v) if v is not None else None


def _i(d: dict, key: str) -> Optional[int]:
    v = d.get(key)
    return int(round(float(v))) if v is not None else None


class ForecastSwissMeteoCollector(BaseForecastCollector):
    """
    Surface forecast collector backed by the SwissMeteo forecast container.

    SOURCE / MODEL tags written to InfluxDB: swissmeteo / icon-ch.
    The container blends ICON-CH1 (0–30 h) and ICON-CH2 (30–120 h) seamlessly.
    """

    SOURCE = "swissmeteo"
    MODEL = "icon-ch"

    def __init__(self, config=None, logger=None) -> None:
        super().__init__(config, logger)
        self._base_url: str = (config or {}).get(
            "base_url", "https://lsmfapi-dev.lg4.ch"
        ).rstrip("/")

    # ------------------------------------------------------------------
    # BaseForecastCollector interface — surface forecast
    # ------------------------------------------------------------------

    async def collect_for_station(
        self,
        station_id: str,
        network: str,
        lat: float,
        lon: float,
        horizon_hours: int = 120,
    ) -> list[ForecastPoint]:
        data = await self._get(
            f"{self._base_url}/api/forecast/station",
            params={"station_id": station_id, "hours": horizon_hours},
        )

        init_time_str = data.get("init_time")
        if not init_time_str:
            self.logger.warning("No init_time in surface response for %s", station_id)
            return []

        init_time = _parse_dt(init_time_str)
        points: list[ForecastPoint] = []

        for hour in data.get("forecast", []):
            vt_str = hour.get("valid_time")
            if not vt_str:
                continue
            valid_time = _parse_dt(vt_str)

            points.append(ForecastPoint(
                station_id=station_id,
                network=network,
                source=self.SOURCE,
                model=self.MODEL,
                init_time=init_time,
                valid_time=valid_time,
                wind_speed=_f(hour, "wind_speed"),
                wind_speed_min=_f(hour, "wind_speed_min"),
                wind_speed_max=_f(hour, "wind_speed_max"),
                wind_gust=_f(hour, "wind_gust"),
                wind_gust_min=_f(hour, "wind_gust_min"),
                wind_gust_max=_f(hour, "wind_gust_max"),
                wind_direction=_i(hour, "wind_direction"),
                wind_direction_min=_i(hour, "wind_direction_min"),
                wind_direction_max=_i(hour, "wind_direction_max"),
                temperature=_f(hour, "temperature"),
                temperature_min=_f(hour, "temperature_min"),
                temperature_max=_f(hour, "temperature_max"),
                humidity=_f(hour, "humidity"),
                humidity_min=_f(hour, "humidity_min"),
                humidity_max=_f(hour, "humidity_max"),
                pressure_qff=_f(hour, "pressure_qff"),
                pressure_qff_min=_f(hour, "pressure_qff_min"),
                pressure_qff_max=_f(hour, "pressure_qff_max"),
                precipitation=_f(hour, "precipitation"),
                precipitation_min=_f(hour, "precipitation_min"),
                precipitation_max=_f(hour, "precipitation_max"),
            ))

        self.logger.debug(
            "SwissMeteo surface: %d points for %s (init %s)",
            len(points), station_id, init_time.isoformat(),
        )
        return points

    # ------------------------------------------------------------------
    # Altitude wind profile — separate method, not part of BaseForecastCollector
    # ------------------------------------------------------------------

    async def collect_altitude_for_station(
        self,
        station_id: str,
        network: str,
        horizon_hours: int = 120,
    ) -> list[StationWindProfilePoint]:
        data = await self._get(
            f"{self._base_url}/api/forecast/altitude-winds",
            params={"station_id": station_id, "hours": horizon_hours},
        )

        init_time_str = data.get("init_time")
        if not init_time_str:
            self.logger.warning("No init_time in altitude response for %s", station_id)
            return []

        init_time = _parse_dt(init_time_str)
        points: list[StationWindProfilePoint] = []

        for profile in data.get("profiles", []):
            vt_str = profile.get("valid_time")
            if not vt_str:
                continue
            valid_time = _parse_dt(vt_str)

            for level in profile.get("levels", []):
                altitude_m = level.get("level_m")
                if altitude_m is None:
                    continue

                points.append(StationWindProfilePoint(
                    station_id=station_id,
                    network=network,
                    level_m=int(altitude_m),
                    init_time=init_time,
                    valid_time=valid_time,
                    wind_speed=_f(level, "wind_speed"),
                    wind_speed_min=_f(level, "wind_speed_min"),
                    wind_speed_max=_f(level, "wind_speed_max"),
                    wind_direction=_i(level, "wind_direction"),
                    wind_direction_min=_i(level, "wind_direction_min"),
                    wind_direction_max=_i(level, "wind_direction_max"),
                    vertical_wind=_f(level, "vertical_wind"),
                    vertical_wind_min=_f(level, "vertical_wind_min"),
                    vertical_wind_max=_f(level, "vertical_wind_max"),
                ))

        self.logger.debug(
            "SwissMeteo altitude: %d points for %s (init %s)",
            len(points), station_id, init_time.isoformat(),
        )
        return points
