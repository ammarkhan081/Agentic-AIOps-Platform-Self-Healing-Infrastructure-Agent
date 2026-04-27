"""
Incidents API routes.
POST /incidents       - trigger a manual incident pipeline run
GET  /incidents       - list all incidents
GET  /incidents/{id}  - get full incident detail
GET  /incidents/{id}/stream - WebSocket for real-time agent status
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...agents.learning import learning_agent
from ...core.metrics import (
    observe_hitl_intervention,
    observe_incident_detected,
    observe_incident_resolved,
)
from ...db.models import IncidentRecord
from ...db.session import get_db, get_session_factory
from ...db.store import (
    append_audit_event,
    list_audit_events,
    serialize_value,
    upsert_incident_record,
)
from ...graph.graph import get_graph
from ...graph.state import initial_state
from ...tools.slack_tool import send_timeout_notification
from ..routes.auth import get_current_user, get_current_user_from_token, require_role
from ..schemas import (
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentSummaryResponse,
    PostmortemResponse,
    TriggerIncidentResponse,
)

router = APIRouter()
logger = logging.getLogger("api.incidents")

_incidents: dict[str, dict] = {}
_ws_clients: dict[str, list[WebSocket]] = {}
_hitl_watchdogs: dict[str, asyncio.Task] = {}
HITL_TIMEOUT_MINUTES = int(os.getenv("HITL_TIMEOUT_MINUTES", "15"))
AUTO_MONITOR_ENABLED = os.getenv("AUTO_MONITOR_ENABLED", "true").strip().lower() == "true"
AUTO_MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_POLL_INTERVAL_SECONDS", "30"))
AUTO_MONITOR_ACTOR = os.getenv("AUTO_MONITOR_ACTOR", "ashia-monitor")
_auto_monitor_task: asyncio.Task | None = None
_auto_monitor_stop: asyncio.Event | None = None


class TriggerRequest(BaseModel):
    service: Optional[str] = None
    notes: Optional[str] = None
    source: Optional[str] = None


def _normalize_optional_iso_param(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_optional_text_param(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _incident_summary_from_record(row: IncidentRecord) -> dict:
    return IncidentSummaryResponse(
        incident_id=row.incident_id,
        status=row.status,
        service=row.service,
        severity=row.severity,
        created_at=row.created_at.isoformat() if row.created_at else "",
        resolved_at=row.resolved_at.isoformat() if row.resolved_at else None,
        time_to_recovery=row.time_to_recovery,
        retry_count=row.retry_count or 0,
        total_cost_usd=row.total_cost_usd or 0.0,
    ).model_dump()


def _incident_summary_from_memory(incident_id: str, data: dict) -> dict:
    state = data.get("state", {})
    alert = state.get("alert")
    return IncidentSummaryResponse(
        incident_id=incident_id,
        status=data.get("status", "unknown"),
        service=alert.service if alert else None,
        severity=alert.severity if alert else None,
        created_at=state.get("created_at", ""),
        resolved_at=state.get("resolved_at"),
        time_to_recovery=state.get("time_to_recovery"),
        retry_count=state.get("retry_count", 0),
        total_cost_usd=state.get("total_cost_usd", 0.0),
    ).model_dump()


def _memory_incident_matches_filters(
    item: dict,
    *,
    status: str | None,
    severity: str | None,
    service: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> bool:
    if status and item.get("status") != status:
        return False
    if severity and item.get("severity") != severity:
        return False
    if service and item.get("service") != service:
        return False

    created_at = item.get("created_at")
    if not created_at:
        return date_from is None and date_to is None

    try:
        created_at_dt = datetime.fromisoformat(created_at)
    except ValueError:
        return False

    if date_from and created_at_dt < date_from:
        return False
    if date_to and created_at_dt > date_to:
        return False
    return True


def _incident_detail_payload(
    incident_id: str, status: str, state: dict, events: list[dict]
) -> dict:
    alert = state.get("alert")
    serialized_alert = serialize_value(alert) if alert else None
    if (
        serialized_alert is not None
        and not isinstance(serialized_alert, dict)
        and hasattr(alert, "__dict__")
    ):
        serialized_alert = vars(alert)
    return IncidentDetailResponse(
        incident_id=incident_id,
        status=status,
        created_at=state.get("created_at"),
        resolved_at=state.get("resolved_at"),
        alert=serialized_alert,
        hypotheses=serialize_value(state.get("hypotheses", [])),
        fix_options=serialize_value(state.get("fix_options", [])),
        selected_fix=serialize_value(state.get("selected_fix"))
        if state.get("selected_fix")
        else None,
        execution_log=serialize_value(state.get("execution_log", [])),
        retry_count=state.get("retry_count", 0),
        current_hypothesis_idx=state.get("current_hypothesis_idx", 0),
        hitl_required=state.get("hitl_required", False),
        recovery_confirmed=state.get("recovery_confirmed"),
        time_to_recovery=state.get("time_to_recovery"),
        total_cost_usd=state.get("total_cost_usd", 0.0),
        error_message=state.get("error_message"),
        postmortem=serialize_value(state.get("postmortem")) if state.get("postmortem") else None,
        events=events,
        raw_metrics=state.get("raw_metrics", {}),
        past_incidents=serialize_value(state.get("past_incidents", [])),
    ).model_dump()


def _broadcast(incident_id: str, event: dict):
    clients = _ws_clients.get(incident_id, [])
    dead = []
    for ws in clients:
        try:
            asyncio.create_task(ws.send_json(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.remove(ws)


def _cancel_hitl_watchdog(incident_id: str) -> None:
    task = _hitl_watchdogs.pop(incident_id, None)
    if task and not task.done():
        task.cancel()


def _start_hitl_watchdog(incident_id: str, actor: str) -> None:
    _cancel_hitl_watchdog(incident_id)
    _hitl_watchdogs[incident_id] = asyncio.create_task(
        _hitl_timeout_watchdog(incident_id, actor, HITL_TIMEOUT_MINUTES * 60)
    )


async def _hitl_timeout_watchdog(incident_id: str, actor: str, timeout_seconds: int):
    try:
        await asyncio.sleep(timeout_seconds)
        data = _incidents.get(incident_id)
        if not data:
            return
        if data.get("status") != "paused":
            return

        state = data.get("state", {})
        escalated_state = {
            **state,
            "status": "escalated",
            "hitl_required": True,
            "error_message": f"HITL timeout exceeded ({timeout_seconds}s) without decision",
        }
        escalated_state = learning_agent(escalated_state)
        data["state"] = escalated_state
        data["status"] = "escalated"
        _persist_snapshot(incident_id, "escalated", escalated_state)
        send_timeout_notification(incident_id, escalated_state, timeout_seconds)

        event = {
            "type": "hitl_timeout",
            "incident_id": incident_id,
            "timestamp": datetime.utcnow().isoformat(),
            "message": "HITL timeout exceeded; incident escalated automatically",
            "timeout_seconds": timeout_seconds,
        }
        _persist_event(incident_id, "hitl_timeout", actor, event)
        _broadcast(incident_id, event)
    except asyncio.CancelledError:
        return
    finally:
        _hitl_watchdogs.pop(incident_id, None)


def _persist_snapshot(incident_id: str, status: str, state: dict):
    try:
        db = get_session_factory()()
        try:
            upsert_incident_record(db, incident_id, state, status)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Incident persistence failed for %s: %s", incident_id, exc)


def _persist_event(incident_id: str, event_type: str, actor: str, details: dict):
    try:
        db = get_session_factory()()
        try:
            append_audit_event(db, incident_id, event_type, actor, details)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Audit persistence failed for %s: %s", incident_id, exc)


async def _run_pipeline(
    incident_id: str,
    user_id: str,
    request: TriggerRequest,
    *,
    initial_graph_state: dict | None = None,
):
    graph = get_graph()
    state = initial_graph_state or initial_state(incident_id=incident_id)
    state["incident_id"] = incident_id
    state["user_id"] = user_id
    config = {"configurable": {"thread_id": incident_id}}

    _incidents[incident_id] = {"state": state, "status": "active"}
    _persist_snapshot(incident_id, "active", state)

    def emit(event_type: str, data: dict):
        event = {
            "type": event_type,
            "incident_id": incident_id,
            "timestamp": datetime.utcnow().isoformat(),
            **data,
        }
        _persist_event(incident_id, event_type, user_id, event)
        _broadcast(incident_id, event)

    emit(
        "pipeline_started",
        {
            "message": "AIOps pipeline initiated",
            "requested_service": request.service,
            "notes": request.notes,
            "source": request.source or "manual",
        },
    )
    observe_incident_detected()

    try:
        for chunk in graph.stream(state, config=config, stream_mode="values"):
            current_state = chunk
            _incidents[incident_id]["state"] = current_state

            node_status = current_state.get("status", "active")
            alert = current_state.get("alert")
            _incidents[incident_id]["status"] = node_status
            _persist_snapshot(incident_id, node_status, current_state)

            emit(
                "state_update",
                {
                    "status": node_status,
                    "service": alert.service if alert else None,
                    "severity": alert.severity if alert else None,
                    "hypotheses_count": len(current_state.get("hypotheses", [])),
                    "retry_count": current_state.get("retry_count", 0),
                    "hitl_required": current_state.get("hitl_required", False),
                },
            )

            if node_status == "paused":
                observe_hitl_intervention()
                _start_hitl_watchdog(incident_id, user_id)
                emit(
                    "hitl_required",
                    {
                        "message": "Human approval required. Check Slack or dashboard.",
                        "incident_id": incident_id,
                    },
                )
                return

        _cancel_hitl_watchdog(incident_id)
        final = _incidents[incident_id]["state"]
        final_status = final.get("status", "unknown")
        _incidents[incident_id]["status"] = final_status
        _persist_snapshot(incident_id, final_status, final)
        emit(
            "pipeline_complete",
            {
                "status": final_status,
                "time_to_recovery": final.get("time_to_recovery"),
                "retry_count": final.get("retry_count", 0),
            },
        )
        if final_status == "resolved":
            observe_incident_resolved(final.get("time_to_recovery"))
    except Exception as exc:
        _cancel_hitl_watchdog(incident_id)
        logger.error("Pipeline error for %s: %s", incident_id, exc)
        _incidents[incident_id]["status"] = "failed"
        _persist_snapshot(
            incident_id, "failed", {**_incidents[incident_id]["state"], "status": "failed"}
        )
        emit("pipeline_error", {"error": str(exc)})


async def _auto_monitor_loop():
    logger.info(
        "Automatic monitor loop started - interval=%ss enabled=%s",
        AUTO_MONITOR_INTERVAL_SECONDS,
        AUTO_MONITOR_ENABLED,
    )
    stop_event = _auto_monitor_stop
    if stop_event is None:
        return

    while not stop_event.is_set():
        cycle_started = asyncio.get_running_loop().time()
        try:
            preflight_state = initial_state()
            preflight_state["user_id"] = AUTO_MONITOR_ACTOR
            monitor_state = graph_preflight_monitor(preflight_state)
            alert = monitor_state.get("alert")
            if alert:
                incident_id = monitor_state["incident_id"]
                logger.info(
                    "Automatic monitor loop detected alert - incident_id=%s service=%s metric=%s severity=%s",
                    incident_id,
                    alert.service,
                    alert.metric_name,
                    alert.severity,
                )
                await _run_pipeline(
                    incident_id,
                    AUTO_MONITOR_ACTOR,
                    TriggerRequest(
                        service=alert.service,
                        notes="Automatic monitor poll detected anomaly",
                        source="auto-monitor",
                    ),
                    initial_graph_state=monitor_state,
                )
        except Exception as exc:
            logger.error("Automatic monitor loop iteration failed: %s", exc)

        elapsed = asyncio.get_running_loop().time() - cycle_started
        sleep_for = max(1.0, AUTO_MONITOR_INTERVAL_SECONDS - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            continue

    logger.info("Automatic monitor loop stopped")


def graph_preflight_monitor(state: dict) -> dict:
    """
    Run a lightweight monitor-only pass before creating a full incident session.
    This prevents the automatic scheduler from persisting empty incidents when
    there is no anomaly, while still allowing the full graph to start with an
    already-detected alert.
    """
    from ...agents.monitor import monitor_agent

    return monitor_agent(state)


async def start_automatic_monitor_loop() -> None:
    global _auto_monitor_task, _auto_monitor_stop
    if not AUTO_MONITOR_ENABLED or _auto_monitor_task is not None:
        return
    _auto_monitor_stop = asyncio.Event()
    _auto_monitor_task = asyncio.create_task(_auto_monitor_loop())


async def stop_automatic_monitor_loop() -> None:
    global _auto_monitor_task, _auto_monitor_stop
    if _auto_monitor_stop is not None:
        _auto_monitor_stop.set()
    task = _auto_monitor_task
    _auto_monitor_task = None
    _auto_monitor_stop = None
    if task:
        await task


@router.post("", status_code=202, response_model=TriggerIncidentResponse)
async def trigger_incident(
    req: TriggerRequest,
    background: BackgroundTasks,
    user=Depends(require_role("admin", "sre")),
):
    incident_id = str(uuid.uuid4())
    background.add_task(_run_pipeline, incident_id, user["username"], req)
    return TriggerIncidentResponse(
        incident_id=incident_id,
        status="initiated",
        stream_url=f"/api/v1/incidents/{incident_id}/stream",
    ).model_dump()


@router.get("", response_model=IncidentListResponse)
async def list_incidents(
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    service: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="ISO datetime"),
    date_to: Optional[str] = Query(default=None, description="ISO datetime"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    status_value = _normalize_optional_text_param(status)
    severity_value = _normalize_optional_text_param(severity)
    service_value = _normalize_optional_text_param(service)
    page_value = page if isinstance(page, int) else 1
    page_size_value = page_size if isinstance(page_size, int) else 50

    date_from_value = _normalize_optional_iso_param(date_from)
    date_to_value = _normalize_optional_iso_param(date_to)
    force_memory_fallback = False
    try:
        parsed_from = datetime.fromisoformat(date_from_value) if date_from_value else None
        parsed_to = datetime.fromisoformat(date_to_value) if date_to_value else None
    except ValueError as exc:
        logger.warning("Incident listing received invalid ISO date filter; falling back: %s", exc)
        parsed_from = None
        parsed_to = None
        force_memory_fallback = True

    try:
        if force_memory_fallback:
            raise RuntimeError("invalid date filter")
        query = db.query(IncidentRecord)
        if status_value:
            query = query.filter(IncidentRecord.status == status_value)
        if severity_value:
            query = query.filter(IncidentRecord.severity == severity_value)
        if service_value:
            query = query.filter(IncidentRecord.service == service_value)
        if parsed_from:
            query = query.filter(IncidentRecord.created_at >= parsed_from)
        if parsed_to:
            query = query.filter(IncidentRecord.created_at <= parsed_to)

        total = query.count()
        offset = (page_value - 1) * page_size_value
        rows = (
            query.order_by(IncidentRecord.created_at.desc())
            .offset(offset)
            .limit(page_size_value)
            .all()
        )
        return IncidentListResponse(
            incidents=[_incident_summary_from_record(row) for row in rows],
            pagination={
                "page": page_value,
                "page_size": page_size_value,
                "total": total,
            },
        ).model_dump()
    except Exception as exc:
        logger.warning("Incident listing fell back to in-memory state: %s", exc)
        result = [
            item
            for iid, data in _incidents.items()
            for item in [_incident_summary_from_memory(iid, data)]
            if _memory_incident_matches_filters(
                item,
                status=status_value,
                severity=severity_value,
                service=service_value,
                date_from=parsed_from,
                date_to=parsed_to,
            )
        ]
        sorted_result = sorted(result, key=lambda x: x["created_at"], reverse=True)
        offset = (page_value - 1) * page_size_value
        return IncidentListResponse(
            incidents=sorted_result[offset : offset + page_size_value],
            pagination={
                "page": page_value,
                "page_size": page_size_value,
                "total": len(sorted_result),
            },
        ).model_dump()


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
async def get_incident(
    incident_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = _incidents.get(incident_id)
    record = db.get(IncidentRecord, incident_id)
    if record:
        state = record.state_snapshot or (data["state"] if data else {})
        status = record.status
    elif data:
        state = data["state"]
        status = data["status"]
    else:
        raise HTTPException(status_code=404, detail="Incident not found")
    events = list_audit_events(db, incident_id)

    return _incident_detail_payload(incident_id, status, state, events)


@router.get("/{incident_id}/postmortem", response_model=PostmortemResponse)
async def get_incident_postmortem(
    incident_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = db.get(IncidentRecord, incident_id)
    if not record:
        raise HTTPException(status_code=404, detail="Incident not found")
    postmortem = (record.state_snapshot or {}).get("postmortem")
    if not postmortem:
        raise HTTPException(status_code=404, detail="Postmortem not available")
    return PostmortemResponse(**postmortem).model_dump()


@router.get("/{incident_id}/postmortem/export")
async def export_incident_postmortem(
    incident_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json|pdf)$"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = db.get(IncidentRecord, incident_id)
    if not record:
        raise HTTPException(status_code=404, detail="Incident not found")

    state = record.state_snapshot or {}
    postmortem = state.get("postmortem")
    if not postmortem:
        raise HTTPException(status_code=404, detail="Postmortem not available")

    if format == "json":
        return postmortem

    alert = state.get("alert") or {}
    hypotheses = state.get("hypotheses") or []
    execution_log = state.get("execution_log") or []
    events = list_audit_events(db, incident_id)

    markdown = f"""# Incident Postmortem - {postmortem.get("incident_id", incident_id)}

