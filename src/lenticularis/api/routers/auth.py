"""
Auth router — local account register/login/refresh/me.

Social login (OAuth2) will be added in a follow-up step.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import get_current_user
from lenticularis.database.db import get_db
from lenticularis.database.models import User
from lenticularis.models.auth import Token, TokenRefreshRequest, UserCreate, UserLogin, UserOut
from lenticularis.services.auth import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        has_password=user.hashed_password is not None,
        org_id=user.org_id,
    )


def _token_pair(user: User) -> Token:
    return Token(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id),
        user=_user_out(user),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: Session = Depends(get_db)):
    """Create a new local pilot account and return tokens."""
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=body.email,
        display_name=body.display_name.strip(),
        hashed_password=hash_password(body.password),
        role="pilot",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("New user registered: %s", user.email)
    return _token_pair(user)


@router.post("/login", response_model=Token)
async def login(body: UserLogin, db: Session = Depends(get_db)):
    """Exchange email + password for a token pair."""
    user = db.query(User).filter(User.email == body.email).first()
    if (
        not user
        or not user.hashed_password
        or not verify_password(body.password, user.hashed_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    return _token_pair(user)


@router.post("/refresh", response_model=Token)
async def refresh(body: TokenRefreshRequest, db: Session = Depends(get_db)):
    """Exchange a refresh token for a fresh token pair."""
    payload = decode_refresh_token(body.refresh_token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return _token_pair(user)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return _user_out(current_user)
