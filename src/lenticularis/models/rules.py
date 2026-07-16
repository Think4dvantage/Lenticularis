"""
Pydantic schemas for rule sets and conditions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SiteType = Literal["launch", "landing", "opportunity"]

FieldName = Literal[
    "wind_speed", "wind_gust", "wind_direction",
    "temperature", "humidity", "pressure", "pressure_delta",
    "precipitation", "snow_depth",
    "foehn_active",
]

OperatorName = Literal[
    ">", "<", ">=", "<=", "=",
    "between", "not_between", "in_direction_range",
]

ResultColour = Literal["green", "orange", "red"]
CombinationLogic = Literal["worst_wins", "majority_vote"]


# ---------------------------------------------------------------------------
# Webcams
# ---------------------------------------------------------------------------

class WebcamBase(BaseModel):
    url: str
    label: Optional[str] = None
    sort_order: int = 0

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        v = (v or "").strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Webcam URL must start with http:// or https://")
        return v


class WebcamCreate(WebcamBase):
    pass


class WebcamOut(WebcamBase):
    id: str

    model_config = {"from_attributes": True}


class WebcamsReplaceRequest(BaseModel):
    webcams: list[WebcamCreate]


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
    site_type: SiteType = "launch"
    combination_logic: CombinationLogic = "worst_wins"
    is_public: bool = False
    org_slug: Optional[str] = None  # if set, ruleset is scoped to this org


class RuleSetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_m: Optional[int] = None
    site_type: Optional[SiteType] = None
    combination_logic: Optional[CombinationLogic] = None
    is_public: Optional[bool] = None
    notify_on: Optional[str] = None  # comma-separated colours e.g. "green" or "green,orange"


class RuleSetOut(BaseModel):
    id: str
    owner_id: str
    name: str
    description: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    altitude_m: Optional[int]
    site_type: SiteType
    combination_logic: CombinationLogic
    is_public: bool
    is_preset: bool
    is_showcase: bool = False
    clone_count: int
    cloned_from_id: Optional[str]
    linked_landing_ids: list[str] = []
    notify_on: Optional[str] = None
    owner_display_name: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class RuleSetDetail(RuleSetOut):
    """Full rule set including all conditions, groups and webcams."""
    conditions: list[RuleConditionOut] = []
    webcams: list[WebcamOut] = []
    # Included so the editor rebuilds groups from stored data rather than
    # inferring them from group_id collisions. May contain empty groups.
    condition_groups: list[ConditionGroupOut] = []


class PublicRuleSetMarker(BaseModel):
    """
    The COMPLETE set of what a visitor may learn about a rule set.

    Deliberately NOT derived from RuleSetOut: that model carries owner_display_name
    and would leak owner identity by default.  Every field here is an explicit
    decision to expose; adding one is a privacy decision.
    """
    id: str
    name: str
    lat: float
    lon: float
    site_type: SiteType
    decision: ResultColour


class PublicMapResponse(BaseModel):
    """Public map payload.  `generated_at` lets the client show data freshness."""
    data: list[PublicRuleSetMarker] = []
    generated_at: str


class ConditionGroupIn(BaseModel):
    """A condition group as sent by the editor.  `id` is client-minted, as group_id already is."""
    id: str
    name: Optional[str] = Field(default=None, max_length=120)
    sort_order: int = 0


class ConditionGroupOut(BaseModel):
    id: str
    name: Optional[str] = None
    sort_order: int = 0

    model_config = {"from_attributes": True}


class ConditionsReplaceRequest(BaseModel):
    """
    Replace the full condition list — and the groups — for a rule set in one call.

    Groups cannot be inferred from the conditions, because a group may legitimately
    hold none, which would leave no trace.  So they are sent explicitly and replaced
    atomically alongside the conditions.
    """
    conditions: list[RuleConditionCreate]
    groups: list[ConditionGroupIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _groups_cover_conditions(self):
        """
        Fail closed: every group a condition points at must be present.

        A permissive default here would let any caller that omits `groups` silently
        delete every group name the pilot typed.  A 422 is vastly preferable to
        silent data loss.
        """
        known = {g.id for g in self.groups}
        referenced = {c.group_id for c in self.conditions if c.group_id}
        missing = referenced - known
        if missing:
            raise ValueError(
                "conditions reference unknown group_id(s): "
                + ", ".join(sorted(missing))
                + " — send them in `groups` or set group_id to null"
            )
        dupes = [g.id for g in self.groups if [x.id for x in self.groups].count(g.id) > 1]
        if dupes:
            raise ValueError(f"duplicate group id(s): {', '.join(sorted(set(dupes)))}")
        return self


class LandingLinksRequest(BaseModel):
    """Replace landing links for a launch ruleset."""
    landing_ids: list[str]


# ---------------------------------------------------------------------------
# Evaluation results
# ---------------------------------------------------------------------------

class ConditionResult(BaseModel):
    """Result of evaluating one condition (or one member of an AND group)."""
    condition_id: str
    station_id: str
    station_b_id: Optional[str] = None
    field: str
    operator: str
    value_a: float
    value_b: Optional[float] = None
    actual_value: Optional[float] = None
    matched: bool
    result_colour: ResultColour
    group_id: Optional[str] = None
    group_all_matched: Optional[bool] = None
    # Presentation only — lets a decision be explained as "Föhn risk" rather than
    # a list of numbers. Populated by lookup; never affects a decision.
    group_name: Optional[str] = None


class LandingDecision(BaseModel):
    """Evaluation summary for one linked landing zone."""
    ruleset_id: str
    name: str
    decision: ResultColour


class EvaluationResult(BaseModel):
    """Full evaluation result for a rule set."""
    decision: ResultColour
    evaluated_at: str
    condition_results: list[ConditionResult] = []
    no_data_stations: list[str] = []
    # Populated only when site_type == "launch" and landing links exist
    landing_decisions: list[LandingDecision] = []
    best_landing_decision: Optional[ResultColour] = None
