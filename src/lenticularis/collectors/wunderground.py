"""
Weather Underground (Weather.com PWS) collector for Lenticularis.

Pulls real-time data from one or more Personal Weather Stations via the
Weather Underground / Weather.com PWS API v2.

You must explicitly list the station IDs you want to import — the API is
per-station, so there is no discovery endpoint and including too many stations
will multiply your API call count.

Config format (under the collector's ``config`` key)::

    api_key: "YOUR_WUNDERGROUND_API_KEY"
    stations:
      - station_id: "ICHE001"        # PWS ID from wunderground.com
        name: "My Paragliding Field" # display name (optional; API name used as fallback)
        latitude: 46.6863            # optional override (from API response if omitted)
        longitude: 7.8632            # optional override
        elevation: 600               # metres (optional override)
        canton: BE                   # optional
      - station_id: "ICHE002"
        name: "Ridge Station"

Unit system: metric (°C, hPa, km/h, mm).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_API_BASE = "https://api.weather.com/v2/pws/observations/current"


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


class WundergroundCollector(BaseCollector):
    """
    Collect real-time observations from Weather Underground Personal Weather Stations.

    Stations are fetched concurrently; only the station IDs listed in config
    are queried to avoid unexpected API load.
    """

    NETWORK = "wunderground"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        cfg = config or {}
        self._api_key: str = cfg.get("api_key", "")
        self._stations_cfg: list[dict] = cfg.get("stations", [])
        # Cache: PWS station_id → (lat, lon, elevation) from previous API response
        self._coord_cache: dict[str, dict] = {}
        if not self._api_key:
            logger.warning(
                "WundergroundCollector: no api_key configured. "
                "Add api_key under config in config.yml."
            )
        if not self._stations_cfg:
            logger.warning(
                "WundergroundCollector: no stations configured. "
                "Add entries under config.stations in config.yml."
            )

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def get_stations(self) -> list[WeatherStation]:
        """Return WeatherStation metadata for every configured PWS station."""
        stations: list[WeatherStation] = []
        for entry in self._stations_cfg:
            pws_id = entry.get("station_id", "")
            if not pws_id:
                logger.warning("WundergroundCollector: station entry missing station_id — skipped.")
                continue

            # Prefer config-supplied coords; fall back to cached API values
            lat = _to_float(entry.get("latitude")) or _to_float(
                self._coord_cache.get(pws_id, {}).get("lat")
            )
            lon = _to_float(entry.get("longitude")) or _to_float(
                self._coord_cache.get(pws_id, {}).get("lon")
            )
            elev = _to_int(entry.get("elevation")) or _to_int(
                self._coord_cache.get(pws_id, {}).get("elev")
            )
            if lat is None or lon is None:
                logger.warning(
                    "WundergroundCollector: station %s has no lat/lon yet "
                    "(add to config or wait for first collect run).",
                    pws_id,
                )
                continue

            sid = self.station_id(self.NETWORK, pws_id)
            stations.append(WeatherStation(
                station_id=sid,
                name=entry.get("name") or f"PWS {pws_id}",
                network=self.NETWORK,
                latitude=lat,
                longitude=lon,
                elevation=elev,
                canton=entry.get("canton"),
            ))
        return stations

    async def collect(self) -> list[WeatherMeasurement]:
        """Fetch latest measurements from all configured PWS stations concurrently."""
        import asyncio
        tasks = [self._collect_station(entry) for entry in self._stations_cfg]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        measurements: list[WeatherMeasurement] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("WundergroundCollector: unhandled error in station task: %s", r)
            elif r is not None:
                measurements.append(r)
        return measurements

    # ------------------------------------------------------------------
    # Per-station fetch
    # ------------------------------------------------------------------

    async def _collect_station(self, entry: dict) -> Optional[WeatherMeasurement]:
        pws_id = entry.get("station_id", "")
        if not pws_id:
            return None
        if not self._api_key:
            logger.warning("WundergroundCollector: api_key missing — cannot fetch %s", pws_id)
            return None

        params = {
            "stationId": pws_id,
            "format":    "json",
            "units":     "m",   # metric: °C, hPa, km/h, mm
            "apiKey":    self._api_key,
            "numericPrecision": "decimal",
        }

        try:
            response = await self._get(_API_BASE, params=params)
        except Exception as exc:
            logger.error("WundergroundCollector [%s]: HTTP request failed: %s", pws_id, exc)
            return None

        logger.debug("WundergroundCollector [%s]: raw response keys=%s", pws_id, list(response.keys()))

        observations = response.get("observations", [])
        if not observations:
            # Log at ERROR so it's always visible regardless of log level config
            logger.error(
                "WundergroundCollector [%s]: API returned no observations "
                "(station offline, wrong station ID, or invalid API key). "
                "Top-level response keys: %s",
                pws_id, list(response.keys()),
            )
            return None

        obs = observations[0]
        logger.debug("WundergroundCollector [%s]: observation keys=%s", pws_id, list(obs.keys()))

        # Parse timestamp
        raw_time = obs.get("obsTimeUtc")
        try:
            ts = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            ts = datetime.now(tz=timezone.utc)
            logger.warning(
                "WundergroundCollector [%s]: bad timestamp %r; using now()", pws_id, raw_time
            )

        # Cache coordinates from API response (used by get_stations if not in config)
        api_lat = _to_float(obs.get("lat"))
        api_lon = _to_float(obs.get("lon"))
        metric  = obs.get("metric", {})
        if not metric:
            logger.error(
                "WundergroundCollector [%s]: no 'metric' block in observation — "
                "units=m may not be accepted. Observation top-level keys: %s",
                pws_id, list(obs.keys()),
            )
        api_elev = _to_float(metric.get("elev"))
        self._coord_cache[pws_id] = {"lat": api_lat, "lon": api_lon, "elev": api_elev}

        # Resolve lat/lon: config overrides API
        lat = _to_float(entry.get("latitude")) or api_lat
        lon = _to_float(entry.get("longitude")) or api_lon

        # Actual PWS API v2 metric field names (units=m → km/h, °C, hPa, mm)
        wind_speed    = _to_float(metric.get("windSpeed"))
        wind_gust     = _to_float(metric.get("windGust"))
        raw_dir       = obs.get("winddir")
        wind_direction: Optional[int] = None if raw_dir is None else int(raw_dir) % 360
        temperature   = _to_float(metric.get("temp"))
        humidity      = _to_float(obs.get("humidity"))
        pressure_qff  = _to_float(metric.get("pressure"))
        precipitation = _to_float(metric.get("precipRate"))  # mm/hr instantaneous rate

        sid = self.station_id(self.NETWORK, pws_id)

        all_null = all(v is None for v in (wind_speed, wind_gust, wind_direction, temperature, humidity, pressure_qff))
        if all_null:
            logger.error(
                "WundergroundCollector [%s]: observation returned but ALL metric values are None. "
                "metric block: %s  |  obs top-level: winddir=%r humidity=%r",
                pws_id, metric, raw_dir, obs.get("humidity"),
            )
        else:
            logger.info(
                "WundergroundCollector: %s — wind=%.1f km/h dir=%s°  temp=%.1f°C  pressure=%.1f hPa",
                sid,
                wind_speed or 0.0,
                wind_direction,
                temperature or 0.0,
                pressure_qff or 0.0,
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
            pressure_qff=pressure_qff,
            pressure_qfe=None,
            precipitation=precipitation,
        )
