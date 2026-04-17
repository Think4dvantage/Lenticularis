"""
METAR collector for Lenticularis.

Pulls no-auth METAR observations from AviationWeather Data API and normalizes
them to ``WeatherMeasurement`` rows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_METAR_URL = "https://aviationweather.gov/api/data/metar"
_STATIONINFO_URL = "https://aviationweather.gov/api/data/stationinfo"

# Major Swiss METAR airports/aerodromes.
_DEFAULT_ICAO_CODES = [
    "LSZH",  # Zurich
    "LSGG",  # Geneva
    "LSZB",  # Bern
    "LSZR",  # Altenrhein
    "LSMP",  # Payerne
    "LSZA",  # Lugano
    "LSGS",  # Sion
    "LSGL",  # Lausanne
    "LSGC",  # Les Eplatures
]


def _parse_iso_utc(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _knots_to_kmh(knots: Optional[float]) -> Optional[float]:
    if knots is None:
        return None
    return knots * 1.852


class MetarCollector(BaseCollector):
    """Collect latest METAR observations from AviationWeather."""

    NETWORK = "metar"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        cfg = config or {}
        codes = cfg.get("icao_codes") or _DEFAULT_ICAO_CODES
        self._icao_codes: list[str] = [str(c).strip().upper() for c in codes if str(c).strip()]
        self._metar_url: str = cfg.get("metar_url", _METAR_URL)
        self._stationinfo_url: str = cfg.get("stationinfo_url", _STATIONINFO_URL)
        self._station_cache: dict[str, WeatherStation] = {}

    def _ids_param(self) -> str:
        return ",".join(self._icao_codes)

    async def get_stations(self) -> list[WeatherStation]:
        if not self._icao_codes:
            logger.warning("METAR: no ICAO codes configured")
            return []

        try:
            data = await self._get(
                self._stationinfo_url,
                params={
                    "ids": self._ids_param(),
                    "format": "json",
                },
            )
        except Exception as exc:
            logger.warning("METAR: stationinfo request failed: %s", exc)
            return []

        stations: list[WeatherStation] = []
        for row in data if isinstance(data, list) else []:
            icao = str(row.get("icaoId") or row.get("id") or "").strip().upper()
            if not icao:
                continue

            lat = _to_float(row.get("lat"))
            lon = _to_float(row.get("lon"))
            if lat is None or lon is None:
                continue

            sid = self.station_id(self.NETWORK, icao)
            station = WeatherStation(
                station_id=sid,
                name=str(row.get("site") or icao),
                network=self.NETWORK,
                latitude=lat,
                longitude=lon,
                elevation=int(_to_float(row.get("elev")) or 0) or None,
                canton=str(row.get("state") or "") or None,
            )
            stations.append(station)
            self._station_cache[sid] = station

        logger.info("METAR: discovered %d stations", len(stations))
        return stations

    async def collect(self) -> list[WeatherMeasurement]:
        if not self._icao_codes:
            logger.warning("METAR: no ICAO codes configured")
            return []

        try:
            rows = await self._get(
                self._metar_url,
                params={
                    "ids": self._ids_param(),
                    "format": "json",
                },
            )
        except Exception as exc:
            logger.warning("METAR: request failed: %s", exc)
            return []

        measurements: list[WeatherMeasurement] = []
        for row in rows if isinstance(rows, list) else []:
            icao = str(row.get("icaoId") or "").strip().upper()
            if not icao:
                continue

            ts = _parse_iso_utc(str(row.get("reportTime") or ""))
            if ts is None:
                obs_time = _to_float(row.get("obsTime"))
                ts = datetime.fromtimestamp(obs_time, tz=timezone.utc) if obs_time is not None else None
            if ts is None:
                continue

            wdir = row.get("wdir")
            wind_direction: Optional[int]
            try:
                wind_direction = int(float(wdir)) if wdir is not None else None
            except (TypeError, ValueError):
                wind_direction = None

            wind_speed = _knots_to_kmh(_to_float(row.get("wspd")))
            wind_gust = _knots_to_kmh(_to_float(row.get("wgst")))

            sid = self.station_id(self.NETWORK, icao)
            measurements.append(
                WeatherMeasurement(
                    station_id=sid,
                    network=self.NETWORK,
                    timestamp=ts,
                    wind_speed=wind_speed,
                    wind_direction=wind_direction,
                    wind_gust=wind_gust,
                    temperature=_to_float(row.get("temp")),
                    humidity=None,
                    pressure_qfe=None,
                    pressure_qff=_to_float(row.get("altim")),
                    precipitation=None,
                    snow_depth=None,
                )
            )

            # Fill cache opportunistically so station list can still render if
            # stationinfo endpoint is temporarily unavailable.
            if sid not in self._station_cache:
                lat = _to_float(row.get("lat"))
                lon = _to_float(row.get("lon"))
                if lat is not None and lon is not None:
                    self._station_cache[sid] = WeatherStation(
                        station_id=sid,
                        name=str(row.get("name") or icao),
                        network=self.NETWORK,
                        latitude=lat,
                        longitude=lon,
                        elevation=int(_to_float(row.get("elev")) or 0) or None,
                        canton=None,
                    )

        logger.info("METAR: collected %d measurements from %d reports", len(measurements), len(rows or []))
        return measurements