"""
Slack webhook integration for HITL notifications.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import httpx

from ..core.config import get_settings
from ..graph.state import AIOpsState

logger = logging.getLogger("tool.slack")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def _dashboard_link(incident_id: str, decision: str | None = None) -> str:
    base = get_settings().frontend_base_url.rstrip("/")
    if decision:
        return f"{base}/incidents/{incident_id}?decision={decision}"
    return f"{base}/incidents/{incident_id}"


def build_hitl_message(state: AIOpsState) -> dict:
    alert = state.get("alert")
    hypotheses = state.get("hypotheses", [])
    idx = state.get("current_hypothesis_idx", 0)
    fix = state.get("selected_fix")
    hypothesis = hypotheses[idx] if hypotheses and idx < len(hypotheses) else None

    text = [
        "ASHIA human approval required",
        f"Incident: {state.get('incident_id', 'unknown')}",
        f"Service: {alert.service if alert else 'unknown'}",
        f"Severity: {alert.severity if alert else 'UNKNOWN'}",
        f"Retry count: {state.get('retry_count', 0)}",
    ]
    if hypothesis:
        text.append(f"Hypothesis: {hypothesis.description}")
    if fix:
        text.append(f"Proposed fix: {fix.action_type} ({fix.risk_score})")
    text.append(f"Generated at: {datetime.utcnow().isoformat()}Z")

    summary = "\n".join(text)
    incident_id = state.get("incident_id", "unknown")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "ASHIA human approval required"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary},
        },
    ]

    if hypothesis or fix:
        detail_lines = []
        if hypothesis:
            detail_lines.append(f"*Hypothesis*\n{hypothesis.description}")
        if fix:
            detail_lines.append(f"*Fix*\n`{fix.action_type}` ({fix.risk_score})")
        blocks.append(
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": line} for line in detail_lines[:2]],
            }
        )

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve in Dashboard"},
                    "style": "primary",
                    "url": _dashboard_link(incident_id, "approve"),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Override in Dashboard"},
                    "url": _dashboard_link(incident_id, "override"),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Abort in Dashboard"},
                    "style": "danger",
                    "url": _dashboard_link(incident_id, "abort"),
                },
            ],
        }
    )

    return {"text": summary, "blocks": blocks}


def build_timeout_message(incident_id: str, state: AIOpsState, timeout_seconds: int) -> dict:
    alert = state.get("alert")
    text = [
        "ASHIA incident escalated after HITL timeout",
        f"Incident: {incident_id}",
        f"Service: {alert.service if alert else 'unknown'}",
        f"Severity: {alert.severity if alert else 'UNKNOWN'}",
        f"Timeout: {timeout_seconds}s without operator decision",
        f"Status: {state.get('status', 'escalated')}",
        f"Generated at: {datetime.utcnow().isoformat()}Z",
    ]
    summary = "\n".join(text)
    return {
        "text": summary,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": summary},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open Incident"},
                        "url": _dashboard_link(incident_id),
                    }
                ],
            },
        ],
    }


def send_hitl_notification(state: AIOpsState) -> bool:
    if not SLACK_WEBHOOK_URL:
        logger.warning("Slack webhook not configured; skipping HITL notification")
        return False
    try:
        response = httpx.post(SLACK_WEBHOOK_URL, json=build_hitl_message(state), timeout=10.0)
        ok = response.status_code == 200
        if not ok:
            logger.warning(
                "Slack webhook returned status %s: %s", response.status_code, response.text
            )
        return ok
    except Exception as exc:
        logger.error("Slack notification failed: %s", exc)
        return False


def send_timeout_notification(incident_id: str, state: AIOpsState, timeout_seconds: int) -> bool:
    if not SLACK_WEBHOOK_URL:
        logger.warning("Slack webhook not configured; skipping timeout escalation notification")
        return False
    try:
        response = httpx.post(
            SLACK_WEBHOOK_URL,
            json=build_timeout_message(incident_id, state, timeout_seconds),
            timeout=10.0,
        )
        ok = response.status_code == 200
        if not ok:
            logger.warning(
                "Slack webhook returned status %s: %s", response.status_code, response.text
            )
        return ok
    except Exception as exc:
        logger.error("Slack timeout escalation notification failed: %s", exc)
        return False
