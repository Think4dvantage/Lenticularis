"""
Fluggruppe Aletsch (FGA) weather station collector for Lenticularis.

Fetches live measurements from 9 stations in the Valais region operated by
Meteo Oberwallis.  Data is served as plain XML — no authentication required.

Endpoint pattern:
  GET https://meteo-oberwallis.ch/wetter/{path}/daten.xml

XML structure:
  <response>
    <station id="Wetterstation">
      <time><date_time value="D.MM.YYYY H:mm:ss"/></time>
      <elevation><elevation value="1060"/></elevation>
      <station>
        <station value="Display Name"/>
        <station_longitude value="008° 08.21' O"/>
        <station_latitude value="46° 24.57' N"/>
      </station>
      <wind><speed value="..."/><direction_wind value="..."/></wind>
      <gust><gust value="..."/></gust>
      <temperature><temperature value="..."/></temperature>
      <humidity><humidity value="..."/></humidity>
      <pressure><pressure value="..."/></pressure>
      <precipitation><rain value="..."/></precipitation>
    </station>
  </response>

Station types:
  Type 1 — coordinates embedded in XML (DMS format, degrees + decimal minutes).
  Type 2 — coordinates hardcoded; gust field is ./gust/gust_1h_max.

Note: the ``bitsch`` station is IP-restricted and is therefore excluded.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from lenticularis.collectors.base import BaseCollector
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_BASE_URL = "https://meteo-oberwallis.ch/wetter/{path}/daten.xml"
_ZURICH_TZ = ZoneInfo("Europe/Zurich")

# DMS regex — handles formats like:  46° 24.57' N   008° 08.21' O
# Supports decimal minutes (no seconds), German O = Ost (East)
_DMS_RE = re.compile(
    r"""(\d+)°\s*([\d.]+)['\u2019]\s*(?:([\d.]+)["\u201d])?\s*([NSEWOo])""",
    re.IGNORECASE,
)


def _dms_to_decimal(text: str) -> Optional[float]:
    """Convert a DMS (or DM.m) coordinate string to a decimal degree float."""
    m = _DMS_RE.search(text)
    if not m:
        return None
    degrees = float(m.group(1))
    minutes = float(m.group(2))
    seconds = float(m.group(3)) if m.group(3) else 0.0
    direction = m.group(4).upper()
    dd = degrees + minutes / 60.0 + seconds / 3600.0
    if direction in ("S", "W"):
        dd = -dd
    # O = Ost (East) → positive longitude, no sign change needed
    return dd


def _attr(element: Optional[ET.Element], path: str) -> Optional[str]:
    """Extract the ``value`` attribute from a sub-element found at ``path``."""
    if element is None:
        return None
    el = element.find(path)
    if el is None:
        return None
    return el.get("value")


