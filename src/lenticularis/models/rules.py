"""
Pydantic schemas for rule sets and conditions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

FieldName = Literal[
    "wind_speed", "wind_gust", "wind_direction",
    "temperature", "humidity", "pressure", "pressure_delta",
    "precipitation", "snow_depth",
]

OperatorName = Literal[
    ">", "<", ">=", "<=", "=",
    "between", "not_between", "in_direction_range",
]

ResultColour = Literal["green", "orange", "red"]
CombinationLogic = Literal["worst_wins", "majority_vote"]


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

class RuleConditionBase(BaseModel):
    station_id: str
    station_b_id: Optional[str] = None
    field: FieldName
    operator: OperatorName
    value_a: float
    value_b: Optional[float] = None
    result_colour: ResultColour = "red"
    sort_order: int = 0
    group_id: Optional[str] = None


class RuleConditionCreate(RuleConditionBase):
    pass


class RuleConditionOut(RuleConditionBase):
    id: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Rule sets
# ---------------------------------------------------------------------------

class RuleSetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_m: Optional[int] = None
    combination_logic: CombinationLogic = "worst_wins"
    is_public: bool = False


class RuleSetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_m: Optional[int] = None
    combination_logic: Optional[CombinationLogic] = None
    is_public: Optional[bool] = None


class RuleSetOut(BaseModel):
    id: str
    owner_id: str
    name: str
    description: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    altitude_m: Optional[int]
    combination_logic: CombinationLogic
    is_public: bool
    clone_count: int
    cloned_from_id: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class RuleSetDetail(RuleSetOut):
    """Full rule set including all conditions."""
    conditions: list[RuleConditionOut] = []


class ConditionsReplaceRequest(BaseModel):
    """Replace the full condition list for a rule set in one call."""
    conditions: list[RuleConditionCreate]


# ---------------------------------------------------------------------------
# Evaluation results
# ---------------------------------------------------------------------------

class ConditionResult(BaseModel):
    """Result of evaluating one condition (or one member of an AND group)."""
    condition_id: str
    station_id: str
    station_b_id: Optional[str] = None
    field: str
    actual_value: Optional[float] = None
    matched: bool
    result_colour: ResultColour
    group_id: Optional[str] = None
    group_all_matched: Optional[bool] = None


class EvaluationResult(BaseModel):
    """Full evaluation result for a rule set."""
    decision: ResultColour
    evaluated_at: str
    condition_results: list[ConditionResult] = []
    no_data_stations: list[str] = []
