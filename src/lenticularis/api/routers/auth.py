"""
Auth router — local account register/login/refresh/me + Google/Facebook OAuth.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from lenticularis.api.dependencies import get_current_user
from lenticularis.config import get_config
from lenticularis.database.db import get_db
from lenticularis.database.models import OAuthIdentity, User
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


@router.get("/providers")
async def list_providers():
    """Return which social login providers are enabled."""
    cfg = get_config().oauth
    return {
        "google":   cfg.google.enabled,
        "facebook": cfg.facebook.enabled,
    }


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _make_state() -> str:
    """Return a short-lived HMAC-signed state token for CSRF protection."""
    secret = get_config().auth.jwt_secret
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{ts}.{sig}".encode()).decode()


def _verify_state(state: str, max_age: int = 600) -> bool:
    """Return True if the state token is valid and not older than max_age seconds."""
    try:
        decoded = base64.urlsafe_b64decode(state + "==").decode()
        ts_str, sig = decoded.rsplit(".", 1)
        secret = get_config().auth.jwt_secret
        expected = hmac.new(secret.encode(), ts_str.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(sig, expected) and (int(time.time()) - int(ts_str)) < max_age
    except Exception:
        return False


def _oauth_redirect_url(request: Request, provider: str) -> str:
    """Build the absolute callback URL.

    Uses ``oauth.base_url`` from config when set (required behind a reverse
    proxy such as Traefik where ``request.base_url`` resolves to the internal
    address rather than the public domain).
    """
    configured = get_config().oauth.base_url.rstrip("/")
    base = configured if configured else str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/api/auth/{provider}/callback"
    logger.info("OAuth redirect_uri for %s: %s (base_url config=%r)", provider, redirect_uri, configured)
    return redirect_uri


def _upsert_oauth_user(db: Session, provider: str, provider_user_id: str,
                       email: str, display_name: str) -> User:
    """Find or create a User linked to this social identity."""
    identity = (
        db.query(OAuthIdentity)
        .filter_by(provider=provider, provider_user_id=provider_user_id)
        .first()
    )
    if identity:
        return identity.user

    # Try to link to an existing account with the same email
    user = db.query(User).filter_by(email=email).first()
    if not user:
        user = User(email=email, display_name=display_name, role="pilot")
        db.add(user)
        db.flush()
        logger.info("New user via %s OAuth: %s", provider, email)

    db.add(OAuthIdentity(
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
        provider_email=email,
    ))
    db.commit()
    db.refresh(user)
    return user


def _build_success_redirect(user: User) -> str:
    """Build the /oauth-callback URL carrying the JWT pair for the frontend."""
    access  = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    user_json = json.dumps({
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "has_password": user.hashed_password is not None,
        "org_id": user.org_id,
    })
    params = urllib.parse.urlencode({
        "access_token":  access,
        "refresh_token": refresh,
        "user":          base64.urlsafe_b64encode(user_json.encode()).decode(),
    })
    return f"/oauth-callback?{params}"


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

_GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USER_URL  = "https://www.googleapis.com/oauth2/v1/userinfo"


@router.get("/google", include_in_schema=False)
async def google_login(request: Request):
    cfg = get_config().oauth.google
    if not cfg.enabled:
        raise HTTPException(status_code=404, detail="Google login is not enabled")
    params = urllib.parse.urlencode({
        "client_id":     cfg.client_id,
        "redirect_uri":  _oauth_redirect_url(request, "google"),
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         _make_state(),
        "access_type":   "online",
    })
    return RedirectResponse(f"{_GOOGLE_AUTH_URL}?{params}")


@router.get("/google/callback", include_in_schema=False)
async def google_callback(code: str | None = None, state: str | None = None,
                           error: str | None = None, request: Request = None,
                           db: Session = Depends(get_db)):
    if error or not code or not state or not _verify_state(state):
        return RedirectResponse("/login?error=google_auth_failed")

    cfg = get_config().oauth.google
    redirect_uri = _oauth_redirect_url(request, "google")

    async with httpx.AsyncClient() as client:
        token_res = await client.post(_GOOGLE_TOKEN_URL, data={
            "code":          code,
            "client_id":     cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        })
        if not token_res.is_success:
            logger.error("Google token exchange failed: %s", token_res.text)
            return RedirectResponse("/login?error=google_token_failed")

        access_token = token_res.json()["access_token"]
        user_res = await client.get(_GOOGLE_USER_URL,
                                    headers={"Authorization": f"Bearer {access_token}"})
        if not user_res.is_success:
            logger.error("Google userinfo failed: %s", user_res.text)
            return RedirectResponse("/login?error=google_userinfo_failed")

    profile = user_res.json()
    email = profile.get("email")
    if not email:
        return RedirectResponse("/login?error=google_no_email")

    user = _upsert_oauth_user(
        db, "google", profile["id"], email,
        profile.get("name") or email.split("@")[0],
    )
    if not user.is_active:
        return RedirectResponse("/login?error=account_disabled")

    logger.info("Google OAuth login: %s", email)
    return RedirectResponse(_build_success_redirect(user))


# ---------------------------------------------------------------------------
# Facebook OAuth
# ---------------------------------------------------------------------------

_FACEBOOK_AUTH_URL  = "https://www.facebook.com/dialog/oauth"
_FACEBOOK_TOKEN_URL = "https://graph.facebook.com/oauth/access_token"
_FACEBOOK_USER_URL  = "https://graph.facebook.com/me"


@router.get("/facebook", include_in_schema=False)
async def facebook_login(request: Request):
    cfg = get_config().oauth.facebook
    if not cfg.enabled:
        raise HTTPException(status_code=404, detail="Facebook login is not enabled")
    params = urllib.parse.urlencode({
        "client_id":     cfg.client_id,
        "redirect_uri":  _oauth_redirect_url(request, "facebook"),
        "response_type": "code",
        "scope":         "email,public_profile",
        "state":         _make_state(),
    })
    return RedirectResponse(f"{_FACEBOOK_AUTH_URL}?{params}")


@router.get("/facebook/callback", include_in_schema=False)
async def facebook_callback(code: str | None = None, state: str | None = None,
                             error: str | None = None, request: Request = None,
                             db: Session = Depends(get_db)):
    if error or not code or not state or not _verify_state(state):
        return RedirectResponse("/login?error=facebook_auth_failed")

    cfg = get_config().oauth.facebook
    redirect_uri = _oauth_redirect_url(request, "facebook")

    async with httpx.AsyncClient() as client:
        token_res = await client.get(_FACEBOOK_TOKEN_URL, params={
            "client_id":     cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri":  redirect_uri,
            "code":          code,
        })
        if not token_res.is_success:
            logger.error("Facebook token exchange failed: %s", token_res.text)
            return RedirectResponse("/login?error=facebook_token_failed")

        access_token = token_res.json()["access_token"]
        user_res = await client.get(_FACEBOOK_USER_URL, params={
            "fields":       "id,name,email",
            "access_token": access_token,
        })
        if not user_res.is_success:
            logger.error("Facebook userinfo failed: %s", user_res.text)
            return RedirectResponse("/login?error=facebook_userinfo_failed")

    profile = user_res.json()
    email = profile.get("email")
    if not email:
        # Facebook can omit email for accounts without a confirmed email address.
        return RedirectResponse("/login?error=facebook_no_email")

    user = _upsert_oauth_user(
        db, "facebook", profile["id"], email,
        profile.get("name") or email.split("@")[0],
    )
    if not user.is_active:
        return RedirectResponse("/login?error=account_disabled")

    logger.info("Facebook OAuth login: %s", email)
    return RedirectResponse(_build_success_redirect(user))
