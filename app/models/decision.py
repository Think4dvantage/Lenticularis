"""
Decision models for launch conditions
"""
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel
from app.models.rule import Severity


class DecisionBase(BaseModel):
    """Base decision schema"""
    launch_id: int
    status: Severity
    contributing_factors: Dict[str, Any] = {}
    message: Optional[str] = None


class Decision(DecisionBase):
    """Decision response schema"""
    timestamp: datetime
    
    class Config:
        json_schema_extra = {
            "example": {
                "launch_id": 1,
                "status": "green",
                "timestamp": "2025-12-13T14:30:00Z",
                "contributing_factors": {
                    "wind_speed": 4.5,
                    "wind_direction": 270,
                    "pressure_trend": -1.2
                },
                "message": "All conditions within safe parameters"
            }
        }
