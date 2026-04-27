"""
Remediation Agent - Agent 03: Action
Takes top hypothesis from Root Cause Agent.
Generates fix options ranked by risk score.
Executes LOW-risk fixes automatically via Docker SDK.
MEDIUM/HIGH risk sets hitl_required=True for human approval.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from ..core.llm import get_chat_model
from ..graph.state import ActionResult, AIOpsState, FixOption, Hypothesis
from ..tools.docker_tool import docker
from ..tools.docker_tool import (
    config_patch_service,
    flush_cache,
    reset_db_connection_fault,
    reset_memory_fault,
    restart_container,
    rollback_service,
    scale_service,
)

logger = logging.getLogger("remediation-agent")

ACTION_RISK_MAP = {
    "restart_container": "LOW",
    "flush_cache": "LOW",
    "scale_up": "MEDIUM",
    "memory_limit_update": "MEDIUM",
    "config_patch": "MEDIUM",
    "image_rollback": "HIGH",
    "db_connection_reset": "HIGH",
    "db_query_kill": "HIGH",
    "manual_investigation": "HIGH",
}

SYSTEM_PROMPT = """You are a senior SRE generating remediation options for a production incident.
Given a root cause hypothesis, generate 2-3 fix options ranked by safety (safest first).

Return ONLY valid JSON array - no markdown, no preamble:
[
  {
    "fix_id": "f1",
    "action_type": "restart_container",
    "parameters": {"container": "order-service", "reason": "clear memory leak"},
    "risk_score": "LOW",
    "estimated_recovery_seconds": 30,
    "reasoning": "Container restart will clear the leaked memory. Service will be down ~10s."
  }
]

action_type must be one of:
restart_container, flush_cache, scale_up, memory_limit_update,
config_patch, image_rollback, db_connection_reset, db_query_kill, manual_investigation

risk_score must be: LOW, MEDIUM, or HIGH"""


def _risk_to_int(value: str) -> int:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(value, 2)


def _int_to_risk(value: int) -> str:
    return {0: "LOW", 1: "MEDIUM", 2: "HIGH"}.get(max(0, min(value, 2)), "HIGH")


def _compute_risk(action_type: str, service: str, retry_count: int) -> str:
    """
    Spec-aligned risk model based on:
    - action type
    - service criticality
    - time of day
    - retry count
    """
    base = _risk_to_int(ACTION_RISK_MAP.get(action_type, "HIGH"))

    # Service criticality: gateway and user-service are treated as higher-impact.
    if service in {"api-gateway", "user-service"}:
        base += 1

    # Time-of-day factor (UTC): production changes in business hours carry higher blast radius.
    utc_hour = datetime.utcnow().hour
    if 8 <= utc_hour <= 18 and action_type in {
        "image_rollback",
        "db_connection_reset",
        "db_query_kill",
        "config_patch",
    }:
        base += 1

    # Retry pressure: repeated failures raise risk.
    if retry_count >= 1:
        base += 1
    if retry_count >= 2:
        base += 1

    return _int_to_risk(base)


def _generate_fix_options(
    hypothesis: Hypothesis, alert_service: str, retry_count: int
) -> list[FixOption]:
    llm = get_chat_model(size="light", temperature=0.0, max_tokens=1024)
    prompt = f"""Hypothesis: {hypothesis.description}
Confidence: {hypothesis.confidence:.2f}
Fix category: {hypothesis.suggested_fix_category}
Affected service: {alert_service}
Evidence: {json.dumps(hypothesis.evidence)}

