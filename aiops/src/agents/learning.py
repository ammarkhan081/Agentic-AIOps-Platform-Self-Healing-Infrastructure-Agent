"""
Learning Agent - Agent 05: Memory
Runs on every incident close (resolved or escalated).
Persists the full postmortem into the configured incident-memory store.
Enables Root Cause Agent to retrieve similar past incidents semantically.
This is the continual learning loop - system gets faster on repeated faults.
"""

import logging
from datetime import datetime

from ..graph.state import AIOpsState, Postmortem
from ..tools.chroma_tool import upsert_incident

logger = logging.getLogger("learning-agent")


def learning_agent(state: AIOpsState) -> AIOpsState:
    """
    Learning Agent node.
    Reads:  incident_id, alert, hypotheses, selected_fix, execution_log,
            recovery_confirmed, retry_count, created_at, total_cost_usd
    Writes: postmortem, status, resolved_at, time_to_recovery
    """
    alert = state.get("alert")
    hypotheses = state.get("hypotheses", [])
    idx = state.get("current_hypothesis_idx", 0)
    selected_fix = state.get("selected_fix")
    recovery = state.get("recovery_confirmed")
    retry_count = state.get("retry_count", 0)
    created_at_str = state.get("created_at", datetime.utcnow().isoformat())
    total_cost = state.get("total_cost_usd", 0.0)

    if recovery:
        outcome = "resolved"
        final_status = "resolved"
    elif state.get("hitl_required") and not state.get("hitl_response"):
        outcome = "escalated"
        final_status = "escalated"
    else:
        outcome = "escalated"
        final_status = "escalated"

    resolved_at = datetime.utcnow()
    try:
        created_at = datetime.fromisoformat(created_at_str)
        elapsed = (resolved_at - created_at).total_seconds()
    except Exception:
        elapsed = None

    confirmed_hypothesis = hypotheses[idx] if hypotheses and idx < len(hypotheses) else None
    root_cause_text = confirmed_hypothesis.description if confirmed_hypothesis else "Unknown"
    fix_text = selected_fix.action_type if selected_fix else "none"
    if selected_fix and selected_fix.parameters:
        fix_text += f" ({selected_fix.parameters})"

    alert_signature = (
        f"{alert.service}:{alert.metric_name}:{alert.severity}"
        if alert
        else "unknown:unknown:unknown"
    )

    postmortem = Postmortem(
        incident_id=state["incident_id"],
        service=alert.service if alert else "unknown",
        alert_signature=alert_signature,
        root_cause_confirmed=root_cause_text,
        fix_applied=fix_text,
        outcome=outcome,
        time_to_recovery_seconds=round(elapsed, 1) if elapsed else None,
        retry_count=retry_count,
        total_cost_usd=round(total_cost, 4),
        created_at=resolved_at.isoformat(),
    )

    try:
        upsert_incident(postmortem)
        logger.info(
            f"Learning Agent: postmortem written to incident memory - "
            f"incident_id={postmortem.incident_id} outcome={outcome} "
            f"ttrecovery={elapsed:.1f}s"
            if elapsed
            else f"Learning Agent: postmortem written - {postmortem.incident_id}"
        )
    except Exception as exc:
        logger.error(f"Learning Agent: incident-memory upsert failed: {exc}")

    logger.info(
        f"Learning Agent: incident closed - status={final_status} "
        f"retries={retry_count} outcome={outcome}"
    )

    return {
        **state,
        "postmortem": postmortem,
        "status": final_status,
        "resolved_at": resolved_at.isoformat(),
        "time_to_recovery": elapsed,
    }
