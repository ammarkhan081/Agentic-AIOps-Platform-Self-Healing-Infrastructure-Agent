"""
HITL Routes - Human-in-the-Loop decision endpoints.
POST /incidents/{id}/hitl - submit human decision to resume paused graph.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...agents.learning import learning_agent
from ...core.metrics import observe_hitl_intervention
from ...db.session import get_session_factory
from ...db.store import append_audit_event
from ...graph.graph import get_graph
from ...graph.state import HumanDecision
from ..routes.auth import require_role
from .incidents import (
    _broadcast,
    _cancel_hitl_watchdog,
    _incidents,
    _persist_snapshot,
    _start_hitl_watchdog,
)

router = APIRouter()
logger = logging.getLogger("api.hitl")


class HITLDecisionRequest(BaseModel):
    decision: Literal["approve", "override", "abort"]
    custom_instruction: Optional[str] = None
    reason: Optional[str] = ""


def _persist_hitl_event(incident_id: str, actor: str, action: str, payload: dict):
    try:
        db = get_session_factory()()
        try:
            append_audit_event(db, incident_id, action, actor, payload)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Failed to persist HITL event for %s: %s", incident_id, exc)


@router.post("/{incident_id}/hitl")
async def submit_hitl_decision(
    incident_id: str,
    body: HITLDecisionRequest,
    user=Depends(require_role("admin", "sre")),
):
    data = _incidents.get(incident_id)
    if not data:
        raise HTTPException(status_code=404, detail="Incident not found")

    if data.get("status") != "paused":
        raise HTTPException(
            status_code=409,
            detail=f"Incident is not paused (status: {data.get('status')}). Cannot submit HITL decision.",
        )

    decision = HumanDecision(
        decision=body.decision,
        custom_instruction=body.custom_instruction,
        decided_by=user["username"],
        decided_at=datetime.utcnow().isoformat(),
        reason=body.reason or "",
    )

    logger.info(
        "HITL decision received: incident=%s decision=%s by=%s",
        incident_id,
        body.decision,
        user["username"],
    )

    def emit(event_type: str, event_data: dict):
        event = {
            "type": event_type,
            "incident_id": incident_id,
            "timestamp": datetime.utcnow().isoformat(),
            **event_data,
        }
        _persist_hitl_event(incident_id, user["username"], event_type, event)
        _broadcast(incident_id, event)

    graph = get_graph()
    config = {"configurable": {"thread_id": incident_id}}
    state = data["state"]

    updated_state = {**state, "hitl_response": decision, "status": "active", "hitl_required": False}
    data["state"] = updated_state
    data["status"] = "active"
    _cancel_hitl_watchdog(incident_id)
    _persist_snapshot(incident_id, "active", updated_state)

    emit(
        "hitl_decision",
        {
            "decision": body.decision,
            "decided_by": user["username"],
            "reason": body.reason or "",
        },
    )

    if body.decision == "abort":
        learned_state = learning_agent(
            {**updated_state, "status": "escalated", "hitl_required": False}
        )
        data["state"] = learned_state
        data["status"] = learned_state.get("status", "escalated")
        _persist_snapshot(incident_id, data["status"], learned_state)
        emit(
            "pipeline_complete",
            {
                "status": data["status"],
                "time_to_recovery": learned_state.get("time_to_recovery"),
                "retry_count": learned_state.get("retry_count", 0),
            },
        )
        return {"message": "Incident aborted", "incident_id": incident_id, "status": data["status"]}

    try:
        for chunk in graph.stream(updated_state, config=config, stream_mode="values"):
            final_state = chunk
            data["state"] = final_state
            data["status"] = final_state.get("status", "active")
            _persist_snapshot(incident_id, data["status"], final_state)

            alert = final_state.get("alert")
            emit(
                "state_update",
                {
                    "status": data["status"],
                    "service": alert.service if alert else None,
                    "severity": alert.severity if alert else None,
                    "hypotheses_count": len(final_state.get("hypotheses", [])),
                    "retry_count": final_state.get("retry_count", 0),
                    "hitl_required": final_state.get("hitl_required", False),
                },
            )
            if data["status"] == "paused":
                observe_hitl_intervention()
                _start_hitl_watchdog(incident_id, user["username"])
                emit(
                    "hitl_required",
                    {
                        "message": "Human approval required. Check Slack or dashboard.",
                        "incident_id": incident_id,
                    },
                )
                return {
                    "message": f"Decision '{body.decision}' applied. Pipeline paused again.",
                    "incident_id": incident_id,
                    "status": "paused",
                }
    except Exception as exc:
        logger.error("Graph resume error for %s: %s", incident_id, exc)
        data["status"] = "failed"
        failed_state = {**data["state"], "status": "failed"}
        data["state"] = failed_state
        _persist_snapshot(incident_id, "failed", failed_state)
        emit("pipeline_error", {"error": str(exc)})
        return {"message": f"Graph resume failed: {exc}", "incident_id": incident_id}

    final_state = data["state"]
    _persist_snapshot(incident_id, data["status"], final_state)
    emit(
        "pipeline_complete",
        {
            "status": final_state.get("status"),
            "time_to_recovery": final_state.get("time_to_recovery"),
            "retry_count": final_state.get("retry_count", 0),
        },
    )

    return {
        "message": f"Decision '{body.decision}' applied. Pipeline resumed.",
        "incident_id": incident_id,
        "status": data["status"],
    }
