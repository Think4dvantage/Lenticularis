"""
Abstract base class for forecast data collectors.

Each forecast source (Open-Meteo, MeteoSwiss STAC/GRIB2, etc.) subclasses
this and implements ``collect_for_station()`` to fetch, normalise, and return
forecast data as ForecastPoint objects ready for InfluxDB writes.

Design
------
- ``SOURCE`` / ``MODEL`` class constants identify the data origin in InfluxDB tags.
- ``collect_for_station()`` is the per-station entry point; subclasses implement this.
- ``collect_all()`` iterates over a list of stations and aggregates results;
  subclasses that support batch APIs can override it for efficiency.
- HTTP helpers (``_get``, ``_ensure_client``, ``close``) mirror BaseCollector.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from lenticularis.models.weather import ForecastPoint, WeatherStation


class BaseForecastCollector(ABC):
    """Abstract base for all forecast collectors."""

    SOURCE: str = ""   # e.g. "open-meteo"
    MODEL: str = ""    # e.g. "icon-seamless"

    def __init__(
        self,
        config: Optional[dict] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config: dict = config or {}
        self.logger: logging.Logger = logger or logging.getLogger(self.__class__.__name__)
        self._http_client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def collect_for_station(
        self,
        station_id: str,
        network: str,
        lat: float,
        lon: float,
        horizon_hours: int,
    ) -> list[ForecastPoint]:
        """
        Fetch forecast for one station and return normalised ForecastPoints.

        ``horizon_hours`` defines how many hours ahead to collect (e.g. 120).
        """

    # ------------------------------------------------------------------
    # Default batch implementation — subclasses may override for efficiency
    # ------------------------------------------------------------------

    async def collect_all(
        self,
        stations: list[WeatherStation],
        horizon_hours: int = 120,
        spread_seconds: float = 0.0,
    ) -> list[ForecastPoint]:
        """
        Collect forecast for every station in the list.

        Falls back to calling ``collect_for_station`` per station. Subclasses
        that support batch HTTP requests can override this for better performance.
        Stations without lat/lon are silently skipped.

        ``spread_seconds`` distributes requests evenly over a time window instead
        of firing them all at once.  When > 0, the method sleeps
        ``spread_seconds / N`` between each station fetch (where N is the number
        of eligible stations).  Pass the collector's interval duration in seconds
        to avoid rate-limit bursts — e.g. a 60-minute interval with 50 stations
        yields a ~72 s gap between requests.
        """
        points: list[ForecastPoint] = []
        async for _station, pts in self.collect_all_iter(stations, horizon_hours, spread_seconds):
            points.extend(pts)
        return points

    async def collect_all_iter(
        self,
        stations: list[WeatherStation],
        horizon_hours: int = 120,
        spread_seconds: float = 0.0,
        concurrency: int = 1,
    ):
        """
        Fetch forecasts serially (concurrency=1 by default).

        Open-Meteo free tier rate-limits burst connections with HTTP 429.
        Serial fetching (one request at a time) with the ~7 s API response
        latency gives ~0.14 req/s — well within free-tier limits.  475 stations
        complete in ~55 min, safely inside the 60-min collection interval.

        ``spread_seconds`` is accepted for API compatibility but ignored.

        On per-station errors, yields ``(station, [])`` and logs the exception.
        """
        eligible = [
            s for s in stations
            if s.latitude is not None and s.longitude is not None
        ]
        if not eligible:
            return

        async def _fetch(station: WeatherStation):
            try:
                pts = await self.collect_for_station(
                    station.station_id,
                    station.network,
                    station.latitude,
                    station.longitude,
                    horizon_hours,
                )
                return station, pts
            except Exception as exc:
                self.logger.error(
                    "Forecast collection failed for %s: %s", station.station_id, exc
                )
                return station, []

        # Serial processing — one station at a time.  The API response latency
        # (~7 s) already limits throughput to ~8 req/min without extra sleep,
        # keeping us well within Open-Meteo's free-tier rate limits.
        for station in eligible:
            result = await _fetch(station)
            yield result

    # ------------------------------------------------------------------
    # Shared HTTP helpers (mirrors BaseCollector)
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> None:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": "lenticularis/1.4 (https://lenti.cloud)"},
            )

    async def _get(self, url: str, params: Optional[dict] = None) -> dict:
        await self._ensure_client()
        assert self._http_client is not None
        _429_delays = [10, 30, 60]  # seconds to wait before each retry on 429
        attempt = 0
        while True:
            try:
                response = await self._http_client.get(url, params=params)
                if response.status_code == 429 and attempt < len(_429_delays):
                    delay = _429_delays[attempt]
                    self.logger.warning(
                        "HTTP 429 fetching %s — retry %d/%d in %ds",
                        url, attempt + 1, len(_429_delays), delay,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                self.logger.error("Timeout fetching %s: %s", url, exc)
                raise
            except httpx.HTTPStatusError as exc:
                self.logger.error("HTTP %s fetching %s: %s", exc.response.status_code, url, exc)
                raise
            except httpx.HTTPError as exc:
                self.logger.error("HTTP error fetching %s: %s", url, exc)
                raise

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
            self.logger.debug("HTTP client closed for %s", self.__class__.__name__)
