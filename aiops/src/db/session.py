"""
Database engine and session helpers for the ASHIA control plane.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings

DATABASE_URL = get_settings().database_url

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
        )
    return _session_factory


def get_db():
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
