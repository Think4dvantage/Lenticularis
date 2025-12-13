"""
Rule models for launch decision making
"""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field
from enum import Enum


class RuleType(str, Enum):
    """Types of rules that can be evaluated"""
    WIND_SPEED = "wind_speed"
    WIND_DIRECTION = "wind_direction"
    GUST_SPEED = "gust_speed"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    PRESSURE_TREND = "pressure_trend"
    MULTI_STATION = "multi_station"


class Severity(str, Enum):
    """Rule severity levels"""
    GREEN = "green"  # Flyable
    ORANGE = "orange"  # Caution
    RED = "red"  # No-go


class Operator(str, Enum):
    """Comparison operators for rules"""
    GREATER_THAN = ">"
    LESS_THAN = "<"
    EQUAL = "="
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    BETWEEN = "between"
    NOT_IN_RANGE = "not_in_range"


class RuleBase(BaseModel):
    """Base rule schema"""
    launch_id: int
    rule_type: RuleType
    station_id: Optional[str] = Field(None, description="Specific station, or null for any associated station")
    
    operator: Operator
    threshold_value: float = Field(..., description="Primary threshold value")
    threshold_value_max: Optional[float] = Field(None, description="Secondary value for BETWEEN operator")
    
    severity: Severity
    priority: int = Field(1, ge=1, le=10, description="Rule priority (1=lowest, 10=highest)")
    active: bool = True
    description: Optional[str] = None


class RuleCreate(RuleBase):
    """Rule creation schema"""
    pass


class RuleUpdate(BaseModel):
    """Rule update schema"""
    rule_type: Optional[RuleType] = None
    station_id: Optional[str] = None
    operator: Optional[Operator] = None
    threshold_value: Optional[float] = None
    threshold_value_max: Optional[float] = None
    severity: Optional[Severity] = None
    priority: Optional[int] = None
    active: Optional[bool] = None
    description: Optional[str] = None


class Rule(RuleBase):
    """Rule response schema"""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True
