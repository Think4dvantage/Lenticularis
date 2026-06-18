"""
Auth endpoint tests — register / login / refresh / me happy paths and error cases.
"""
from __future__ import annotations

import pytest


_EMAIL = "pilot@test.example"
_PASSWORD = "s3cr3t-password"
_NAME = "Test Pilot"


async def _register(client, email=_EMAIL, password=_PASSWORD, name=_NAME):
    return await client.post(
        "/api/auth/register",
        json={"email": email, "display_name": name, "password": password},
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

async def test_register_creates_user_and_returns_tokens(client):
    r = await _register(client)
    assert r.status_code == 201
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["user"]["email"] == _EMAIL
    assert body["user"]["role"] == "pilot"


async def test_register_duplicate_email_returns_409(client):
    await _register(client)
    r = await _register(client)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def test_login_correct_password_returns_tokens(client):
    await _register(client)
    r = await client.post(
        "/api/auth/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_login_wrong_password_returns_401(client):
    await _register(client)
    r = await client.post(
        "/api/auth/login",
        json={"email": _EMAIL, "password": "wrong"},
    )
    assert r.status_code == 401


async def test_login_unknown_email_returns_401(client):
    r = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "x"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

async def test_refresh_returns_fresh_token_pair(client):
    reg = (await _register(client)).json()
    refresh_token = reg["refresh_token"]
    r = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body


async def test_refresh_with_garbage_token_returns_401(client):
    r = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": "not-a-real-token"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------

async def test_me_returns_current_user(client):
    reg = (await _register(client)).json()
    access = reg["access_token"]
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.json()["email"] == _EMAIL


async def test_me_without_token_returns_401(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_with_garbage_token_returns_401(client):
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
