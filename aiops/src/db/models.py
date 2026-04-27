"""
SQLAlchemy ORM models for persistent storage.
Tables: incidents, audit_log, users
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase

from .session import get_engine


class Base(DeclarativeBase):
    pass


class IncidentRecord(Base):
    __tablename__ = "incidents"

    incident_id = Column(String(36), primary_key=True)
    status = Column(String(20), nullable=False, default="active")
    service = Column(String(100))
    severity = Column(String(20))
    alert_signature = Column(String(200))
    root_cause = Column(Text)
    fix_applied = Column(String(500))
    outcome = Column(String(20))
    retry_count = Column(Integer, default=0)
    time_to_recovery = Column(Float)
    total_cost_usd = Column(Float, default=0.0)
    hitl_required = Column(Boolean, default=False)
    hitl_decision = Column(String(20))
    hitl_decided_by = Column(String(100))
    state_snapshot = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String(36), index=True)
    action = Column(String(100))
    actor = Column(String(100))
    details = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserRecord(Base):
    __tablename__ = "users"

    username = Column(String(100), primary_key=True)
    hashed_password = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    role = Column(String(20), nullable=False, default="viewer")
    name = Column(String(150), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RevokedTokenRecord(Base):
    __tablename__ = "revoked_tokens"

    token_hash = Column(String(64), primary_key=True)
    token_type = Column(String(20), nullable=False, default="refresh")
    username = Column(String(100), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, default=datetime.utcnow)


def create_tables():
    Base.metadata.create_all(get_engine())