Generate 2-3 fix options ranked safest first."""

    for attempt in range(1, 3):
        try:
            response = llm.invoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            parsed = json.loads(raw)
            options: list[FixOption] = []
            for item in parsed:
                action = item.get("action_type", "manual_investigation")
                risk = _compute_risk(action, alert_service, retry_count)
                options.append(
                    FixOption(
                        fix_id=item.get("fix_id", f"f{len(options) + 1}"),
                        action_type=action,
                        parameters=item.get("parameters", {}),
                        risk_score=risk,
                        estimated_recovery_seconds=int(item.get("estimated_recovery_seconds", 60)),
                        reasoning=item.get("reasoning", ""),
                    )
                )

            options.sort(key=lambda o: _risk_to_int(o.risk_score))
            return options
        except Exception as exc:
            logger.warning("Remediation Agent: fix generation attempt %s failed: %s", attempt, exc)

    return [
        FixOption(
            fix_id="f_fallback",
            action_type="manual_investigation",
            parameters={"service": alert_service},
            risk_score="HIGH",
            estimated_recovery_seconds=300,
            reasoning="LLM failed to generate fix options. Manual investigation required.",
        )
    ]


def _execute_fix(fix: FixOption) -> ActionResult:
    started = datetime.utcnow()
    try:
        action = fix.action_type
        params = fix.parameters

        if action == "restart_container":
            service = params.get("container", "")
            response = restart_container(service)
            logger.info("Remediation: restarted container for %s", service)

        elif action == "flush_cache":
            response = flush_cache()
            logger.info("Remediation: Redis cache flushed")

        elif action == "scale_up":
            service = params.get("service", "order-service")
            replicas = int(params.get("replicas", 2))
            response = scale_service(service, replicas)
            logger.info("Remediation: scale_up executed for %s -> %s replicas", service, replicas)

        elif action == "memory_limit_update":
            service = params.get("container", "order-service")
            response = reset_memory_fault(service)
            logger.info("Remediation: memory fault reset on %s", service)

        elif action == "config_patch":
            service = params.get("service", "order-service")
            response = config_patch_service(service, params)
            logger.info("Remediation: config_patch executed for %s", service)

        elif action == "db_connection_reset":
            response = reset_db_connection_fault()
            logger.info("Remediation: DB connection fault reset")

        elif action == "image_rollback":
            service = params.get("service", "order-service")
            target_version = params.get("target_version", "v0.9.0")
            response = rollback_service(service, target_version)
            logger.info(
                "Remediation: image rollback executed for %s -> %s", service, target_version
            )

        else:
            response = f"Action {action} acknowledged - no automated executor for this type"
            logger.info("Remediation: no executor for action_type=%s", action)

        duration = (datetime.utcnow() - started).total_seconds()
        return ActionResult(
            action_type=action,
            parameters=params,
            executed_at=started.isoformat(),
            outcome="success",
            response=response,
            duration_seconds=round(duration, 2),
        )

    except Exception as exc:
        duration = (datetime.utcnow() - started).total_seconds()
        logger.error("Remediation: execution failed: %s", exc)
        return ActionResult(
            action_type=fix.action_type,
            parameters=fix.parameters,
            executed_at=started.isoformat(),
            outcome="failure",
            response=str(exc),
            duration_seconds=round(duration, 2),
        )


def remediation_agent(state: AIOpsState) -> AIOpsState:
    """
    Remediation Agent node.
    Reads: hypotheses, current_hypothesis_idx, alert
    Writes: fix_options, selected_fix, execution_log, hitl_required
    """
    hypotheses = state.get("hypotheses", [])
    idx = state.get("current_hypothesis_idx", 0)
    alert = state.get("alert")
    retry_count = state.get("retry_count", 0)

    if not hypotheses or idx >= len(hypotheses):
        logger.error("Remediation Agent: no hypothesis to work with")
        return {**state, "hitl_required": True, "error_message": "No hypothesis available"}

    hypothesis = hypotheses[idx]
    hypothesis.attempted = True
    logger.info(
        "Remediation Agent: working on hypothesis '%s' confidence=%.2f - %s",
        hypothesis.hypothesis_id,
        hypothesis.confidence,
        hypothesis.description[:80],
    )

    fix_options = _generate_fix_options(
        hypothesis, alert.service if alert else "unknown", retry_count
    )
    best_fix = fix_options[0] if fix_options else None

    if not best_fix:
        return {**state, "fix_options": [], "hitl_required": True}

    logger.info(
        "Remediation Agent: best fix = %s risk=%s", best_fix.action_type, best_fix.risk_score
    )

    if best_fix.risk_score in ("MEDIUM", "HIGH"):
        logger.info("Remediation Agent: %s risk - triggering HITL", best_fix.risk_score)
        return {
            **state,
            "fix_options": fix_options,
            "selected_fix": best_fix,
            "hitl_required": True,
        }

    logger.info("Remediation Agent: LOW risk - auto-executing %s", best_fix.action_type)
    result = _execute_fix(best_fix)
    execution_log = list(state.get("execution_log", [])) + [result]

    return {
        **state,
        "fix_options": fix_options,
        "selected_fix": best_fix,
        "execution_log": execution_log,
        "hitl_required": False,
    }
