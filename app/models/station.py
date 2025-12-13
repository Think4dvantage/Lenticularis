"""
Weather station models
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class StationBase(BaseModel):
    """Base station schema"""
    station_id: str = Field(..., description="Unique station identifier from source")
    source: str = Field(..., description="Data source: meteoswiss, holfuy, slf, windline")
    name: str
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    elevation: Optional[int] = Field(None, description="Elevation in meters")
    active: bool = True


class StationCreate(StationBase):
    """Station creation schema"""
    pass


class StationUpdate(BaseModel):
    """Station update schema"""
    name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    elevation: Optional[int] = None
    active: Optional[bool] = None


class Station(StationBase):
    """Station response schema"""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True
