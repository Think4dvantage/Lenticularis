"""
SQLAlchemy ORM models for Lenticularis.

Tables
------
organizations        — customer organisations (multi-tenant)
users                — pilot and admin accounts (local + social login)
oauth_identities     — one row per linked social provider per user
rulesets             — pilot's rule set (includes site name + coordinates)
rule_conditions      — individual condition rows within a rule set
launch_landing_links — links a launch ruleset to one or more landing rulesets
ruleset_webcams      — webcam URLs linked to a ruleset for visual reference
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Organization(Base):
    """One row per customer organisation (e.g. VKPI Interlaken)."""

    __tablename__ = "organizations"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug        = Column(String, unique=True, nullable=False, index=True)   # e.g. "vkpi"
    name        = Column(String, nullable=False)                            # e.g. "VKPI Interlaken"
    description = Column(String, nullable=True)
    created_at  = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    members  = relationship("User",    back_populates="organization")
    rulesets = relationship("RuleSet", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    display_name = Column(String, nullable=False)
    # Null for social-login-only accounts (no local password set)
    hashed_password = Column(String, nullable=True)
    # "pilot" | "customer" | "admin" | "org_admin" | "org_pilot"
    role = Column(String, nullable=False, default="pilot")
    is_active = Column(Boolean, nullable=False, default=True)

    # Organisation membership (null for regular pilots/admins)
    org_id       = Column(String, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    organization = relationship("Organization", back_populates="members")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    oauth_identities = relationship(
        "OAuthIdentity", back_populates="user", cascade="all, delete-orphan"
    )


class OAuthIdentity(Base):
    """One row per linked social-login provider for a user."""

    __tablename__ = "oauth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_provider_user"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider = Column(String, nullable=False)           # "google" | "github"
    provider_user_id = Column(String, nullable=False)   # opaque ID from provider
    provider_email = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="oauth_identities")


class RuleSet(Base):
    """One rule set = one pilot's decision profile for a launch site."""

    __tablename__ = "rulesets"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Site identity (embedded — no separate launch_sites table)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    altitude_m = Column(Integer, nullable=True)

    # Site type: "launch" | "landing" | "opportunity"
    site_type = Column(String, nullable=False, default="launch")

    # Evaluation
    combination_logic = Column(String, nullable=False, default="worst_wins")  # worst_wins | majority_vote

    # Gallery
    is_public = Column(Boolean, nullable=False, default=False)
    clone_count = Column(Integer, nullable=False, default=0)
    cloned_from_id = Column(String, ForeignKey("rulesets.id", ondelete="SET NULL"), nullable=True)

    # Preset — admin-curated template visible to all pilots in the new-ruleset form
    is_preset = Column(Boolean, nullable=False, default=False)

    # Organisation ownership (null for personal rulesets)
    org_id       = Column(String, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    organization = relationship("Organization", back_populates="rulesets")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    conditions = relationship(
        "RuleCondition", back_populates="ruleset", cascade="all, delete-orphan",
        order_by="RuleCondition.sort_order",
    )

    webcams = relationship(
        "RuleSetWebcam", back_populates="ruleset", cascade="all, delete-orphan",
        order_by="RuleSetWebcam.sort_order",
    )

    # Landing links — only populated/meaningful when site_type == "launch"
    landing_links = relationship(
        "LaunchLandingLink",
        foreign_keys="LaunchLandingLink.launch_ruleset_id",
        back_populates="launch_ruleset",
        cascade="all, delete-orphan",
    )

    @property
    def linked_landing_ids(self) -> list[str]:
        return [link.landing_ruleset_id for link in self.landing_links]


class LaunchLandingLink(Base):
    """Associates a launch ruleset with one or more landing rulesets (many-to-many)."""

    __tablename__ = "launch_landing_links"
    __table_args__ = (
        UniqueConstraint("launch_ruleset_id", "landing_ruleset_id", name="uq_launch_landing"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    launch_ruleset_id = Column(
        String, ForeignKey("rulesets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    landing_ruleset_id = Column(
        String, ForeignKey("rulesets.id", ondelete="CASCADE"), nullable=False
    )

    launch_ruleset = relationship(
        "RuleSet", foreign_keys=[launch_ruleset_id], back_populates="landing_links"
    )
    landing_ruleset = relationship(
        "RuleSet", foreign_keys=[landing_ruleset_id]
    )


class RuleSetWebcam(Base):
    """A webcam URL linked to a rule set for additional visual context."""

    __tablename__ = "ruleset_webcams"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ruleset_id = Column(
        String, ForeignKey("rulesets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url = Column(String, nullable=False)
    label = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    ruleset = relationship("RuleSet", back_populates="webcams")


class StationDedupOverride(Base):
    """Admin-managed pairs of station IDs that are always merged regardless of distance."""

    __tablename__ = "station_dedup_overrides"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    station_id_a = Column(String, nullable=False)
    station_id_b = Column(String, nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class RuleCondition(Base):
    """One condition row in a rule set's condition builder."""

    __tablename__ = "rule_conditions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ruleset_id = Column(
        String, ForeignKey("rulesets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Grouping (NULL = top-level flat list; future: group_id references a condition_groups table)
    group_id = Column(String, nullable=True)

    # Station reference (per-condition — this is the key design)
    station_id = Column(String, nullable=False)
    station_b_id = Column(String, nullable=True)   # only for pressure_delta field

    # Condition definition
    field = Column(String, nullable=False)          # wind_speed | wind_gust | wind_direction | temperature | humidity | pressure | pressure_delta | precipitation | snow_depth
    operator = Column(String, nullable=False)       # > | < | >= | <= | = | between | not_between | in_direction_range
    value_a = Column(Float, nullable=False)
    value_b = Column(Float, nullable=True)          # upper bound for between / direction range

    result_colour = Column(String, nullable=False, default="red")   # green | orange | red
    sort_order = Column(Integer, nullable=False, default=0)

    ruleset = relationship("RuleSet", back_populates="conditions")
