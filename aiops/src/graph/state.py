"""
AIOpsState — the single source of truth flowing through every LangGraph node.
Every agent reads from and writes to specific fields only.
All fields are optional at init; agents populate them as the graph progresses.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional, TypedDict

# ── Supporting Data Classes ────────────────────────────────────────


@dataclass
class AlertEvent:
    """Fired by Monitor Agent when anomaly is detected."""

    alert_id: str
    service: str
    metric_name: str
    current_value: float
    expected_mean: float
    expected_std: float
    threshold: float
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    fired_at: str
    description: str

    @classmethod
    def create(
        cls,
        service: str,
        metric_name: str,
        current: float,
        mean: float,
        std: float,
        threshold: float,
        severity: str,
    ) -> "AlertEvent":
        z = abs(current - mean) / std if std > 0 else 0
        return cls(
            alert_id=str(uuid.uuid4())[:8],
            service=service,
            metric_name=metric_name,
            current_value=round(current, 4),
            expected_mean=round(mean, 4),
            expected_std=round(std, 4),
            threshold=threshold,
            severity=severity,
            fired_at=datetime.utcnow().isoformat(),
            description=f"{metric_name} on {service} is {current:.3f} (z-score: {z:.1f}σ, expected: {mean:.3f}±{std:.3f})",
        )


@dataclass
class LogLine:
    timestamp: str
    level: str
    message: str
    service: str
    raw: str = ""


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    operation_name: str
    service: str
    duration_ms: float
    status: str
    tags: dict = field(default_factory=dict)


@dataclass
class PastIncident:
    incident_id: str
    service: str
    alert_signature: str
    root_cause: str
    fix_applied: str
    outcome: str
    time_to_recovery_seconds: float
    similarity_score: float
    occurred_at: str


@dataclass
class Hypothesis:
    hypothesis_id: str
    description: str
    confidence: float  # 0.0 – 1.0
    evidence: list  # list of strings
    suggested_fix_category: Literal[
        "restart", "scale", "rollback", "config", "db", "cache", "unknown"
    ]
    attempted: bool = False


@dataclass
class FixOption:
    fix_id: str
    action_type: str
    parameters: dict
    risk_score: Literal["LOW", "MEDIUM", "HIGH"]
    estimated_recovery_seconds: int
    reasoning: str


@dataclass
class ActionResult:
    action_type: str
    parameters: dict
    executed_at: str
    outcome: Literal["success", "failure", "skipped"]
    response: str
    duration_seconds: float


@dataclass
class HumanDecision:
    decision: Literal["approve", "override", "abort"]
    custom_instruction: Optional[str]
    decided_by: str
    decided_at: str
    reason: str = ""


@dataclass
class Postmortem:
    incident_id: str
    service: str
    alert_signature: str
    root_cause_confirmed: str
    fix_applied: str
    outcome: Literal["resolved", "escalated", "failed"]
    time_to_recovery_seconds: Optional[float]
    retry_count: int
    total_cost_usd: float
    created_at: str


# ── Main State ────────────────────────────────────────────────────


class AIOpsState(TypedDict):
    # ── Session metadata ──────────────────────────────────────────
    incident_id: str
    user_id: Optional[str]
    created_at: str

    # ── Monitor Agent output ──────────────────────────────────────
    alert: Optional[AlertEvent]
    raw_metrics: dict  # snapshot of all metrics at alert time

    # ── Root Cause Agent inputs & output ─────────────────────────
    logs: list  # list[LogLine]
    traces: list  # list[TraceSpan]
    past_incidents: list  # list[PastIncident] from ChromaDB
    hypotheses: list  # list[Hypothesis] ranked by confidence
    current_hypothesis_idx: int  # which hypothesis is being tried

    # ── Remediation Agent output ──────────────────────────────────
    fix_options: list  # list[FixOption]
    selected_fix: Optional[FixOption]
    execution_log: list  # list[ActionResult]

    # ── Verifier Agent output ─────────────────────────────────────
    recovery_confirmed: Optional[bool]
    recovery_metrics: dict

    # ── Control flow ─────────────────────────────────────────────
    retry_count: int
    hitl_required: bool
    hitl_response: Optional[HumanDecision]

    # ── Learning Agent output ─────────────────────────────────────
    postmortem: Optional[Postmortem]

    # ── Final state ───────────────────────────────────────────────
    status: Literal["active", "paused", "resolved", "escalated", "failed"]
    resolved_at: Optional[str]
    time_to_recovery: Optional[float]
    total_cost_usd: float
    error_message: Optional[str]


def initial_state(incident_id: Optional[str] = None) -> AIOpsState:
    """Returns a fresh AIOpsState ready for a new incident."""
    return AIOpsState(
        incident_id=incident_id or str(uuid.uuid4()),
        user_id=None,
        created_at=datetime.utcnow().isoformat(),
        alert=None,
        raw_metrics={},
        logs=[],
        traces=[],
        past_incidents=[],
        hypotheses=[],
        current_hypothesis_idx=0,
        fix_options=[],
        selected_fix=None,
        execution_log=[],
        recovery_confirmed=None,
        recovery_metrics={},
        retry_count=0,
        hitl_required=False,
        hitl_response=None,
        postmortem=None,
        status="active",
        resolved_at=None,
        time_to_recovery=None,
        total_cost_usd=0.0,
        error_message=None,
    )
