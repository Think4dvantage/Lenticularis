"""
Launch site models
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class LaunchBase(BaseModel):
    """Base launch site schema"""
    name: str = Field(..., min_length=1, max_length=200)
    location: str = Field(..., description="General location/region")
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    elevation: int = Field(..., description="Elevation in meters")
    description: Optional[str] = None
    preferred_wind_directions: Optional[str] = Field(None, description="Comma-separated directions, e.g., 'N,NW,W'")
    webcam_urls: Optional[str] = Field(None, description="Comma-separated webcam URLs")
    active: bool = True


class LaunchCreate(LaunchBase):
    """Launch creation schema"""
    pass


class LaunchUpdate(BaseModel):
    """Launch update schema - all fields optional"""
    name: Optional[str] = None
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    elevation: Optional[int] = None
    description: Optional[str] = None
    preferred_wind_directions: Optional[str] = None
    webcam_urls: Optional[str] = None
    active: Optional[bool] = None


class Launch(LaunchBase):
    """Launch response schema"""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class LaunchWithStations(Launch):
    """Launch with associated weather stations"""
    stations: List[str] = []
