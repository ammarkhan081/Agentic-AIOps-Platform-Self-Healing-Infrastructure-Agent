"""
Unit tests for authentication helpers and routes.
"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

auth = importlib.import_module("src.api.routes.auth")


class FakeDB:
    def __init__(self, existing=None):
        self.rows = dict(existing or {})
        self.added = []
        self.committed = False
        self.closed = False

    def get(self, model, key):
        return self.rows.get(key)

    def add(self, record):
        key = getattr(record, "token_hash", None) or getattr(record, "username", None)
        self.rows[key] = record
        self.added.append(record)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_seed_default_users_creates_missing_accounts(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(auth, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(auth, "SEED_DEMO_USERS", True)

    auth.seed_default_users()

    assert len(db.added) == 3
    assert db.committed is True
    assert db.closed is True


def test_require_role_rejects_unpermitted_user():
    checker = auth.require_role("admin")

    with pytest.raises(HTTPException) as exc:
        checker({"username": "viewer", "role": "viewer"})

    assert exc.value.status_code == 403


def test_refresh_access_token_rejects_revoked_token(monkeypatch):
    db = FakeDB(
        {
            "admin": SimpleNamespace(
                username="admin", role="admin", name="Admin User", is_active=True
            ),
        }
    )
    token = auth._create_refresh_token({"sub": "admin", "role": "admin"})
    db.rows[auth._token_hash(token)] = SimpleNamespace(token_hash=auth._token_hash(token))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.refresh_access_token(auth.RefreshRequest(refresh_token=token), db=db))

    assert exc.value.status_code == 401


def test_logout_revokes_refresh_token():
    db = FakeDB()
    token = "refresh-token-1"

    result = asyncio.run(auth.logout(auth.LogoutRequest(refresh_token=token), db=db))

    assert result["message"] == "Logged out"
    assert auth._token_hash(token) in db.rows


def test_seed_default_users_skips_when_disabled(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(auth, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(auth, "SEED_DEMO_USERS", False)

    auth.seed_default_users()

    assert db.added == []
    assert db.committed is False


def test_register_creates_user(monkeypatch):
    db = FakeDB()
    payload = auth.UserCreate(
        username="new-user",
        password="secret123",
        name="New User",
        email="new@example.com",
        role="viewer",
    )

    result = asyncio.run(auth.register(payload, _={"role": "admin"}, db=db))

    assert result["message"].startswith("User 'new-user' created")
    assert db.committed is True
    assert "new-user" in db.rows
    assert db.rows["new-user"].email == "new@example.com"


def test_get_current_user_returns_identity():
    db = FakeDB(
        {
            "admin": SimpleNamespace(
                username="admin", role="admin", name="Admin User", is_active=True
            ),
        }
    )
    token = auth._create_token({"sub": "admin", "role": "admin"})

    result = auth.get_current_user(token=token, db=db)

    assert result == {"username": "admin", "name": "Admin User", "role": "admin", "email": None}


def test_get_current_user_rejects_invalid_token():
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(token="bad-token", db=FakeDB())

    assert exc.value.status_code == 401


def test_login_returns_access_and_refresh_tokens():
    password = "secret123"
    db = FakeDB(
        {
            "admin": SimpleNamespace(
                username="admin",
                role="admin",
                name="Admin User",
                hashed_password=auth.pwd_context.hash(password),
                is_active=True,
            )
        }
    )

    result = asyncio.run(
        auth.login(form=SimpleNamespace(username="admin", password=password), db=db)
    )

    assert result.role == "admin"
    assert result.refresh_token is not None
    assert result.token_type == "bearer"


def test_login_rejects_bad_password():
    db = FakeDB(
        {
            "admin": SimpleNamespace(
                username="admin",
                role="admin",
                name="Admin User",
                hashed_password=auth.pwd_context.hash("right-password"),
                is_active=True,
            )
        }
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth.login(form=SimpleNamespace(username="admin", password="wrong-password"), db=db)
        )

    assert exc.value.status_code == 401


def test_refresh_access_token_returns_new_tokens():
    db = FakeDB(
        {
            "admin": SimpleNamespace(
                username="admin", role="admin", name="Admin User", is_active=True
            ),
        }
    )
    token = auth._create_refresh_token({"sub": "admin", "role": "admin"})

    result = asyncio.run(auth.refresh_access_token(auth.RefreshRequest(refresh_token=token), db=db))

    assert result.username == "admin"
    assert result.refresh_token is not None
    assert auth._token_hash(token) in db.rows


def test_register_rejects_duplicate_username():
    db = FakeDB(
        {"viewer": SimpleNamespace(username="viewer", role="viewer", name="Viewer", is_active=True)}
    )
    payload = auth.UserCreate(
        username="viewer",
        password="secret123",
        name="Viewer",
        email="viewer@example.com",
        role="viewer",
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.register(payload, _={"role": "admin"}, db=db))

    assert exc.value.status_code == 409


def test_me_returns_user_payload():
    user = {"username": "viewer", "role": "viewer", "name": "Viewer User", "email": None}

    result = asyncio.run(auth.me(user=user))

    assert result["username"] == "viewer"
    assert "incidents:read" in result["permissions"]
