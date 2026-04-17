"""
SwissMeteo forecast container collector.

Fetches ICON-CH1/CH2 ensemble forecasts from the Lenticularis SwissMeteo
forecast container (lsmfapi).  The container exposes two endpoints:

  - /api/forecast/station   → surface forecast (all fields as EnsembleValue)
  - /api/forecast/altitude-winds → per-level wind profile (EnsembleValue)

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


def _probable(d) -> Optional[float]:
    """Extract the ``probable`` value from an EnsembleValue dict or scalar."""
    if d is None:
        return None
    if isinstance(d, dict):
        v = d.get("probable")
    else:
        v = d
    return float(v) if v is not None else None


def _ens(d, key: str) -> Optional[float]:
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    return float(v) if v is not None else None


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

        generated_at = data.get("generated_at")
        if not generated_at:
            self.logger.warning("No generated_at in surface response for %s", station_id)
            return []

        init_time = _parse_dt(generated_at)
        points: list[ForecastPoint] = []

        for hour in data.get("hours", []):
            vt_str = hour.get("valid_time")
            if not vt_str:
                continue
            valid_time = _parse_dt(vt_str)

            ws = hour.get("wind_speed")
            wg = hour.get("wind_gusts")
            wd = hour.get("wind_direction")
            tmp = hour.get("temperature")
            hum = hour.get("humidity")
            pqff = hour.get("pressure_qff")
            prec = hour.get("precipitation")

            wd_prob = _probable(wd)
            wd_min  = _ens(wd, "min")
            wd_max  = _ens(wd, "max")

            points.append(ForecastPoint(
                station_id=station_id,
                network=network,
                source=self.SOURCE,
                model=self.MODEL,
                init_time=init_time,
                valid_time=valid_time,
                wind_speed=_probable(ws),
                wind_speed_min=_ens(ws, "min"),
                wind_speed_max=_ens(ws, "max"),
                wind_gust=_probable(wg),
                wind_gust_min=_ens(wg, "min"),
                wind_gust_max=_ens(wg, "max"),
                wind_direction=int(wd_prob) if wd_prob is not None else None,
                wind_direction_min=int(wd_min) if wd_min is not None else None,
                wind_direction_max=int(wd_max) if wd_max is not None else None,
                temperature=_probable(tmp),
                temperature_min=_ens(tmp, "min"),
                temperature_max=_ens(tmp, "max"),
                humidity=_probable(hum),
                humidity_min=_ens(hum, "min"),
                humidity_max=_ens(hum, "max"),
                pressure_qff=_probable(pqff),
                pressure_qff_min=_ens(pqff, "min"),
                pressure_qff_max=_ens(pqff, "max"),
                precipitation=_probable(prec),
                precipitation_min=_ens(prec, "min"),
                precipitation_max=_ens(prec, "max"),
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

        generated_at = data.get("generated_at")
        if not generated_at:
            self.logger.warning("No generated_at in altitude response for %s", station_id)
            return []

        init_time = _parse_dt(generated_at)
        points: list[StationWindProfilePoint] = []

        for hour in data.get("hours", []):
            vt_str = hour.get("valid_time")
            if not vt_str:
                continue
            valid_time = _parse_dt(vt_str)

            for level in hour.get("levels", []):
                altitude_m = level.get("altitude_m")
                if altitude_m is None:
                    continue

                ws = level.get("wind_speed")
                wd = level.get("wind_direction")
                vw = level.get("vertical_wind")

                wd_prob = _probable(wd)
                wd_min = _ens(wd, "min")
                wd_max = _ens(wd, "max")

                points.append(StationWindProfilePoint(
                    station_id=station_id,
                    network=network,
                    level_m=int(altitude_m),
                    init_time=init_time,
                    valid_time=valid_time,
                    wind_speed=_probable(ws),
                    wind_speed_min=_ens(ws, "min"),
                    wind_speed_max=_ens(ws, "max"),
                    wind_direction=int(wd_prob) if wd_prob is not None else None,
                    wind_direction_min=int(wd_min) if wd_min is not None else None,
                    wind_direction_max=int(wd_max) if wd_max is not None else None,
                    vertical_wind=_probable(vw),
                    vertical_wind_min=_ens(vw, "min"),
                    vertical_wind_max=_ens(vw, "max"),
                ))

        self.logger.debug(
            "SwissMeteo altitude: %d points for %s (init %s)",
            len(points), station_id, init_time.isoformat(),
        )
        return points
