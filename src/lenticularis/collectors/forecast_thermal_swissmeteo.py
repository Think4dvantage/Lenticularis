"""
SwissMeteo thermal forecast collector.

Fetches the ICON-CH1/CH2 thermal grid from lsmfapi — solar radiation, sunshine
duration, cloud cover (total/low/mid/high), freezing level, CAPE/CIN, LCL, LFC,
TKE, plus a deliberate subset of ensemble spread — for every ~1 km grid cell
covering Switzerland. ONE request covers every station and the whole map
(28.6 MB / 4.1 s at the default stride_km=10); this collector maps that
response onto Lenticularis stations by nearest grid point and computes the
derived thermal metrics (``services/thermal.py``).

Not a ``BaseForecastCollector`` subclass: that ABC is per-station
(``collect_for_station``), and this endpoint is one spatial request for
everything — same reasoning as ``ForecastGridSwissMeteoCollector``.

See ``specs/006-thermal-forecast/plan.md`` §7.1 for the full design.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from lenticularis.models.weather import ThermalForecastPoint, WeatherStation
from lenticularis.services import thermal as thermal_metrics
from lenticularis.services.dedup import haversine_m

logger = logging.getLogger(__name__)

_ZURICH_TZ = ZoneInfo("Europe/Zurich")

# The 12 raw ensemble-median fields lsmfapi publishes per frame.
_RAW_FIELDS = (
    "solar", "sunshine", "cloud_cover", "cloud_low", "cloud_mid", "cloud_high",
    "freezing_level", "cape", "cin", "lcl", "lfc", "tke",
)
# The deliberate 5-field ensemble-spread subset stored (plan.md §5.1).
_SPREAD_FIELDS = ("lcl_min", "lcl_max", "cape_max", "cloud_cover_max", "solar_min")

# Phase 1 exit gate (plan.md §10.1) — local hours a pilot actually flies.
_COVERAGE_HOUR_START = 8
_COVERAGE_HOUR_END = 19


def _at(frame: dict, field: str, j: int) -> Optional[float]:
    """Null-safe indexed lookup into one of a frame's parallel field arrays."""
    arr = frame.get(field)
    if not arr or j >= len(arr):
        return None
    v = arr[j]
    return float(v) if v is not None else None


def _coverage_by_local_day(frames: list[dict]) -> dict[str, int]:
    """Count, for today and D+1 (Europe/Zurich local dates), how many of local
    hours 08-19 have at least one non-null ``solar`` reading anywhere in the grid.

    This is the Phase 1 exit gate, not decoration: lsmfapi is known to null
    entire frames mid-horizon (plan.md §3.2), and those frames land squarely on
    today's and tomorrow's flyable hours. A row-count check cannot see this —
    rows get written either way — so the gate has to be measured per hour.
    """
    now_local = datetime.now(timezone.utc).astimezone(_ZURICH_TZ)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)
    counts = {"today": 0, "d1": 0}
    for frame in frames:
        vt_str = frame.get("valid_time")
        if not vt_str:
            continue
        vt_local = datetime.fromisoformat(vt_str.replace("Z", "+00:00")).astimezone(_ZURICH_TZ)
        if not (_COVERAGE_HOUR_START <= vt_local.hour <= _COVERAGE_HOUR_END):
            continue
        solar_arr = frame.get("solar") or []
        if not any(v is not None for v in solar_arr):
            continue
        if vt_local.date() == today:
            counts["today"] += 1
        elif vt_local.date() == tomorrow:
            counts["d1"] += 1
    return counts