## Summary
- Service: {postmortem.get("service", "unknown")}
- Severity: {alert.get("severity", "N/A")}
- Outcome: {postmortem.get("outcome", "unknown")}
- Time to recovery: {postmortem.get("time_to_recovery_seconds", "N/A")}
- Retry count: {postmortem.get("retry_count", 0)}
- Total LLM cost: ${postmortem.get("total_cost_usd", 0.0):.4f}
- Created at: {postmortem.get("created_at", "")}

## Alert
{alert.get("description", "No alert details")}

## Root Cause
{postmortem.get("root_cause_confirmed", "N/A")}

## Fix Applied
`{postmortem.get("fix_applied", "none")}`

## Hypotheses
"""
    for idx, hyp in enumerate(hypotheses, start=1):
        markdown += (
            f"\n### {idx}. {hyp.get('description', '')}\n"
            f"- Confidence: {hyp.get('confidence', 0):.2f}\n"
            f"- Category: {hyp.get('suggested_fix_category', 'unknown')}\n"
            f"- Attempted: {hyp.get('attempted', False)}\n"
        )

    markdown += "\n## Execution Log\n"
    for action in execution_log:
        markdown += (
            f"- {action.get('executed_at', '')}: `{action.get('action_type', 'unknown')}` "
            f"=> {action.get('outcome', 'unknown')} ({action.get('duration_seconds', 0)}s)\n"
        )

    markdown += "\n## Timeline\n"
    for event in events:
        markdown += f"- {event.get('timestamp', '')} - {event.get('type', 'event')}\n"

    if format == "markdown":
        return PlainTextResponse(content=markdown, media_type="text/markdown")

    # format == pdf (best-effort runtime generation)
    try:
        from io import BytesIO

        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        y = 760
        for line in markdown.splitlines():
            if y < 40:
                pdf.showPage()
                y = 760
            pdf.drawString(40, y, line[:110])
            y -= 14
        pdf.save()
        data = buffer.getvalue()
        buffer.close()
        return Response(content=data, media_type="application/pdf")
    except Exception as exc:
        raise HTTPException(
            status_code=501,
            detail=f"PDF export unavailable in current runtime: {exc}",
        )


@router.websocket("/{incident_id}/stream")
async def stream_incident(incident_id: str, websocket: WebSocket):
    await websocket.accept()

    try:
        auth_message = await websocket.receive_json()
    except Exception:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing auth message",
        )
        return

    token = auth_message.get("token") if isinstance(auth_message, dict) else None
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing auth token")
        return

    try:
        db = get_session_factory()()
        try:
            get_current_user_from_token(token, db)
        finally:
            db.close()
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid auth token")
        return

    if incident_id not in _ws_clients:
        _ws_clients[incident_id] = []
    _ws_clients[incident_id].append(websocket)

    # Replay persisted timeline first (DB-backed), then stream live events.
    try:
        db = get_session_factory()()
        try:
            for event in list_audit_events(db, incident_id):
                await websocket.send_json(event)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Failed to replay incident timeline for %s: %s", incident_id, exc)

    try:
        while True:
            await asyncio.sleep(1)
            await websocket.send_json({"type": "ping", "incident_id": incident_id})
    except WebSocketDisconnect:
        if incident_id in _ws_clients and websocket in _ws_clients[incident_id]:
            _ws_clients[incident_id].remove(websocket)
