"""
SLF collector for Lenticularis.

Uses the public SLF measurement API:
  - Station list + latest wind snapshot (GeoJSON)
  - Weekly per-station timeseries for detailed fields

Notes:
  - Stations belonging to the SMN network are skipped to avoid duplicating
    MeteoSwiss stations.
  - We ingest the latest N points per station (default 6), which mirrors the
    production winds-mobi strategy and provides enough overlap for hourly
    scheduler cadences.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_STATIONS_URL = "https://public-meas-data.slf.ch/public/station-data/timepoint/WIND_MEAN/current/geojson"
_TIMESERIES_URL = "https://public-meas-data.slf.ch/public/station-data/timeseries/week/current/{network}/{code}"


def _parse_timestamp(raw: str) -> Optional[datetime]:
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


class SlfCollector(BaseCollector):
    """Collect weather observations from the SLF public API."""

    NETWORK = "slf"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        cfg = config or {}
        self._stations_url: str = cfg.get("stations_url", _STATIONS_URL)
        self._timeseries_url: str = cfg.get("timeseries_url", _TIMESERIES_URL)
        self._max_points: int = int(cfg.get("max_points", 6))
        self._concurrency: int = int(cfg.get("concurrency", 20))

        # station_id -> (slf_network, station_code)
        self._station_refs: dict[str, tuple[str, str]] = {}
        self._station_cache: dict[str, WeatherStation] = {}

    async def get_stations(self) -> list[WeatherStation]:
        try:
            data = await self._get(self._stations_url)
        except Exception as exc:
            logger.warning("SLF: could not fetch station list: %s", exc)
            return []

        stations: list[WeatherStation] = []
        self._station_refs.clear()

        for feature in data.get("features", []):
            props = feature.get("properties", {})
            geometry = feature.get("geometry", {})

            slf_network = str(props.get("network") or "").strip()
            if slf_network == "SMN":
                # SMN belongs to MeteoSwiss and would duplicate those stations.
                continue

            code = str(props.get("code") or "").strip()
            if not code:
                continue

            coords = geometry.get("coordinates") or []
            if len(coords) < 2:
                continue

            lon = _to_float(coords[0])
            lat = _to_float(coords[1])
            if lon is None or lat is None:
                continue

            elevation = _to_float(props.get("elevation"))
            sid = self.station_id(self.NETWORK, code)
            station = WeatherStation(
                station_id=sid,
                name=str(props.get("label") or code),
                network=self.NETWORK,
                latitude=lat,
                longitude=lon,
                elevation=int(elevation) if elevation is not None else None,
                canton=None,
            )

            stations.append(station)
            self._station_cache[sid] = station
            self._station_refs[sid] = (slf_network, code)

        logger.info("SLF: discovered %d stations", len(stations))
        return stations

    async def _collect_station(self, station_id: str, slf_network: str, code: str, semaphore: asyncio.Semaphore) -> list[WeatherMeasurement]:
        async with semaphore:
            url = self._timeseries_url.format(network=slf_network, code=code)
            try:
                data = await self._get(url)
            except Exception as exc:
                logger.warning("SLF: failed station timeseries %s (%s/%s): %s", station_id, slf_network, code, exc)
                return []

        by_ts: dict[datetime, dict[str, float | int | None]] = {}

        def upsert(ts_raw: object, field: str, raw_value: object, *, integer: bool = False) -> None:
            ts = _parse_timestamp(str(ts_raw)) if ts_raw is not None else None
            if ts is None:
                return

            value = _to_float(raw_value)
            if value is None:
                return

            if ts not in by_ts:
                by_ts[ts] = {}

            if integer:
                by_ts[ts][field] = int(round(value)) % 360
            else:
                by_ts[ts][field] = value

        for row in (data.get("windDirectionMean") or [])[: self._max_points]:
            upsert(row.get("timestamp"), "wind_direction", row.get("value"), integer=True)

        for row in (data.get("windVelocityMean") or [])[: self._max_points]:
            upsert(row.get("timestamp"), "wind_speed", row.get("value"))

        for row in (data.get("windVelocityMax") or [])[: self._max_points]:
            upsert(row.get("timestamp"), "wind_gust", row.get("value"))

        for row in (data.get("temperatureAir") or [])[: self._max_points]:
            upsert(row.get("timestamp"), "temperature", row.get("value"))

        for row in (data.get("snowHeight") or [])[: self._max_points]:
            upsert(row.get("timestamp"), "snow_depth", row.get("value"))

        measurements: list[WeatherMeasurement] = []
        for ts in sorted(by_ts.keys(), reverse=True):
            values = by_ts[ts]
            measurements.append(
                WeatherMeasurement(
                    station_id=station_id,
                    network=self.NETWORK,
                    timestamp=ts,
                    wind_speed=values.get("wind_speed"),
                    wind_direction=values.get("wind_direction"),
                    wind_gust=values.get("wind_gust"),
                    temperature=values.get("temperature"),
                    humidity=None,
                    pressure_qfe=None,
                    pressure_qnh=None,
                    pressure_qff=None,
                    precipitation=None,
                    snow_depth=values.get("snow_depth"),
                )
            )

        return measurements

    async def collect(self) -> list[WeatherMeasurement]:
        if not self._station_refs:
            await self.get_stations()

        if not self._station_refs:
            logger.info("SLF: no stations available for collection")
            return []

        semaphore = asyncio.Semaphore(max(1, self._concurrency))
        tasks = [
            self._collect_station(station_id=sid, slf_network=ref[0], code=ref[1], semaphore=semaphore)
            for sid, ref in self._station_refs.items()
        ]
        station_rows = await asyncio.gather(*tasks, return_exceptions=True)

        measurements: list[WeatherMeasurement] = []
        for item in station_rows:
            if isinstance(item, Exception):
                logger.warning("SLF: station collection task failed: %s", item)
                continue
            measurements.extend(item)

        logger.info("SLF: collected %d measurements from %d stations", len(measurements), len(self._station_refs))
        return measurements