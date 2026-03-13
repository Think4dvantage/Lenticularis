"""
MeteoSwiss collector for Lenticularis.

Hits the free public GeoJSON API at data.geo.admin.ch — 8 separate endpoints,
one per observation type. Converts Swiss LV95 grid coordinates (EPSG:2056) to
WGS84 (EPSG:4326) using pyproj.

Endpoint URL pattern:
  https://data.geo.admin.ch/ch.meteoschweiz.messwerte-{param}/
     ch.meteoschweiz.messwerte-{param}_en.json
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from pyproj import Transformer

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LV95 → WGS84 transformer (singleton, thread-safe)
# ---------------------------------------------------------------------------
_lv95_to_wgs84 = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=False)


def _lv95_to_latlon(easting: float, northing: float) -> tuple[float, float]:
    """Convert Swiss LV95 (E, N) to (lat, lon) in WGS84."""
    lat, lon = _lv95_to_wgs84.transform(easting, northing)
    return float(lat), float(lon)


def _is_lv95(x: float, y: float) -> bool:
    """Heuristic: LV95 eastings start at ~2,480,000 m."""
    return x > 100_000 or y > 100_000


# ---------------------------------------------------------------------------
# Endpoint configuration
# ---------------------------------------------------------------------------
_BASE = "https://data.geo.admin.ch"

# MeteoSwiss uses 99999 (and variants) as a sentinel for missing data.
_SENTINEL = 9999.0

# Note: wind_direction is not a separate endpoint — it is embedded as
# properties["wind_direction"] in every wind_speed feature (confirmed from
# winds-mobi/winds-mobi-providers meteoswiss.py).
_ENDPOINTS: dict[str, str] = {
    "wind_speed":      f"{_BASE}/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min_en.json",
    "wind_gust":       f"{_BASE}/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min_en.json",
    "temperature":     f"{_BASE}/ch.meteoschweiz.messwerte-lufttemperatur-10min/ch.meteoschweiz.messwerte-lufttemperatur-10min_en.json",
    "humidity":        f"{_BASE}/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min_en.json",
    "pressure_qff":    f"{_BASE}/ch.meteoschweiz.messwerte-luftdruck-qff-10min/ch.meteoschweiz.messwerte-luftdruck-qff-10min_en.json",
    "pressure_qfe":    f"{_BASE}/ch.meteoschweiz.messwerte-luftdruck-qfe-10min/ch.meteoschweiz.messwerte-luftdruck-qfe-10min_en.json",
    "pressure_qnh":    f"{_BASE}/ch.meteoschweiz.messwerte-luftdruck-qnh-10min/ch.meteoschweiz.messwerte-luftdruck-qnh-10min_en.json",
    "precipitation":   f"{_BASE}/ch.meteoschweiz.messwerte-niederschlag-10min/ch.meteoschweiz.messwerte-niederschlag-10min_en.json",
}


# ---------------------------------------------------------------------------
# Collector implementation
# ---------------------------------------------------------------------------

class MeteoSwissCollector(BaseCollector):
    """
    Pulls the latest 10-minute observation data from MeteoSwiss's free
    GeoJSON API and returns normalised ``WeatherMeasurement`` objects.
    """

    NETWORK = "meteoswiss"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        # Allow individual endpoint overrides from config
        self._endpoints: dict[str, str] = {
            **_ENDPOINTS,
            **(config.get("endpoints", {}) if config else {}),
        }
        # Internal cache: station_id → WeatherStation (populated on first call)
        self._station_cache: dict[str, WeatherStation] = {}

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def _parse_station(self, feature: dict) -> Optional[WeatherStation]:
        """Build a ``WeatherStation`` from a single GeoJSON feature, or return
        ``None`` if the feature lacks a usable station ID or coordinates."""
        props = feature.get("properties", {})
        raw_id: str = (
            feature.get("id")
            or props.get("station_id")
            or props.get("nat_abbr")
            or ""
        )
        if not raw_id:
            return None

        lat, lon, elev = _extract_coords(feature)
        if lat == 0.0 and lon == 0.0:
            return None

        name: str = props.get("station_name") or props.get("name") or str(raw_id)
        canton: Optional[str] = props.get("canton") or props.get("canton_name")
        sid = self.station_id(self.NETWORK, raw_id)

        return WeatherStation(
            station_id=sid,
            name=name,
            network=self.NETWORK,
            latitude=lat,
            longitude=lon,
            elevation=elev,
            canton=canton,
        )

    async def get_stations(self) -> list[WeatherStation]:
        """
        Derive station metadata from the wind-speed endpoint (the most
        complete one — all ~160 SMN stations appear there).
        """
        url = self._endpoints["wind_speed"]
        try:
            data = await self._get(url)
        except Exception:
            logger.warning("Could not fetch station list from MeteoSwiss")
            return []

        stations: list[WeatherStation] = []
        for feature in data.get("features", []):
            station = self._parse_station(feature)
            if station:
                stations.append(station)
                self._station_cache[station.station_id] = station

        logger.info("MeteoSwiss: discovered %d stations", len(stations))
        return stations

    async def collect(self) -> list[WeatherMeasurement]:
        """
        Fetch all observation endpoints, merge by station ID, and return
        one ``WeatherMeasurement`` per station.
        """
        # Accumulate raw values: station_id → {field → value}
        raw: dict[str, dict] = {}
        # Also track timestamps and station metadata
        station_meta: dict[str, tuple[str, float, float, Optional[int]]] = {}  # id → (name, lat, lon, elevation)
        timestamps: dict[str, datetime] = {}

        for field_name, url in self._endpoints.items():
            try:
                data = await self._get(url)
            except Exception as exc:
                logger.warning("MeteoSwiss endpoint '%s' failed: %s", field_name, exc)
                continue

            for feature in data.get("features", []):
                props = feature.get("properties", {})
                # Station ID is the GeoJSON feature id or properties.station_id
                raw_id: str = (
                    feature.get("id")
                    or props.get("station_id")
                    or props.get("nat_abbr")
                    or ""
                )
                if not raw_id:
                    continue

                sid = self.station_id(self.NETWORK, raw_id)

                # wind_direction is embedded in every wind_speed feature
                # (mirrors winds-mobi/winds-mobi-providers meteoswiss.py)
                if field_name == "wind_speed":
                    raw_dir = props.get("wind_direction")
                    if raw_dir is not None and raw_dir != "-":
                        if sid not in raw:
                            raw[sid] = {}
                        try:
                            dir_val = float(str(raw_dir))
                            if dir_val < _SENTINEL:
                                raw[sid]["wind_direction"] = int(dir_val)
                        except (ValueError, TypeError):
                            pass

                # Parse value
                value = props.get("value")
                if value is None or value == "-":
                    continue
                try:
                    parsed_value = float(value)
                except (ValueError, TypeError):
                    continue
                if parsed_value >= _SENTINEL:
                    continue

                # Parse timestamp — winds-mobi uses reference_ts (ISO) as the canonical field
                ts = _parse_timestamp(props.get("reference_ts") or props.get("date") or "")
                if ts and sid not in timestamps:
                    timestamps[sid] = ts

                # Station geometry for metadata cache
                if sid not in station_meta:
                    lat, lon, elev = _extract_coords(feature)
                    name = props.get("station_name") or props.get("name") or raw_id
                    station_meta[sid] = (name, lat, lon, elev)
                    canton = props.get("canton") or props.get("canton_name")
                    if sid not in self._station_cache and lat != 0.0:
                        self._station_cache[sid] = WeatherStation(
                            station_id=sid,
                            name=name,
                            network=self.NETWORK,
                            latitude=lat,
                            longitude=lon,
                            elevation=elev,
                            canton=canton,
                        )

                if sid not in raw:
                    raw[sid] = {}
                raw[sid][field_name] = parsed_value

        # Build WeatherMeasurement objects
        measurements: list[WeatherMeasurement] = []
        now_utc = datetime.now(timezone.utc)

        for sid, fields in raw.items():
            ts = timestamps.get(sid, now_utc)
            wind_dir = fields.get("wind_direction")

            measurements.append(
                WeatherMeasurement(
                    station_id=sid,
                    network=self.NETWORK,
                    timestamp=ts,
                    wind_speed=fields.get("wind_speed"),
                    wind_direction=int(wind_dir) if wind_dir is not None else None,
                    wind_gust=fields.get("wind_gust"),
                    temperature=fields.get("temperature"),
                    humidity=fields.get("humidity"),
                    pressure_qfe=fields.get("pressure_qfe"),
                    pressure_qnh=fields.get("pressure_qnh"),
                    pressure_qff=fields.get("pressure_qff"),
                    precipitation=fields.get("precipitation"),
                    snow_depth=None,  # Not provided by MeteoSwiss 10-min endpoints
                )
            )

        logger.info(
            "MeteoSwiss: collected %d measurements from %d stations",
            len(measurements),
            len(raw),
        )
        return measurements


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_timestamp(raw: str) -> Optional[datetime]:
    """Parse a MeteoSwiss date string (e.g. '202603081000') to UTC datetime."""
    if not raw:
        return None
    raw = str(raw).strip()
    # Format: YYYYMMDDHHmm  (12 chars)
    if len(raw) == 12 and raw.isdigit():
        try:
            return datetime(
                int(raw[0:4]),
                int(raw[4:6]),
                int(raw[6:8]),
                int(raw[8:10]),
                int(raw[10:12]),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    # ISO 8601 fallback
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    return None


def _extract_coords(feature: dict) -> tuple[float, float, Optional[int]]:
    """
    Extract (latitude, longitude, elevation) from a GeoJSON feature.
    Handles both WGS84 and LV95 coordinates.
    """
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates", [])
    props = feature.get("properties", {})

    if len(coords) < 2:
        return 0.0, 0.0, None

    x, y = float(coords[0]), float(coords[1])

    def _to_int_elev(raw) -> Optional[int]:
        """Parse elevation from int, float, or string like '1538.86 m'."""
        if raw is None:
            return None
        # Strip unit suffixes (e.g. '1538.86 m' → '1538.86')
        s = str(raw).split()[0]
        try:
            return int(float(s)) or None
        except (ValueError, TypeError):
            return None

    elev: Optional[int] = _to_int_elev(coords[2]) if len(coords) > 2 else (
        _to_int_elev(props.get("altitude") or props.get("elevation"))
    )

    if _is_lv95(x, y):
        lat, lon = _lv95_to_latlon(x, y)
    else:
        # Already WGS84: GeoJSON convention is [lon, lat]
        lon, lat = x, y

    return lat, lon, elev