class ForecastThermalSwissMeteoCollector:
    """Collects the thermal grid from lsmfapi and maps it onto Lenticularis stations."""

    SOURCE = "swissmeteo"
    MODEL = "icon-ch"

    def __init__(self, base_url: str = "https://lsmfapi-dev.lg4.ch") -> None:
        self._base_url = base_url.rstrip("/")
        self._http_client: Optional[httpx.AsyncClient] = None
        # Populated by build_station_points — read by the scheduler for the
        # re-collection guard (plan.md §7.1 note 7).
        self.last_init_time: Optional[datetime] = None
        self.last_model: Optional[str] = None
        self.last_usable_frame_count: Optional[int] = None

    async def _ensure_client(self) -> None:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=300.0,
                headers={
                    "User-Agent": "lenticularis/1.4 (https://lenti.cloud)",
                    "Accept-Encoding": "gzip",
                },
            )

    async def fetch(self) -> Optional[dict]:
        """ONE request for the whole thermal grid.

        Returns the parsed payload, or ``None`` on 503 ``cache_warming`` (logged at
        WARNING, no raise — a warming upstream is expected, not exceptional).
        """
        await self._ensure_client()
        assert self._http_client is not None
        url = f"{self._base_url}/api/forecast/thermal-grid"
        try:
            response = await self._http_client.get(url)
            if response.status_code == 503:
                logger.warning(
                    "[Lenti:thermal-collector] lsmfapi cache_warming — will retry next run"
                )
                return None
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error("[Lenti:thermal-collector] Timeout fetching thermal grid: %s", exc)
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[Lenti:thermal-collector] HTTP %s fetching thermal grid: %s",
                exc.response.status_code, exc,
            )
            raise
        except httpx.HTTPError as exc:
            logger.error("[Lenti:thermal-collector] HTTP error fetching thermal grid: %s", exc)
            raise

        # 28.6 MB of JSON — parsing it is blocking work (04-constraints.md).
        return await asyncio.to_thread(response.json)

    def build_station_points(
        self, payload: dict, stations: list[WeatherStation],
    ) -> list[ThermalForecastPoint]:
        """Nearest grid point per station, then derive. Pure/sync — call via ``asyncio.to_thread``."""
        init_time_str = payload.get("init_time")
        if not init_time_str:
            logger.warning("[Lenti:thermal-collector] No init_time in thermal-grid response")
            return []
        init_time = datetime.fromisoformat(init_time_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        model = payload.get("model", self.MODEL)
        grid = payload.get("grid", [])
        frames = payload.get("frames", [])

        if not grid or not frames:
            logger.warning("[Lenti:thermal-collector] Empty grid or frames in thermal-grid response")
            return []

        stations_with_coords = [
            s for s in stations
            if getattr(s, "latitude", None) is not None and getattr(s, "longitude", None) is not None
        ]

        # Nearest grid point per station, computed once per run —
        # len(stations_with_coords) x len(grid) evaluations, negligible next to the fetch.
        station_grid_index: dict[str, int] = {}
        for s in stations_with_coords:
            best_j = 0
            best_dist = float("inf")
            for j, pt in enumerate(grid):
                d = haversine_m(s.latitude, s.longitude, pt["lat"], pt["lon"])
                if d < best_dist:
                    best_dist = d
                    best_j = j
            station_grid_index[s.station_id] = best_j

        points: list[ThermalForecastPoint] = []
        usable_frames = 0

        for frame in frames:
            vt_str = frame.get("valid_time")
            if not vt_str:
                continue
            valid_time = datetime.fromisoformat(vt_str.replace("Z", "+00:00")).astimezone(timezone.utc)

            frame_has_data = False

            for s in stations_with_coords:
                j = station_grid_index[s.station_id]
                raw = {f: _at(frame, f, j) for f in _RAW_FIELDS}
                if all(v is None for v in raw.values()):
                    # All-null frame for this point — emit nothing (plan.md §3.2 rule 5).
                    continue
                frame_has_data = True

                spread = {f: _at(frame, f, j) for f in _SPREAD_FIELDS}
                elevation_m = float(s.elevation) if getattr(s, "elevation", None) is not None else None

                points.append(ThermalForecastPoint(
                    station_id=s.station_id,
                    network=s.network,
                    source=self.SOURCE,
                    model=model,
                    init_time=init_time,
                    valid_time=valid_time,
                    solar=raw["solar"],
                    sunshine=raw["sunshine"],
                    cloud_cover=raw["cloud_cover"],
                    cloud_low=raw["cloud_low"],
                    cloud_mid=raw["cloud_mid"],
                    cloud_high=raw["cloud_high"],
                    freezing_level=raw["freezing_level"],
                    cape=raw["cape"],
                    cin=raw["cin"],
                    lcl=raw["lcl"],
                    lfc=raw["lfc"],
                    tke=raw["tke"],
                    lcl_min=spread["lcl_min"],
                    lcl_max=spread["lcl_max"],
                    cape_max=spread["cape_max"],
                    cloud_cover_max=spread["cloud_cover_max"],
                    solar_min=spread["solar_min"],
                    thermal_ceiling_m=thermal_metrics.thermal_ceiling_m(raw["lcl"], raw["freezing_level"]),
                    cloud_base_agl_m=thermal_metrics.cloud_base_agl_m(raw["lcl"], elevation_m),
                    thermal_strength=thermal_metrics.thermal_strength(
                        raw["solar"], raw["cape"], raw["cloud_cover"], raw["cin"], raw["sunshine"],
                    ),
                    overdevelopment_risk=thermal_metrics.overdevelopment_risk(
                        raw["cape"], raw["cin"], raw["cloud_mid"],
                    ),
                    blue_thermal=thermal_metrics.blue_thermal(raw["lfc"], raw["lcl"]),
                    turbulence_index=thermal_metrics.turbulence_index(raw["tke"]),
                    ceiling_spread_m=thermal_metrics.ceiling_spread_m(spread["lcl_min"], spread["lcl_max"]),
                ))

            if frame_has_data:
                usable_frames += 1

        coverage = _coverage_by_local_day(frames)
        logger.info(
            "[Lenti:thermal-collector] init=%s model=%s frames=%d usable=%d points=%d",
            init_time_str, model, len(frames), usable_frames, len(points),
        )
        logger.info(
            "[Lenti:thermal-collector] coverage today=%d/12 d1=%d/12 (local 08-19, non-null solar)",
            coverage["today"], coverage["d1"],
        )

        self.last_init_time = init_time
        self.last_model = model
        self.last_usable_frame_count = usable_frames

        return points

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
