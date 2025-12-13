"""
Launch decision API endpoints
"""
from fastapi import APIRouter, HTTPException

from app.models import Decision

router = APIRouter()


@router.get("/launch/{launch_id}", response_model=Decision)
async def get_current_decision(launch_id: int):
    """Get current launch decision"""
    # TODO: Implement decision engine integration
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.get("/launch/{launch_id}/history")
async def get_decision_history(launch_id: int):
    """Get historical decisions for a launch"""
    # TODO: Implement
    raise HTTPException(status_code=501, detail="Not implemented yet")
