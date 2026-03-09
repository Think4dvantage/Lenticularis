"""
Auth service — password hashing and JWT helpers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from lenticularis.config import get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _secret() -> str:
    return get_config().auth.jwt_secret


def _algorithm() -> str:
    return get_config().auth.jwt_algorithm


def create_access_token(user_id: str, role: str) -> str:
    cfg = get_config().auth
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=cfg.access_token_expire_minutes
    )
    payload = {"sub": user_id, "role": role, "exp": expire, "type": "access"}
    return jwt.encode(payload, _secret(), algorithm=_algorithm())


def create_refresh_token(user_id: str) -> str:
    cfg = get_config().auth
    expire = datetime.now(timezone.utc) + timedelta(days=cfg.refresh_token_expire_days)
    payload = {"sub": user_id, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, _secret(), algorithm=_algorithm())


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_algorithm()])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


def decode_refresh_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_algorithm()])
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None
