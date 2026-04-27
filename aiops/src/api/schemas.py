from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class APIModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class PaginationResponse(BaseModel):
    page: int
    page_size: int
    total: int


class TriggerIncidentResponse(BaseModel):
    incident_id: str
    status: str
    stream_url: str


class AlertResponse(APIModel):
    alert_id: str | None = None
    service: str | None = None
    metric_name: str | None = None
    current_value: float | None = None
    expected_mean: float | None = None
    expected_std: float | None = None
    threshold: float | None = None
    severity: str | None = None
    fired_at: str | None = None
    description: str | None = None


class IncidentSummaryResponse(BaseModel):
    incident_id: str
    status: str
    service: str | None = None
    severity: str | None = None
    created_at: str
    resolved_at: str | None = None
    time_to_recovery: float | None = None
    retry_count: int = 0
    total_cost_usd: float = 0.0


class IncidentListResponse(BaseModel):
    incidents: list[IncidentSummaryResponse]
    pagination: PaginationResponse


class AuditEventResponse(APIModel):
    type: str
    timestamp: str | None = None


class IncidentDetailResponse(BaseModel):
    incident_id: str
    status: str
    created_at: str | None = None
    resolved_at: str | None = None
    alert: AlertResponse | None = None
    hypotheses: list[dict[str, Any]]
    fix_options: list[dict[str, Any]]
    selected_fix: dict[str, Any] | None = None
    execution_log: list[dict[str, Any]]
    retry_count: int = 0
    current_hypothesis_idx: int = 0
    hitl_required: bool = False
    recovery_confirmed: bool | None = None
    time_to_recovery: float | None = None
    total_cost_usd: float = 0.0
    error_message: str | None = None
    postmortem: dict[str, Any] | None = None
    events: list[AuditEventResponse]
    raw_metrics: dict[str, Any]
    past_incidents: list[dict[str, Any]]


class PostmortemResponse(BaseModel):
    incident_id: str
    service: str
    alert_signature: str | None = None
    root_cause_confirmed: str
    fix_applied: str
    outcome: str
    time_to_recovery_seconds: float | None = None
    retry_count: int
    total_cost_usd: float
    created_at: str


class ReportItemResponse(PostmortemResponse):
    status: str | None = None


class ReportListResponse(BaseModel):
    reports: list[ReportItemResponse]
    pagination: PaginationResponse


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    uptime_seconds: int
    checks: dict[str, bool]


class MetricSummaryValueResponse(BaseModel):
    service: str
    query: str
    value: float | None = None
    verifier_query: str | None = None
    threshold_direction: str | None = None
    description: str | None = None
    minimum_samples: int | None = None
    minimum_absolute_delta: float | None = None
    minimum_relative_delta: float | None = None


class MetricsSummaryResponse(BaseModel):
    metrics: dict[str, MetricSummaryValueResponse]


class ObservabilityMetricStatusResponse(BaseModel):
    service: str
    description: str
    query: str
    verifier_query: str
    threshold_direction: str
    current_value: float | None = None
    expected_mean: float | None = None
    expected_std: float | None = None
    z_score: float | None = None
    minimum_samples: int
    minimum_absolute_delta: float
    minimum_relative_delta: float
    status: str
    baseline_window_hours: int
    evaluation_step: str


class ObservabilitySummaryResponse(BaseModel):
    metrics: dict[str, ObservabilityMetricStatusResponse]
    generated_at: str


class ControlPlaneMetricsResponse(BaseModel):
    metrics: dict[str, float]


class MonitorTriggerResponse(BaseModel):
    triggered: bool
    alert_fired: bool
    alert: AlertResponse | None = None
    raw_metrics: dict[str, Any]


class MemoryIncidentResponse(BaseModel):
    incident_id: str
    service: str
    status: str | None = None
    outcome: str | None = None
    created_at: str | None = None
    alert_signature: str | None = None
    similarity_score: float | None = None
    postmortem: PostmortemResponse | dict[str, Any] | None = None


class MemoryQueryMetadata(BaseModel):
    text: str | None = None
    service: str | None = None
    top_k: int
    mode: str


class MemoryIncidentListResponse(BaseModel):
    memory: dict[str, Any]
    incidents: list[MemoryIncidentResponse]
    total: int
    query: MemoryQueryMetadata


class DeleteMemoryIncidentResponse(BaseModel):
    deleted: bool
    incident_id: str


class ServiceStatusResponse(BaseModel):
    name: str
    ok: bool
    data: dict[str, Any]
    error: str | None = None


class DemoFaultStatusResponse(BaseModel):
    services: dict[str, ServiceStatusResponse]


class DemoFaultInjectResponse(BaseModel):
    queued: bool
    fault_type: str
    service: str
    message: str | None = None
    result: dict[str, Any] | None = None


class DemoFaultResetResponse(BaseModel):
    reset: bool
    services: dict[str, dict[str, Any]]


class DemoScenarioPrepareResponse(BaseModel):
    reset: bool
    reset_monitor: bool
    cooldown_seconds: int
    warmed_requests: dict[str, int]
    services: dict[str, dict[str, Any]]
    monitor: dict[str, Any] | None = None
