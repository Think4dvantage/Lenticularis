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
    ) -> list[ForecastPoint]:
        """
        Collect forecast for every station in the list.

        Falls back to calling ``collect_for_station`` per station. Subclasses
        that support batch HTTP requests can override this for better performance.
        Stations without lat/lon are silently skipped.
        """
        points: list[ForecastPoint] = []
        for station in stations:
            if station.latitude is None or station.longitude is None:
                continue
            try:
                pts = await self.collect_for_station(
                    station.station_id,
                    station.network,
                    station.latitude,
                    station.longitude,
                    horizon_hours,
                )
                points.extend(pts)
            except Exception as exc:
                self.logger.error(
                    "Forecast collection failed for %s: %s", station.station_id, exc
                )
        return points

    # ------------------------------------------------------------------
    # Shared HTTP helpers (mirrors BaseCollector)
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> None:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)

    async def _get(self, url: str, params: Optional[dict] = None) -> dict:
        await self._ensure_client()
        assert self._http_client is not None
        try:
            response = await self._http_client.get(url, params=params)
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
