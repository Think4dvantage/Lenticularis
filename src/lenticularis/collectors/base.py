# Import libraries
#   - ABC and abstractmethod from abc module (for abstract base class)
#   - logging module (for error and info logging)
#   - httpx (async HTTP client for API calls)
#   - typing annotations (List, Optional, Dict, Any)
#   - WeatherStation and WeatherMeasurement models from models.weather
from abc import ABC, abstractmethod
import logging
from typing import Optional
import httpx

from lenticularis.models.weather import WeatherStation, WeatherMeasurement


class BaseCollector(ABC):
    """
    Abstract base class for all weather data collectors.

    Each weather network (MeteoSwiss, Holfuy, SLF, …) subclasses this and
    implements ``collect()`` to fetch, normalise, and return weather data in a
    unified format ready for InfluxDB writes.
    """

    # Subclasses set these as class-level constants
    NETWORK: str = ""

    def __init__(self, config: Optional[dict] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config: dict = config or {}
        self.logger: logging.Logger = logger or logging.getLogger(self.__class__.__name__)
        self._http_client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Abstract interface — every collector must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_stations(self) -> list[WeatherStation]:
        """Return station metadata for all stations provided by this network."""

    @abstractmethod
    async def collect(self) -> list[WeatherMeasurement]:
        """
        Fetch latest measurements from the network and return them as a list
        of ``WeatherMeasurement`` Pydantic models.

        The caller (scheduler) is responsible for writing the returned
        measurements to InfluxDB via ``InfluxClient.write_measurements()``.
        """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def station_id(network: str, raw_id: str) -> str:
        """
        Build a globally unique, network-namespaced station ID.

        Example: station_id("meteoswiss", "BAS") → "meteoswiss-BAS"
        """
        return f"{network}-{raw_id}"

    async def _ensure_client(self) -> None:
        """Lazily create the shared ``httpx.AsyncClient``."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)

    async def _get(self, url: str, params: Optional[dict] = None) -> dict:
        """
        Perform a GET request and return the parsed JSON body.

        Logs and re-raises ``httpx.HTTPError`` and ``httpx.TimeoutException``
        so the scheduler can catch them and continue with the next collector.
        """
        await self._ensure_client()
        assert self._http_client is not None  # for type checker
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
        """Close the underlying HTTP client. Call during application shutdown."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
            self.logger.debug("HTTP client closed for %s", self.__class__.__name__)
