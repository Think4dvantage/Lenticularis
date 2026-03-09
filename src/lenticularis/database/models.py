"""
SQLAlchemy ORM models for Lenticularis.

Tables
------
users            — pilot and admin accounts (local + social login)
oauth_identities — one row per linked social provider per user
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    display_name = Column(String, nullable=False)
    # Null for social-login-only accounts (no local password set)
    hashed_password = Column(String, nullable=True)
    role = Column(String, nullable=False, default="pilot")   # "pilot" | "admin"
    is_active = Column(Boolean, nullable=False, default=True)
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
