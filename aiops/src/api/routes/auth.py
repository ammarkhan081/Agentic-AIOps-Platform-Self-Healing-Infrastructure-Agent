"""
Authentication routes - JWT-based auth with role-based access control.
Roles: admin, sre, viewer
"""

from __future__ import annotations

import hashlib
import os
import secrets
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ...db.models import RevokedTokenRecord, UserRecord
from ...db.session import get_db, get_session_factory

router = APIRouter()
logger = logging.getLogger("api.auth")

SECRET_KEY = os.getenv("JWT_SECRET", "").strip() or secrets.token_urlsafe(48)
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_EXP = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15"))

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

DEFAULT_USERS = [
    {
        "username": "admin",
        "password": "admin123",
        "role": "admin",
        "name": "Admin User",
        "email": "admin@example.com",
    },
    {
        "username": "ammar",
        "password": "ammar123",
        "role": "sre",
        "name": "Ammar Ayaz",
        "email": "ammar@example.com",
    },
    {
        "username": "viewer",
        "password": "viewer123",
        "role": "viewer",
        "name": "Viewer User",
        "email": "viewer@example.com",
    },
]
SEED_DEMO_USERS = os.getenv("SEED_DEMO_USERS", "false").strip().lower() == "true"

if not os.getenv("JWT_SECRET", "").strip():
    logger.warning(
        "JWT_SECRET not set; using an ephemeral process-local secret. "
        "Tokens will be invalidated on restart."
    )


class Token(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str
    role: str
    username: str


class UserCreate(BaseModel):
    username: str
    password: str
    name: str
    email: str | None = None
    role: str = "viewer"


def ensure_user_schema() -> None:
    session_factory = get_session_factory()
    db = session_factory()
    try:
        bind = getattr(db, "bind", None)
        if bind is None:
            return
        inspector = inspect(bind)
        columns = {column["name"] for column in inspector.get_columns("users")}
        if "email" not in columns:
            db.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
            db.commit()
    except Exception:
        # Non-fatal bootstrap compatibility shim for existing local databases.
        db.rollback()
    finally:
        db.close()


def seed_default_users() -> None:
    if not SEED_DEMO_USERS:
        return
    session_factory = get_session_factory()
    db = session_factory()
    try:
        for item in DEFAULT_USERS:
            existing = db.get(UserRecord, item["username"])
            if existing:
                continue
            db.add(
                UserRecord(
                    username=item["username"],
                    hashed_password=pwd_context.hash(item["password"]),
                    email=item.get("email"),
                    role=item["role"],
                    name=item["name"],
                    is_active=True,
                )
            )
        db.commit()
    finally:
        db.close()


def _create_token(data: dict, expires_minutes: int = ACCESS_EXP) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=expires_minutes)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _create_refresh_token(data: dict) -> str:
    days = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(days=days)
    payload["token_type"] = "refresh"
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_expiry(payload: dict) -> datetime | None:
    exp_value = payload.get("exp")
    if exp_value is None:
        return None
    try:
        return datetime.utcfromtimestamp(exp_value)
    except Exception:
        return None


def _is_refresh_token_revoked(db: Session, refresh_token: str) -> bool:
    return db.get(RevokedTokenRecord, _token_hash(refresh_token)) is not None


def _revoke_refresh_token(db: Session, refresh_token: str, username: str | None = None) -> None:
    token_hash = _token_hash(refresh_token)
    if db.get(RevokedTokenRecord, token_hash):
        return

    expires_at = None
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        expires_at = _parse_expiry(payload)
        username = username or payload.get("sub")
    except JWTError:
        pass

    db.add(
        RevokedTokenRecord(
            token_hash=token_hash,
            token_type="refresh",
            username=username,
            expires_at=expires_at,
        )
    )
    db.commit()


def _get_user_record(db: Session, username: str) -> UserRecord | None:
    return db.get(UserRecord, username)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    return get_current_user_from_token(token, db)


def get_current_user_from_token(token: str, db: Session) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        record = _get_user_record(db, username)
        if not record or not record.is_active:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {
            "username": record.username,
            "name": record.name,
            "role": record.role,
            "email": getattr(record, "email", None),
        }
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Could not validate token") from exc


def require_role(*roles):
    def checker(user=Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail=f"Role '{user['role']}' not permitted")
        return user

    return checker


def _role_permissions(role: str) -> list[str]:
    base = ["incidents:read", "reports:read", "memory:read", "health:read"]
    if role in {"admin", "sre"}:
        base.extend(["incidents:trigger", "hitl:decide", "demo:faults:write"])
    if role == "admin":
        base.extend(["users:write", "monitor:trigger", "memory:delete"])
    return base


@router.post("/login", response_model=Token)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    record = _get_user_record(db, form.username)
    if not record or not pwd_context.verify(form.password, record.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = _create_token({"sub": record.username, "role": record.role})
    refresh = _create_refresh_token({"sub": record.username, "role": record.role})
    return Token(
        access_token=token,
        refresh_token=refresh,
        token_type="bearer",
        role=record.role,
        username=record.username,
    )


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=Token)
async def refresh_access_token(body: RefreshRequest, db: Session = Depends(get_db)):
    if _is_refresh_token_revoked(db, body.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    try:
        payload = jwt.decode(body.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("token_type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        record = _get_user_record(db, username)
        if not record or not record.is_active:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        _revoke_refresh_token(db, body.refresh_token, record.username)
        new_access = _create_token({"sub": record.username, "role": record.role})
        new_refresh = _create_refresh_token({"sub": record.username, "role": record.role})
        return Token(
            access_token=new_access,
            refresh_token=new_refresh,
            token_type="bearer",
            role=record.role,
            username=record.username,
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Could not validate refresh token") from exc


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


@router.post("/logout")
async def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    if body.refresh_token:
        _revoke_refresh_token(db, body.refresh_token)
    return {"message": "Logged out"}


@router.post("/register", status_code=201)
async def register(
    data: UserCreate,
    _: dict = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    if _get_user_record(db, data.username):
        raise HTTPException(status_code=409, detail="Username already exists")

    record = UserRecord(
        username=data.username,
        hashed_password=pwd_context.hash(data.password),
        email=data.email,
        role=data.role,
        name=data.name,
        is_active=True,
    )
    db.add(record)
    db.commit()
    return {"message": f"User '{data.username}' created with role '{data.role}'"}


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return {**user, "permissions": _role_permissions(user["role"])}