def _to_float(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    raw = raw.strip().rstrip("°").strip()
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    """Parse ``D.MM.YYYY H:mm:ss`` (Europe/Zurich) → UTC datetime."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw.strip(), "%d.%m.%Y %H:%M:%S")
        dt = dt.replace(tzinfo=_ZURICH_TZ)
        return dt.astimezone(timezone.utc)
    except ValueError:
        logger.debug("FGA: could not parse timestamp %r", raw)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Station definitions
# ─────────────────────────────────────────────────────────────────────────────

# type=1  → URL path includes /XML subdir; coordinates extracted from XML (DMS)
# type=2  → URL path is direct; coordinates hardcoded; gust field differs
#
# ``url_path`` is used in the URL: .../wetter/{url_path}/daten.xml
# ``id``       is used as the station identifier (network-namespaced)
_STATIONS: list[dict] = [
    {"url_path": "fieschertal/XML",  "id": "fieschertal",  "name": "Fieschertal",      "type": 1},
    {"url_path": "fleschen/XML",     "id": "fleschen",     "name": "Bellwald Fleschen", "type": 1},
    {"url_path": "ried-brig/XML",    "id": "ried-brig",    "name": "Ried-Brig",         "type": 1},
    {"url_path": "chaeserstatt/XML", "id": "chaeserstatt", "name": "Chaeserstatt",      "type": 1},
    {"url_path": "jeizinen/XML",     "id": "jeizinen",     "name": "Jeizinen",          "type": 1},
    {"url_path": "grimselpass/XML",  "id": "grimselpass",  "name": "Grimselpass",       "type": 1},
    {"url_path": "hohbiel/XML",      "id": "hohbiel",      "name": "Hohbiel",           "type": 1},
    # Type 2 — fixed coordinates
    {"url_path": "rothorli",         "id": "rothorli",     "name": "Rothorli",          "type": 2, "lat": 46.2497, "lon": 7.938},
    {"url_path": "klaena",           "id": "klaena",       "name": "Klaena",            "type": 2, "lat": 46.3135, "lon": 8.0632},
]


class FgaCollector(BaseCollector):
    """Collect live measurements from Fluggruppe Aletsch / Meteo Oberwallis stations."""

    NETWORK = "fga"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self._station_cache: dict[str, WeatherStation] = {}

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def get_stations(self) -> list[WeatherStation]:
        if not self._station_cache:
            await self.collect()
        return list(self._station_cache.values())

    async def collect(self) -> list[WeatherMeasurement]:
        await self._ensure_client()
        measurements: list[WeatherMeasurement] = []

        for station_def in _STATIONS:
            url_path = station_def["url_path"]
            url = _BASE_URL.format(path=url_path)
            try:
                m = await self._fetch_station(url, station_def)
                if m is not None:
                    measurements.append(m)
            except Exception as exc:
                logger.error("FGA: failed to collect %s: %s", url_path, exc)

        logger.info("FGA: collected %d measurements", len(measurements))
        return measurements

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_station(
        self, url: str, station_def: dict
    ) -> Optional[WeatherMeasurement]:
        assert self._http_client is not None

        try:
            response = await self._http_client.get(url)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("FGA: HTTP error for %s: %s", url, exc)
            return None

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            logger.warning("FGA: XML parse error for %s: %s", url, exc)
            return None

        return self._parse_station(root, station_def)

    def _parse_station(
        self, root: ET.Element, station_def: dict
    ) -> Optional[WeatherMeasurement]:
        station_id_key = station_def["id"]
        stype = station_def["type"]
        sid = self.station_id(self.NETWORK, station_id_key)

        # XML root is <response>; all data lives under the first <station> child.
        data = root.find("./station") or root

        # ── Coordinates ────────────────────────────────────────────────
        if stype == 1:
            # Nested under a second <station> block within data:
            #   <station><station_latitude value="46° 24.57' N"/></station>
            lat_raw = _attr(data, "./station/station_latitude")
            lon_raw = _attr(data, "./station/station_longitude")
            lat = _dms_to_decimal(lat_raw) if lat_raw else None
            lon = _dms_to_decimal(lon_raw) if lon_raw else None
            if lat is None or lon is None:
                logger.warning(
                    "FGA: %s — could not extract coordinates (lat_raw=%r, lon_raw=%r)",
                    station_id_key, lat_raw, lon_raw,
                )
        else:
            lat = station_def.get("lat")
            lon = station_def.get("lon")

        # ── Elevation ──────────────────────────────────────────────────
        elev_raw = _attr(data, "./elevation/elevation")
        elevation: Optional[int] = None
        if elev_raw is not None:
            try:
                elevation = int(float(elev_raw))
            except (ValueError, TypeError):
                pass

        # ── Station name from XML (override if available) ───────────────
        station_name_raw = _attr(data, "./station/station")
        display_name = station_name_raw.strip() if station_name_raw else station_def["name"]

        # ── Cache station metadata ──────────────────────────────────────
        if lat is not None and lon is not None and sid not in self._station_cache:
            self._station_cache[sid] = WeatherStation(
                station_id=sid,
                name=display_name,
                network=self.NETWORK,
                latitude=lat,
                longitude=lon,
                elevation=elevation,
                canton=None,
            )

        # ── Timestamp ──────────────────────────────────────────────────
        ts_raw = _attr(data, "./time/date_time")
        ts = _parse_timestamp(ts_raw)
        if ts is None:
            ts = datetime.now(timezone.utc)
        else:
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_hours > 2:
                logger.warning(
                    "FGA: %s — stale data (timestamp %s, %.1fh old), skipping",
                    station_id_key, ts.isoformat(), age_hours,
                )
                return None

        # ── Wind direction ─────────────────────────────────────────────
        dir_raw = _attr(data, "./wind/direction_wind")
        wind_direction: Optional[int] = None
        if dir_raw is not None:
            try:
                wind_direction = int(float(dir_raw.strip().rstrip("°"))) % 360
            except (ValueError, TypeError):
                pass

        # ── Wind speed ─────────────────────────────────────────────────
        wind_speed = _to_float(_attr(data, "./wind/speed"))

        # ── Wind gust ──────────────────────────────────────────────────
        if stype == 1:
            wind_gust = _to_float(_attr(data, "./gust/gust"))
        else:
            wind_gust = _to_float(_attr(data, "./gust/gust_1h_max"))

        # ── Temperature ────────────────────────────────────────────────
        temperature = _to_float(_attr(data, "./temperature/temperature"))

        # ── Humidity ───────────────────────────────────────────────────
        humidity = _to_float(_attr(data, "./humidity/humidity"))

        # ── Pressure (surface QFE) ─────────────────────────────────────
        pressure_qfe = _to_float(_attr(data, "./pressure/pressure"))

        # ── Precipitation ──────────────────────────────────────────────
        precipitation = _to_float(_attr(data, "./precipitation/rain"))

        logger.debug(
            "FGA: %s ts=%s spd=%s dir=%s gust=%s temp=%s",
            station_id_key, ts, wind_speed, wind_direction, wind_gust, temperature,
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
            pressure_qfe=pressure_qfe,
            precipitation=precipitation,
            snow_depth=None,
        )
