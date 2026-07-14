"""
Jungfraubahn (JFB) weather station collector for Lenticularis.

Fetches live measurements for the Jungfrau region from the Jungfraubahn middleware
API.  Data is served as JSON — no authentication required.  A single request returns
every station, so one HTTP call per collection cycle is enough.

Endpoint:
  GET {url}?currentDateTime=YYYY-MM-DDTHH:MM

Response structure:
  {
    "meta": {"observationParameters": [{"paramKey": "TL", "paramUnit": "°C", ...}, ...]},
    "observationsByStation": [
      {
        "station": {"name": "Lauberhorn", "lat": 46.585, "lon": 7.95, "elevation": 2315},
        "observations": {
          "FF":  {"value": "14",    "timeUTC": "11:30"},
          "DIR": {"value": "230",   "timeUTC": "11:30"},
          ...
        }
      },
      ...
    ]
  }

Parameters use MeteoSwiss SwissMetNet codes.  Only those carrying information the
unified ``WeatherMeasurement`` schema does not already hold are mapped:

  FF   wind speed, 10-min mean  (knots) → wind_speed      (km/h)
  G10  max gust over 10 min     (knots) → wind_gust       (km/h)
  DIR  wind direction           (deg)   → wind_direction
  TL   temperature 2 m          (°C)    → temperature
  RH   relative humidity        (%)     → humidity
  QFE  station-level pressure   (hPa)   → pressure_qfe

Deliberately ignored:
  TD / DIFFTD  dewpoint and temperature−dewpoint spread — both derivable from TL + RH.
  G1h          1-hour max gust — different semantics from wind_gust (10-min peak), which
               is the convention used by meteoswiss/fga/holfuy.

``pressure_qff`` is left None: JFB reports QFE only, and QFF is not derivable from QFE
and station height (that is QNH).  QFF is the meteorological reduction using the actual
temperature and humidity of the air column, so synthesising it would inject an error of
several hPa at these altitudes — larger than the föhn pressure gradients it would be
compared against.  fga.py leaves QFF None for the same reason.

Two stations are excluded as exact duplicates of MeteoSwiss stations already collected
(see ``_EXCLUDED_STATIONS``).

Quirks handled here:
  - ``currentDateTime`` must be sent.  Without it the API replies with observations that
    are several hours stale.
  - ``timeUTC`` carries a time but no date; the date is reconstructed, with rollover.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from lenticularis.collectors.base import BaseCollector
from lenticularis.collectors.utils import normalize_wind_dir, to_float
from lenticularis.models.weather import WeatherMeasurement, WeatherStation

logger = logging.getLogger(__name__)

_DEFAULT_URL = (
    "https://jfbmdlw-production.apps-customer.410400260094.ninegcp.ch"
    "/api/weather/v01/observations/current"
)

KNOTS_TO_KMH = 1.852

# Skip data older than this — a stale reading must never feed a traffic-light decision.
_MAX_AGE_HOURS = 2.0

# Exact duplicates of MeteoSwiss stations Lenticularis already collects, at strictly
# lower quality (fewer fields, coordinates rounded to 2–3 decimals).
_EXCLUDED_STATIONS = {
    "Interlaken",           # = meteoswiss-INT
    "Jungfraujoch-Sphinx",  # = meteoswiss-JUN
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    """Build a stable ASCII slug from a station name: ``Wengen-Lauberhorn (Ziel)`` →
    ``wengen-lauberhorn-ziel``."""
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return _SLUG_RE.sub("-", ascii_only.lower()).strip("-")


def _reconstruct_timestamp(time_utc: str, now: datetime) -> Optional[datetime]:
    """Turn a bare ``"HH:MM"`` into a full UTC datetime, resolving the date against *now*.

    The API omits the date entirely.  A reading that lands more than
    ``_MAX_AGE_HOURS`` in the future must belong to the previous day — this is the
    midnight rollover case (obs ``23:50`` fetched at ``00:05``).
    """
    try:
        hour, minute = (int(part) for part in time_utc.split(":", 1))
    except (ValueError, AttributeError):
        return None
    ts = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if ts > now + timedelta(hours=_MAX_AGE_HOURS):
        ts -= timedelta(days=1)
    return ts


class JfbCollector(BaseCollector):
    """Collect live measurements from the Jungfraubahn middleware weather API."""

    NETWORK = "jfb"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config=config)
        self._url: str = (self.config or {}).get("url") or _DEFAULT_URL
        self._station_cache: dict[str, WeatherStation] = {}

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    async def get_stations(self) -> list[WeatherStation]:
        if not self._station_cache:
            await self.collect()
        return list(self._station_cache.values())

    async def collect(self) -> list[WeatherMeasurement]:
        now = datetime.now(timezone.utc)
        # Mandatory: without currentDateTime the API returns hours-old observations.
        current_dt = now.strftime("%Y-%m-%dT%H:%M")

        logger.info("JFB: fetching %s (currentDateTime=%s)", self._url, current_dt)
        started = time.monotonic()
        try:
            payload = await self._get(self._url, params={"currentDateTime": current_dt})
        except Exception:
            logger.exception("JFB: fetch failed — url=%s currentDateTime=%s", self._url, current_dt)
            raise
        elapsed_ms = (time.monotonic() - started) * 1000

        rows = payload.get("observationsByStation") or []
        if not rows:
            logger.warning(
                "JFB: response contained no stations — url=%s currentDateTime=%s (%.0f ms)",
                self._url, current_dt, elapsed_ms,
            )
            return []

        measurements: list[WeatherMeasurement] = []
        skipped_excluded = 0

        for row in rows:
            name = (row.get("station") or {}).get("name") or ""
            if name in _EXCLUDED_STATIONS:
                skipped_excluded += 1
                continue
            measurement = self._parse_station(row, name, now)
            if measurement is not None:
                measurements.append(measurement)

        logger.info(
            "JFB: collected %d measurements from %d stations in %.0f ms "
            "(%d skipped as duplicates of MeteoSwiss)",
            len(measurements), len(rows), elapsed_ms, skipped_excluded,
        )
        if not measurements:
            logger.warning("JFB: no usable measurements in a %d-station response", len(rows))
        return measurements

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_station(
        self, row: dict, name: str, now: datetime
    ) -> Optional[WeatherMeasurement]:
        """Map one station row to a measurement, or None if the row is unusable."""
        station = row.get("station") or {}
        obs = row.get("observations") or {}
        sid = self.station_id(self.NETWORK, _slug(name))

        def value(key: str) -> Optional[float]:
            entry = obs.get(key)
            return to_float(entry.get("value")) if isinstance(entry, dict) else None

        knots = value("FF")
        gust_knots = value("G10")
        wind_speed = knots * KNOTS_TO_KMH if knots is not None else None
        wind_gust = gust_knots * KNOTS_TO_KMH if gust_knots is not None else None
        wind_direction = normalize_wind_dir(
            obs["DIR"].get("value") if isinstance(obs.get("DIR"), dict) else None
        )
        temperature = value("TL")
        humidity = value("RH")
        pressure_qfe = value("QFE")

        fields = (wind_speed, wind_gust, wind_direction, temperature, humidity, pressure_qfe)
        if all(f is None for f in fields):
            logger.warning("JFB: %s — no usable fields in response, skipping", name)
            return None

        # Each parameter carries its own observation time; the newest one that belongs to
        # a field we actually keep is the timestamp for this point.
        used_keys = ("FF", "G10", "DIR", "TL", "RH", "QFE")
        stamps = [
            ts
            for key in used_keys
            if isinstance(obs.get(key), dict)
            for ts in (_reconstruct_timestamp(obs[key].get("timeUTC", ""), now),)
            if ts is not None
        ]
        if not stamps:
            logger.warning("JFB: %s — no parsable observation time, skipping", name)
            return None
        ts = max(stamps)

        age_hours = (now - ts).total_seconds() / 3600
        if age_hours > _MAX_AGE_HOURS:
            logger.warning(
                "JFB: %s — stale data (timestamp %s, %.1fh old), skipping",
                name, ts.isoformat(), age_hours,
            )
            return None

        lat = to_float(station.get("lat"))
        lon = to_float(station.get("lon"))
        elevation = station.get("elevation")
        if lat is not None and lon is not None and sid not in self._station_cache:
            self._station_cache[sid] = WeatherStation(
                station_id=sid,
                name=name,
                network=self.NETWORK,
                latitude=lat,
                longitude=lon,
                elevation=int(elevation) if elevation is not None else None,
                canton="BE",
            )

        logger.debug(
            "JFB: %s ts=%s spd=%s dir=%s gust=%s temp=%s rh=%s qfe=%s",
            sid, ts, wind_speed, wind_direction, wind_gust, temperature, humidity, pressure_qfe,
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
            pressure_qff=None,
            precipitation=None,
            snow_depth=None,
        )
