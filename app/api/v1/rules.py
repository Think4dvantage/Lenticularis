"""
Rule management API endpoints
"""
from fastapi import APIRouter, HTTPException
from typing import List

from app.models import Rule

router = APIRouter()


@router.get("/launch/{launch_id}", response_model=List[Rule])
async def get_launch_rules(launch_id: int):
    """Get all rules for a launch"""
    # TODO: Implement
    return []


@router.post("/launch/{launch_id}")
async def create_rule(launch_id: int):
    """Create a new rule for a launch"""
    # TODO: Implement
    raise HTTPException(status_code=501, detail="Not implemented yet")
