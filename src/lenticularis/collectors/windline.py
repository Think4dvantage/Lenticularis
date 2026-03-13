"""
Windline collector for Lenticularis.

Windline does not provide a station listing endpoint, so stations are configured
explicitly by ID. Each station is fetched individually from:
  https://m.windline.ch/sensorAjaxData.php?stationID={ID}
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_DATA_URL = "https://m.windline.ch/sensorAjaxData.php"

_DEFAULT_STATIONS = [
    {"id": "6200", "name": "Amisbuehl"},
    {"id": "4104", "name": "Grindelwald First"},
    {"id": "6679", "name": "Hohwald"},
    {"id": "6116", "name": "Lehn"},
    {"id": "4109", "name": "Niederhorn"},
    {"id": "4096", "name": "Niederhuenigen"},
]


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> Optional[int]:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(round(parsed))


def _parse_epoch_ms(value: object) -> Optional[datetime]:
    parsed = _to_float(value)
    if parsed is None:
        return None
    # Windline timestamps are epoch milliseconds.
    return datetime.fromtimestamp(parsed / 1000.0, tz=timezone.utc)


def _dms_to_decimal(raw: object) -> Optional[float]:
    if raw is None:
        return None

    text = str(raw).strip().upper()
    if not text:
        return None

    match = re.search(r"([NSEW])\s*$", text)
    if not match:
        return None
    direction = match.group(1)

    parts = re.findall(r"\d+(?:\.\d+)?", text)
    if len(parts) < 3:
        return None

    deg = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2])
    value = deg + (minutes / 60.0) + (seconds / 3600.0)
    if direction in ("S", "W"):
        value = -value
    return value


def _normalise_wind_direction(value: object) -> Optional[int]:
    direction = _to_int(value)
    if direction is None:
        return None
    return direction % 360


def _mps_to_kmh(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return value * 3.6


class WindlineCollector(BaseCollector):
    """Collect station measurements from Windline."""

    NETWORK = "windline"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        cfg = config or {}

        self._data_url: str = str(cfg.get("data_url") or _DATA_URL)
        self._speed_unit: str = str(cfg.get("speed_unit") or "mps").strip().lower()
        self._concurrency: int = int(cfg.get("concurrency", 8))

        configured_stations = cfg.get("stations") or _DEFAULT_STATIONS
        self._stations: list[dict[str, str]] = []
        for entry in configured_stations:
            if isinstance(entry, dict):
                station_id = str(entry.get("id") or entry.get("station_id") or "").strip()
                station_name = str(entry.get("name") or station_id).strip()
            else:
                station_id = str(entry).strip()
                station_name = station_id

            if not station_id:
                continue
            self._stations.append({"id": station_id, "name": station_name or station_id})

        self._station_cache: dict[str, WeatherStation] = {}
        self._last_signature_by_station: dict[str, tuple] = {}

    def _speed_to_kmh(self, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if self._speed_unit == "kmh":
            return value
        return _mps_to_kmh(value)

    async def _fetch_station_payload(self, station_id: str) -> Optional[dict]:
        try:
            data = await self._get(self._data_url, params={"stationID": station_id})
            if isinstance(data, dict):
                return data
            return None
        except Exception as exc:
            logger.warning("Windline: failed to fetch station %s: %s", station_id, exc)
            return None

    def _station_from_payload(self, payload: dict, fallback_name: str) -> Optional[WeatherStation]:
        raw_station_id = str(payload.get("stationID") or "").strip()
        if not raw_station_id:
            return None

        sid = self.station_id(self.NETWORK, raw_station_id)
        latitude = _dms_to_decimal(payload.get("latitude"))
        longitude = _dms_to_decimal(payload.get("longitude"))
        if latitude is None or longitude is None:
            return None

        elevation = _to_int(payload.get("altitude"))
        # Ignore impossible elevation outliers from upstream payload glitches.
        if elevation is not None and (elevation < -200 or elevation > 6000):
            elevation = None

        station_name = str(payload.get("stationName") or fallback_name or raw_station_id).strip()
        return WeatherStation(
            station_id=sid,
            name=station_name,
            network=self.NETWORK,
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            canton=None,
        )

    async def get_stations(self) -> list[WeatherStation]:
        if not self._stations:
            logger.warning("Windline: no stations configured")
            return []

        semaphore = asyncio.Semaphore(max(1, self._concurrency))

        async def fetch(entry: dict[str, str]) -> Optional[WeatherStation]:
            async with semaphore:
                payload = await self._fetch_station_payload(entry["id"])
            if not payload:
                return None
            return self._station_from_payload(payload, fallback_name=entry["name"])

        results = await asyncio.gather(*(fetch(s) for s in self._stations), return_exceptions=True)

        stations: list[WeatherStation] = []
        for item in results:
            if isinstance(item, Exception):
                logger.warning("Windline: station metadata task failed: %s", item)
                continue
            if item is None:
                continue
            stations.append(item)
            self._station_cache[item.station_id] = item

        logger.info("Windline: discovered %d stations", len(stations))
        return stations

    async def collect(self) -> list[WeatherMeasurement]:
        if not self._stations:
            logger.warning("Windline: no stations configured")
            return []

        semaphore = asyncio.Semaphore(max(1, self._concurrency))

        async def fetch_measurement(entry: dict[str, str]) -> Optional[WeatherMeasurement]:
            async with semaphore:
                payload = await self._fetch_station_payload(entry["id"])
            if not payload:
                return None

            ts = _parse_epoch_ms(payload.get("timestamp"))
            raw_station_id = str(payload.get("stationID") or entry["id"]).strip()
            if ts is None or not raw_station_id:
                return None

            sid = self.station_id(self.NETWORK, raw_station_id)
            station = self._station_from_payload(payload, fallback_name=entry["name"])
            if station is not None:
                self._station_cache[sid] = station

            wind_speed = self._speed_to_kmh(_to_float(payload.get("windspeed1")))
            wind_direction = _normalise_wind_direction(payload.get("winddir1"))
            wind_gust = self._speed_to_kmh(_to_float(payload.get("windpeak1")))
            temperature = _to_float(payload.get("temp1"))
            humidity = _to_float(payload.get("hum1"))
            pressure_qfe = _to_float(payload.get("airpressurestation_qfe"))
            pressure_qnh = _to_float(payload.get("airpressure1"))
            precipitation = _to_float(payload.get("rainfall1"))

            signature = (
                ts,
                wind_speed,
                wind_direction,
                wind_gust,
                temperature,
                humidity,
                pressure_qfe,
                pressure_qnh,
                precipitation,
            )
            if self._last_signature_by_station.get(sid) == signature:
                logger.debug("Windline: skipped unchanged payload for station %s", sid)
                return None

            self._last_signature_by_station[sid] = signature

            measurement = WeatherMeasurement(
                station_id=sid,
                network=self.NETWORK,
                timestamp=ts,
                wind_speed=wind_speed,
                wind_direction=wind_direction,
                wind_gust=wind_gust,
                temperature=temperature,
                humidity=humidity,
                pressure_qfe=pressure_qfe,
                pressure_qnh=pressure_qnh,
                pressure_qff=None,
                precipitation=precipitation,
                snow_depth=None,
            )
            return measurement

        results = await asyncio.gather(*(fetch_measurement(s) for s in self._stations), return_exceptions=True)

        measurements: list[WeatherMeasurement] = []
        for item in results:
            if isinstance(item, Exception):
                logger.warning("Windline: measurement task failed: %s", item)
                continue
            if item is not None:
                measurements.append(item)

        logger.info("Windline: collected %d measurements from %d stations", len(measurements), len(self._stations))
        return measurements
