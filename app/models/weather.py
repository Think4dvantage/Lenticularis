"""
Pydantic models for API schemas
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class WeatherDataBase(BaseModel):
    """Base weather data schema"""
    station_id: str
    source: str
    timestamp: datetime
    
    wind_speed: Optional[float] = Field(None, description="Wind speed in m/s")
    wind_direction: Optional[int] = Field(None, ge=0, le=360, description="Wind direction in degrees")
    gust_speed: Optional[float] = Field(None, description="Gust speed in m/s")
    gust_direction: Optional[int] = Field(None, ge=0, le=360, description="Gust direction in degrees")
    
    temperature: Optional[float] = Field(None, description="Temperature in Celsius")
    humidity: Optional[float] = Field(None, ge=0, le=100, description="Relative humidity %")
    pressure: Optional[float] = Field(None, description="Pressure in hPa")
    rain: Optional[float] = Field(None, description="Precipitation in mm")
    
    class Config:
        json_schema_extra = {
            "example": {
                "station_id": "INT",
                "source": "meteoswiss",
                "timestamp": "2025-12-13T14:30:00Z",
                "wind_speed": 5.5,
                "wind_direction": 270,
                "gust_speed": 8.2,
                "temperature": 12.5,
                "humidity": 65.0,
                "pressure": 1013.25
            }
        }


class WeatherData(WeatherDataBase):
    """Weather data response schema"""
    pass


class WeatherDataCreate(WeatherDataBase):
    """Weather data creation schema"""
    pass
