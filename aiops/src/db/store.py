"""
Persistence helpers for incidents, audit events, and snapshot serialization.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, IncidentRecord


def serialize_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    return value


def build_incident_projection(state: dict, status: str) -> dict[str, Any]:
    alert = state.get("alert") or {}
    postmortem = state.get("postmortem") or {}
    selected_fix = state.get("selected_fix") or {}
    hitl_response = state.get("hitl_response") or {}

    if hasattr(alert, "service"):
        alert = serialize_value(alert)
    if hasattr(postmortem, "incident_id"):
        postmortem = serialize_value(postmortem)
    if hasattr(selected_fix, "fix_id"):
        selected_fix = serialize_value(selected_fix)
    if hasattr(hitl_response, "decision"):
        hitl_response = serialize_value(hitl_response)

    created_at_raw = state.get("created_at")
    resolved_at_raw = state.get("resolved_at")
    created_at = _parse_dt(created_at_raw) if created_at_raw else datetime.utcnow()
    resolved_at = _parse_dt(resolved_at_raw) if resolved_at_raw else None

    return {
        "status": status,
        "service": alert.get("service") or postmortem.get("service"),
        "severity": alert.get("severity"),
        "alert_signature": postmortem.get("alert_signature")
        or (
            f"{alert.get('service')}:{alert.get('metric_name')}:{alert.get('severity')}"
            if alert.get("service") and alert.get("metric_name") and alert.get("severity")
            else None
        ),
        "root_cause": postmortem.get("root_cause_confirmed"),
        "fix_applied": postmortem.get("fix_applied") or selected_fix.get("action_type"),
        "outcome": postmortem.get("outcome"),
        "retry_count": state.get("retry_count", 0),
        "time_to_recovery": state.get("time_to_recovery"),
        "total_cost_usd": state.get("total_cost_usd", 0.0),
        "hitl_required": state.get("hitl_required", False),
        "hitl_decision": hitl_response.get("decision"),
        "hitl_decided_by": hitl_response.get("decided_by"),
        "state_snapshot": serialize_value(state),
        "created_at": created_at,
        "resolved_at": resolved_at,
    }


def upsert_incident_record(
    db: Session, incident_id: str, state: dict, status: str
) -> IncidentRecord:
    projection = build_incident_projection(state, status)
    record = db.get(IncidentRecord, incident_id)
    if record is None:
        record = IncidentRecord(incident_id=incident_id, **projection)
        db.add(record)
    else:
        for key, value in projection.items():
            setattr(record, key, value)
    db.commit()
    db.refresh(record)
    return record


def append_audit_event(
    db: Session,
    incident_id: str,
    action: str,
    actor: str,
    details: dict[str, Any],
) -> AuditLog:
    entry = AuditLog(
        incident_id=incident_id,
        action=action,
        actor=actor,
        details=serialize_value(details),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_audit_events(db: Session, incident_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.incident_id == incident_id)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .all()
    )
    return [
        {
            "type": row.action,
            "incident_id": row.incident_id,
            "timestamp": row.created_at.isoformat(),
            **(row.details or {}),
        }
        for row in rows
    ]


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)
