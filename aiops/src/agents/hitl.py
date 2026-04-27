"""
HITL Supervisor - Agent 06: Oversight
Fires when Remediation Agent sets hitl_required=True.
Sends Slack notification with full incident context.
On resume, executes approve/override decisions before verifier continues.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..agents.remediation import _execute_fix
from ..graph.state import ActionResult, AIOpsState, FixOption
from ..tools.slack_tool import send_hitl_notification

logger = logging.getLogger("hitl-supervisor")


def _override_to_fix(custom_instruction: str, state: AIOpsState) -> FixOption:
    instruction = (custom_instruction or "").strip().lower()
    alert = state.get("alert")
    service = alert.service if alert else "order-service"

    # Minimal deterministic parser for override intents.
    if "restart" in instruction:
        action = "restart_container"
        params = {"container": service, "reason": custom_instruction}
        risk = "MEDIUM"
    elif "cache" in instruction and ("flush" in instruction or "clear" in instruction):
        action = "flush_cache"
        params = {"reason": custom_instruction}
        risk = "MEDIUM"
    elif "db" in instruction and ("reset" in instruction or "pool" in instruction):
        action = "db_connection_reset"
        params = {"service": "user-service", "reason": custom_instruction}
        risk = "HIGH"
    elif "rollback" in instruction:
        action = "image_rollback"
        params = {"service": service, "reason": custom_instruction}
        risk = "HIGH"
    else:
        action = "manual_investigation"
        params = {"service": service, "instruction": custom_instruction}
        risk = "HIGH"

    return FixOption(
        fix_id=f"override-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        action_type=action,
        parameters=params,
        risk_score=risk,
        estimated_recovery_seconds=90,
        reasoning=f"Operator override: {custom_instruction}",
    )


def hitl_supervisor(state: AIOpsState) -> AIOpsState:
    """
    HITL Supervisor node.
    Reads: alert, hypotheses, selected_fix, retry_count, hitl_response
    Writes: status, hitl_required, selected_fix, execution_log
    """
    logger.info(
        "HITL Supervisor: preparing interrupt for incident %s - fix=%s retry=%s",
        state.get("incident_id"),
        state.get("selected_fix").action_type if state.get("selected_fix") else "none",
        state.get("retry_count", 0),
    )

    # First visit: notify and pause.
    if not state.get("hitl_response"):
        send_hitl_notification(state)
        return {**state, "status": "paused", "hitl_required": True}

    # Resume path: execute the human decision.
    hitl_response = state.get("hitl_response")
    decision = (
        hitl_response.decision
        if hasattr(hitl_response, "decision")
        else hitl_response.get("decision")
    )
    custom_instruction = (
        hitl_response.custom_instruction
        if hasattr(hitl_response, "custom_instruction")
        else hitl_response.get("custom_instruction")
    )
    logger.info("HITL Supervisor: human decision received - %s", decision)

    if decision == "abort":
        return {**state, "status": "escalated", "hitl_required": False}

    selected_fix = state.get("selected_fix")
    if decision == "override" and custom_instruction:
        selected_fix = _override_to_fix(custom_instruction, state)

    execution_log = list(state.get("execution_log", []))
    if selected_fix:
        result = _execute_fix(selected_fix)
        execution_log.append(result)
    else:
        execution_log.append(
            ActionResult(
                action_type="none",
                parameters={},
                executed_at=datetime.utcnow().isoformat(),
                outcome="skipped",
                response="No fix selected during HITL resume",
                duration_seconds=0.0,
            )
        )

    return {
        **state,
        "status": "active",
        "hitl_required": False,
        "selected_fix": selected_fix,
        "execution_log": execution_log,
    }
