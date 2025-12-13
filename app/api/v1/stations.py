"""
Weather station API endpoints
"""
from fastapi import APIRouter, HTTPException
from typing import List

from app.models import Station

router = APIRouter()


@router.get("/", response_model=List[Station])
async def list_stations():
    """Get all weather stations"""
    # TODO: Implement database query
    return []


@router.get("/{station_id}")
async def get_station(station_id: str):
    """Get a specific weather station"""
    # TODO: Implement
    raise HTTPException(status_code=501, detail="Not implemented yet")
