"""
Weather data models for Lenticularis.

These Pydantic models define the unified schema that all weather data collectors
normalize their data into. This ensures consistent data structure regardless of source.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional



class WeatherStation(BaseModel):
    """
    Represents a weather station from any network.

    This model stores metadata about weather stations including their location,
    network affiliation, and identifiers.
    """

    station_id: str = Field(..., description="Unique identifier for the station (network-prefixed, e.g. 'meteoswiss-BAS')")
    name: str = Field(..., description="Human-readable station name")
    network: str = Field(..., description="Weather network (e.g., 'meteoswiss', 'holfuy')")
    latitude: float = Field(..., description="Latitude in decimal degrees (WGS84)")
    longitude: float = Field(..., description="Longitude in decimal degrees (WGS84)")
    elevation: Optional[int] = Field(None, description="Elevation in meters above sea level")
    canton: Optional[str] = Field(None, description="Swiss canton code (e.g., 'BE', 'ZH')")
    member_ids: list[str] = Field(default_factory=list, description="Real station IDs that make up this virtual station (canonical first). Empty for non-virtual stations.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "station_id": "meteoswiss-BER",
                "name": "Bern / Zollikofen",
                "network": "meteoswiss",
                "latitude": 46.9914,
                "longitude": 7.4638,
                "elevation": 553,
                "canton": "BE",
            }
        }
    }


class WeatherMeasurement(BaseModel):
    """
    Represents a single weather measurement from any station.

    This is the unified data model that all collectors normalize their data into.
    All fields except station_id and timestamp are optional since different stations
    may provide different measurements.

    Pressure is stored as three physically distinct variants:
      - pressure_qfe: Station pressure (actual atmospheric pressure at station elevation)
      - pressure_qnh: Altimeter setting / aviation standard (corrected to sea level via ISA)
      - pressure_qff: Meteorological sea-level pressure (corrected with local temperature & humidity)
    """

    station_id: str = Field(..., description="Network-prefixed station identifier (e.g. 'meteoswiss-BAS')")
    network: str = Field(..., description="Source network")
    timestamp: datetime = Field(..., description="Measurement timestamp in UTC")

    # Wind
    wind_speed: Optional[float] = Field(None, description="Wind speed in km/h (10-min average)")
    wind_direction: Optional[int] = Field(None, ge=0, le=360, description="Wind direction in degrees (0-360, meteo convention)")
    wind_gust: Optional[float] = Field(None, description="Wind gust / peak speed in km/h")

    # Atmosphere
    temperature: Optional[float] = Field(None, description="Air temperature in °C")
    humidity: Optional[float] = Field(None, ge=0, le=100, description="Relative humidity in %")
    pressure_qfe: Optional[float] = Field(None, description="Station pressure (QFE) in hPa")
    pressure_qnh: Optional[float] = Field(None, description="Altimeter setting (QNH) in hPa")
    pressure_qff: Optional[float] = Field(None, description="Sea-level pressure (QFF) in hPa")

    # Precipitation / snow
    precipitation: Optional[float] = Field(None, description="Precipitation in mm")
    snow_depth: Optional[float] = Field(None, description="Snow depth in cm")

    model_config = {
        "json_schema_extra": {
            "example": {
                "station_id": "meteoswiss-BER",
                "network": "meteoswiss",
                "timestamp": "2026-03-08T10:00:00Z",
                "wind_speed": 12.5,
                "wind_direction": 270,
                "wind_gust": 18.3,
                "temperature": 5.2,
                "humidity": 75.0,
                "pressure_qfe": 974.8,
                "pressure_qnh": 1013.2,
                "pressure_qff": 1012.9,
                "precipitation": 0.0,
                "snow_depth": None,
            }
        }
    }


class ForecastPoint(BaseModel):
    """
    A single hourly forecast value for one station from one model run.

    Multiple ForecastPoints for the same (station_id, valid_time) may exist
    when collected across different model runs (different init_times). The
    query layer picks the latest init_time per valid_time for evaluation.
    """

    station_id: str = Field(..., description="Network-prefixed station identifier")
    network: str = Field(..., description="Station network (e.g. 'meteoswiss')")
    source: str = Field(..., description="Forecast data source (e.g. 'open-meteo')")
    model: str = Field(..., description="NWP model name (e.g. 'icon-seamless')")
    init_time: datetime = Field(..., description="Model run initialisation time (UTC)")
    valid_time: datetime = Field(..., description="Forecast valid time (UTC) — the future moment this applies to")

    # Wind
    wind_speed: Optional[float] = Field(None, description="Wind speed in km/h")
    wind_direction: Optional[int] = Field(None, ge=0, le=360, description="Wind direction in degrees")
    wind_gust: Optional[float] = Field(None, description="Wind gust in km/h")

    # Atmosphere
    temperature: Optional[float] = Field(None, description="Temperature in °C")
    humidity: Optional[float] = Field(None, description="Relative humidity in %")
    pressure_qnh: Optional[float] = Field(None, description="Pressure (QNH) in hPa")

    # Precipitation
    precipitation: Optional[float] = Field(None, description="Precipitation in mm")
