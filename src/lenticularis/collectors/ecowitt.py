"""
Ecowitt collector for Lenticularis.

Pulls real-time data from one or more Ecowitt personal weather stations via the
Ecowitt Cloud API v3 (api.ecowitt.net).  Each station has its own credentials
because Ecowitt application keys are per-account, not per-device.

Config format (under the collector's ``config`` key)::

    stations:
      - application_key: "<app-key>"   # from ecowitt.net → User → API
        api_key: "<api-key>"           # per-device key
        mac: "AA:BB:CC:DD:EE:FF"       # gateway MAC (from device settings)
        station_name: "My Station"     # display name (optional)
        latitude: 46.6863
        longitude: 7.8632
        elevation: 600                 # metres (optional)
      - ...                            # additional community stations
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_API_BASE = "https://api.ecowitt.net/api/v3"
_REAL_TIME_PATH = "/device/real_time"

# Unit IDs for the Ecowitt API request
_TEMP_UNIT = 1      # Celsius
_PRESSURE_UNIT = 3  # hPa
_WIND_SPEED_UNIT = 7  # km/h  — matches MeteoSwiss / SLF storage convention
_RAINFALL_UNIT = 12   # mm


def _to_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_int(val: object) -> Optional[int]:
    f = _to_float(val)
    return None if f is None else int(round(f))


def _field_value(data: dict, *keys: str) -> Optional[str]:
    """Safely walk nested dict and return the 'value' leaf, or None."""
    node = data
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    if isinstance(node, dict):
        return node.get("value")
    return None


# Station entry type for internal use
_StationCfg = dict  # keys: application_key, api_key, mac, station_name, latitude, longitude, elevation


class EcowittCollector(BaseCollector):
    """
    Collect real-time observations from one or more Ecowitt personal weather stations.

    Each station in the ``stations`` list has its own Ecowitt credentials
    (application_key + api_key) because Ecowitt keys are per-account.
    All stations are fetched concurrently.
    """

    NETWORK = "ecowitt"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        cfg = config or {}
        self._stations_cfg: list[_StationCfg] = cfg.get("stations", [])
        # Track last seen rain_daily per station MAC to compute per-interval delta.
        # Key: upper-case MAC without colons; value: last rain_daily float (mm).
        self._last_daily_rain: dict[str, float] = {}
        if not self._stations_cfg:
            logger.warning(
                "EcowittCollector: no stations configured. "
                "Add entries under config.stations in config.yml."
            )

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def get_stations(self) -> list[WeatherStation]:
        """Return WeatherStation metadata for every configured Ecowitt station."""
        stations: list[WeatherStation] = []
        for entry in self._stations_cfg:
            mac = entry.get("mac", "")
            lat = _to_float(entry.get("latitude"))
            lon = _to_float(entry.get("longitude"))
            if not mac:
                logger.warning("EcowittCollector: station entry missing mac — skipped.")
                continue
            if lat is None or lon is None:
                logger.warning(
                    "EcowittCollector: station %s has no lat/lon — will not appear on map.", mac
                )
                continue
            sid = self.station_id(self.NETWORK, mac.replace(":", "").upper())
            stations.append(WeatherStation(
                station_id=sid,
                name=entry.get("station_name") or f"Ecowitt {mac}",
                network=self.NETWORK,
                latitude=lat,
                longitude=lon,
                elevation=_to_int(entry.get("elevation")),
                canton=None,
            ))
        return stations

    async def collect(self) -> list[WeatherMeasurement]:
        """Fetch latest measurements from all configured Ecowitt stations concurrently."""
        import asyncio
        tasks = [self._collect_station(entry) for entry in self._stations_cfg]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        measurements: list[WeatherMeasurement] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("EcowittCollector: unhandled error in station task: %s", r)
            elif r is not None:
                measurements.append(r)
        return measurements

    # ------------------------------------------------------------------
    # Per-station fetch
    # ------------------------------------------------------------------

    async def _collect_station(self, entry: _StationCfg) -> Optional[WeatherMeasurement]:
        mac = entry.get("mac", "")
        application_key = entry.get("application_key", "")
        api_key = entry.get("api_key", "")

        if not (application_key and api_key and mac):
            logger.warning(
                "EcowittCollector: station entry is missing application_key/api_key/mac — skipped."
            )
            return None

        params = {
            "application_key": application_key,
            "api_key": api_key,
            "mac": mac,
            "call_back": "wind,outdoor,pressure,rainfall",
            "temp_unitid": _TEMP_UNIT,
            "pressure_unitid": _PRESSURE_UNIT,
            "wind_speed_unitid": _WIND_SPEED_UNIT,
            "rainfall_unitid": _RAINFALL_UNIT,
        }

        try:
            response = await self._get(f"{_API_BASE}{_REAL_TIME_PATH}", params=params)
        except Exception as exc:
            logger.error("EcowittCollector [%s]: HTTP request failed: %s", mac, exc)
            return None

        code = response.get("code")
        if code != 0:
            logger.error(
                "EcowittCollector [%s]: API error code=%s msg=%s",
                mac, code, response.get("msg", "unknown"),
            )
            return None

        data = response.get("data", {})

        raw_time = response.get("time")
        try:
            ts = datetime.fromtimestamp(int(raw_time), tz=timezone.utc)
        except (TypeError, ValueError):
            ts = datetime.now(tz=timezone.utc)
            logger.warning("EcowittCollector [%s]: bad timestamp %r; using now()", mac, raw_time)

        sid = self.station_id(self.NETWORK, mac.replace(":", "").upper())

        wind_speed = _to_float(_field_value(data, "wind", "wind_speed"))
        wind_gust = _to_float(_field_value(data, "wind", "wind_gust"))
        raw_dir = _to_int(_field_value(data, "wind", "wind_direction"))
        wind_direction: Optional[int] = None if raw_dir is None else raw_dir % 360

        temperature = _to_float(_field_value(data, "outdoor", "temperature"))
        humidity = _to_float(_field_value(data, "outdoor", "humidity"))

        # Ecowitt "relative" ≈ QNH; "absolute" ≈ QFE
        pressure_qnh = _to_float(_field_value(data, "pressure", "relative"))
        pressure_qfe = _to_float(_field_value(data, "pressure", "absolute"))

        # Use rain_daily delta so stored values are mm-per-interval (same semantics
        # as MeteoSwiss 10-min precipitation) rather than the instantaneous rate.
        mac_key = mac.replace(":", "").upper()
        raw_daily = _to_float(_field_value(data, "rainfall", "rain_daily"))
        precipitation: Optional[float] = None
        if raw_daily is not None:
            if mac_key in self._last_daily_rain:
                last = self._last_daily_rain[mac_key]
                if raw_daily >= last:
                    # Normal accumulation during the day
                    precipitation = round(raw_daily - last, 2)
                else:
                    # Midnight reset: new day just started, raw_daily is the new total
                    precipitation = round(raw_daily, 2)
            else:
                # First reading for this station — no delta available yet; emit 0
                precipitation = 0.0
            self._last_daily_rain[mac_key] = raw_daily

        logger.info(
            "EcowittCollector: %s — wind=%.1f km/h dir=%s°  temp=%.1f°C  pressure=%.1f hPa",
            sid, wind_speed or 0.0, wind_direction, temperature or 0.0, pressure_qnh or 0.0,
        )
        return WeatherMeasurement(
            station_id=sid,
            network=self.NETWORK,
            timestamp=ts,
            wind_speed=wind_speed,
            wind_direction=wind_direction,
            wind_gust=wind_gust,
            temperature=temperature,
            humidity=humidity,
            pressure_qnh=pressure_qnh,
            pressure_qfe=pressure_qfe,
            precipitation=precipitation,
        )
