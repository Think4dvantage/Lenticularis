"""
Holfuy collector for Lenticularis.

Fetches the latest wind measurements from the Holfuy API using an API key.
All stations associated with the key are fetched in a single request when
``station_ids`` is set to ``"all"`` (the default), or a filtered subset
when specific IDs are listed in config.

API endpoint (authenticated via ``pw`` query param):
  GET https://api.holfuy.com/live/
      ?pw=<key>&m=JSON&s=<id|all>&su=km/h&tu=C&loc&utc

Key parameters (from official docs at https://api.holfuy.com/live/):
  s    — station ID, comma-separated list, or "all"
  pw   — API password/key
  m    — response format: JSON | CSV | XML (default CSV)
  su   — wind speed unit: km/h | m/s | knots | mph (default m/s)
  tu   — temperature unit: C | F (default C)
  loc  — flag: include station location (lat/lon/altitude) in JSON response
  utc  — flag: return timestamps in UTC instead of CE(S)T

Field mapping:
  wind.speed     → wind_speed  (km/h, requested via su=km/h)
  wind.gust      → wind_gust   (km/h)
  wind.direction → wind_direction (degrees 0–359)
  temperature    → temperature (°C, via tu=C)
  humidity       → humidity    (%)
  dateTime       → timestamp   (UTC, via utc flag)
  latitude/longitude/altitude → station coordinates (via loc flag)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_LIVE_URL = "https://api.holfuy.com/live/"


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(raw: object) -> Optional[datetime]:
    """Parse Holfuy dateTime strings: 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    if not raw:
        return None
    text = str(raw).strip()
    # Normalise the space separator to 'T' for fromisoformat
    text = text.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


class HolfuyCollector(BaseCollector):
    """Collect latest wind measurements from the Holfuy API."""

    NETWORK = "holfuy"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        cfg = config or {}

        self._api_key: str = str(cfg.get("api_key") or "").strip()
        if not self._api_key:
            logger.warning("HolfuyCollector: no api_key configured — collector will return no data")

        # station_ids: "all" (string) or a list of integer IDs
        raw_ids = cfg.get("station_ids", "all")
        if isinstance(raw_ids, list):
            self._station_ids: str = ",".join(str(i) for i in raw_ids)
        else:
            self._station_ids = str(raw_ids).strip() or "all"

        self._station_cache: dict[str, WeatherStation] = {}

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def get_stations(self) -> list[WeatherStation]:
        """Station metadata is embedded in the measurements response."""
        if not self._station_cache:
            await self.collect()
        return list(self._station_cache.values())

    async def collect(self) -> list[WeatherMeasurement]:
        if not self._api_key:
            return []

        params = {
            "pw":  self._api_key,
            "m":   "JSON",
            "s":   self._station_ids,
            "su":  "km/h",  # wind speed in km/h (default is m/s)
            "tu":  "C",     # temperature in Celsius
            "loc": "",      # include lat/lon/altitude in response
            "utc": "",      # return timestamps in UTC instead of CE(S)T
        }

        # Let HTTP/network errors propagate — the scheduler marks the job "error"
        # rather than silently showing "ok_no_data" for an authentication failure.
        data = await self._get(_LIVE_URL, params=params)

        # Response shape: {"measurements": [...]} wrapper, or bare list/dict
        if isinstance(data, dict):
            if "measurements" in data:
                entries = data["measurements"]
            else:
                entries = [data]
        elif isinstance(data, list):
            entries = data
        else:
            logger.warning(
                "Holfuy: unexpected response type %s — raw: %.200s",
                type(data).__name__, repr(data),
            )
            return []

        if not entries:
            logger.warning("Holfuy: API returned an empty station list")
            return []

        measurements: list[WeatherMeasurement] = []
        for entry in entries:
            m = self._parse_entry(entry)
            if m is not None:
                measurements.append(m)
            else:
                logger.debug("Holfuy: skipped unparseable entry: %.200s", repr(entry))

        logger.info("Holfuy: collected %d measurements from %d entries", len(measurements), len(entries))
        return measurements

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_entry(self, entry: dict) -> Optional[WeatherMeasurement]:
        raw_id = entry.get("stationId")
        if raw_id is None:
            logger.debug("Holfuy: entry missing stationId — skipping: %.200s", repr(entry))
            return None

        sid = self.station_id(self.NETWORK, str(raw_id))

        # Build / update station metadata from the embedded coordinates
        loc = entry.get("location") or {}
        lat = _to_float(loc.get("latitude"))
        lon = _to_float(loc.get("longitude"))
        if lat is not None and lon is not None and sid not in self._station_cache:
            alt_raw = loc.get("altitude")
            alt = int(float(alt_raw)) if alt_raw is not None else None
            self._station_cache[sid] = WeatherStation(
                station_id=sid,
                name=str(entry.get("stationName") or raw_id),
                network=self.NETWORK,
                latitude=lat,
                longitude=lon,
                elevation=alt,
                canton=None,
            )

        ts = _parse_timestamp(entry.get("dateTime")) or datetime.now(timezone.utc)

        wind = entry.get("wind") or {}
        speed     = _to_float(wind.get("speed"))
        gust      = _to_float(wind.get("gust"))
        direction = wind.get("direction")
        if direction is not None:
            try:
                direction = int(float(direction)) % 360
            except (TypeError, ValueError):
                direction = None

        return WeatherMeasurement(
            station_id=sid,
            network=self.NETWORK,
            timestamp=ts,
            wind_speed=speed,
            wind_direction=direction,
            wind_gust=gust,
            temperature=_to_float(entry.get("temperature")),
            humidity=_to_float(entry.get("humidity")),
            pressure_qfe=None,
            pressure_qnh=None,
            pressure_qff=None,
            precipitation=None,
            snow_depth=None,
        )
