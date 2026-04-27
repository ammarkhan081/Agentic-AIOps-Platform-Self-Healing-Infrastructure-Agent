"""
Root Cause Agent - Agent 02: Reasoning
Assembles context from Loki logs, Jaeger traces, and incident memory.
Uses the configured LLM provider (Groq/OpenAI) to generate ranked hypotheses.
"""

from __future__ import annotations

import json
import logging
import os
from hashlib import sha256

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from ..core.llm import get_chat_model
from ..graph.state import AIOpsState, Hypothesis
from ..tools.jaeger_tool import fetch_traces
from ..tools.loki_tool import fetch_logs
from ..tools.chroma_tool import search_similar_incidents

logger = logging.getLogger("root-cause-agent")

MAX_LOG_LINES = 50
MAX_TRACES = 20
MAX_PAST = 3
MAX_LLM_RETRIES = 2
TOKEN_BUDGET = int(os.getenv("ROOT_CAUSE_TOKEN_BUDGET", "12000"))
ROOT_CAUSE_CACHE_TTL = int(os.getenv("ROOT_CAUSE_CACHE_TTL_SECONDS", "3600"))


class HypothesisPayload(BaseModel):
    hypothesis_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str]
    suggested_fix_category: str


SYSTEM_PROMPT = """You are a senior Site Reliability Engineer performing root cause analysis.
You will receive:
1. An alert event with metric anomaly details
2. Recent log lines from the affected service (JSON format)
3. Recent distributed trace spans
4. Similar past incidents from memory

Your job: reason step by step and produce a ranked JSON list of root cause hypotheses.

RULES:
- Return ONLY valid JSON - no markdown, no code fences, no preamble
- Every hypothesis must have ALL required fields
- Rank by confidence score (highest first)
- confidence must be a float between 0.0 and 1.0
- suggested_fix_category must be one of: restart, scale, rollback, config, db, cache, unknown
- evidence must reference specific log lines, trace data, or metric values
- Generate 2-4 hypotheses minimum

OUTPUT FORMAT (return this exact JSON structure):
[
  {
    "hypothesis_id": "h1",
    "description": "Detailed description of what went wrong and why",
    "confidence": 0.87,
    "evidence": ["log line X shows Y", "trace span Z shows elevated duration", "metric W exceeded threshold"],
    "suggested_fix_category": "restart",
    "reasoning": "Step-by-step chain of thought explaining your conclusion"
  }
]"""


def _approx_tokens(text: str) -> int:
    # Fast approximation; avoids model-specific tokenization in hot path.
    return max(1, len(text) // 4)


def _build_user_prompt(alert, logs, traces, past) -> str:

    alert_str = json.dumps(
        {
            "service": alert.service,
            "metric": alert.metric_name,
            "current_value": alert.current_value,
            "expected_mean": alert.expected_mean,
            "expected_std": alert.expected_std,
            "severity": alert.severity,
            "description": alert.description,
            "fired_at": alert.fired_at,
        },
        indent=2,
    )

    log_str = (
        "\n".join([f"[{l.timestamp}] [{l.level}] {l.message}" for l in logs[:MAX_LOG_LINES]])
        or "No log data available"
    )

    trace_str = (
        "\n".join(
            [
                f"span={t.span_id} op={t.operation_name} svc={t.service} "
                f"duration={t.duration_ms:.0f}ms status={t.status}"
                for t in traces[:MAX_TRACES]
            ]
        )
        or "No trace data available"
    )

    past_str = (
        "\n".join(
            [
                f"Past incident {i + 1}: service={p.service} root_cause='{p.root_cause}' "
                f"fix='{p.fix_applied}' outcome={p.outcome} similarity={p.similarity_score:.2f}"
                for i, p in enumerate(past[:MAX_PAST])
            ]
        )
        or "No similar past incidents found"
    )

    return f"""=== ALERT ===
{alert_str}

=== RECENT LOGS ({len(logs)} lines) ===
{log_str}

=== DISTRIBUTED TRACES ({len(traces)} spans) ===
{trace_str}

=== SIMILAR PAST INCIDENTS ===
{past_str}

Analyze the above and return the JSON hypothesis list."""


def _build_user_prompt_with_budget(alert, logs, traces, past, token_budget: int) -> str:
    work_logs = list(logs)
    work_traces = list(traces)
    work_past = list(past)

    prompt = _build_user_prompt(alert, work_logs, work_traces, work_past)
    while _approx_tokens(prompt) > token_budget and (work_logs or work_traces or work_past):
        # Drop least-critical context first: logs, then traces, then past incidents.
        if work_logs:
            work_logs = work_logs[:-5]
        elif work_traces:
            work_traces = work_traces[:-2]
        elif work_past:
            work_past = work_past[:-1]
        prompt = _build_user_prompt(alert, work_logs, work_traces, work_past)

    return prompt


def _cache_key(alert, prompt: str) -> str:
    digest = sha256(prompt.encode("utf-8")).hexdigest()
    return f"ashia:root-cause:{alert.service}:{alert.metric_name}:{digest}"


def _get_cached_hypotheses(cache_key: str) -> list[Hypothesis] | None:
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True
        )
        cached = client.get(cache_key)
        if not cached:
            return None

        payload = json.loads(cached)
        hypotheses: list[Hypothesis] = []
        for item in payload:
            validated = HypothesisPayload.model_validate(item)
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=validated.hypothesis_id,
                    description=validated.description,
                    confidence=float(validated.confidence),
                    evidence=validated.evidence,
                    suggested_fix_category=validated.suggested_fix_category
                    if validated.suggested_fix_category
                    in {"restart", "scale", "rollback", "config", "db", "cache", "unknown"}
                    else "unknown",
                    attempted=False,
                )
            )
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        logger.info("Root Cause Agent: cache hit for %s", cache_key)
        return hypotheses
    except Exception as exc:
        logger.warning("Root Cause Agent: cache read failed: %s", exc)
        return None


def _set_cached_hypotheses(cache_key: str, hypotheses: list[Hypothesis]) -> None:
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True
        )
        payload = [
            {
                "hypothesis_id": item.hypothesis_id,
                "description": item.description,
                "confidence": item.confidence,
                "evidence": item.evidence,
                "suggested_fix_category": item.suggested_fix_category,
            }
            for item in hypotheses
        ]
        client.setex(cache_key, max(60, ROOT_CAUSE_CACHE_TTL), json.dumps(payload))
    except Exception as exc:
        logger.warning("Root Cause Agent: cache write failed: %s", exc)


def root_cause_agent(state: AIOpsState) -> AIOpsState:
    """
    Root Cause Agent node.
    Reads:  alert, (fetches logs/traces/past_incidents externally)
    Writes: logs, traces, past_incidents, hypotheses
    """
    alert = state.get("alert")
    if not alert:
        logger.error("Root Cause Agent: no alert in state - skipping")
        return {**state, "error_message": "No alert to analyze"}

    logger.info("Root Cause Agent: analyzing alert for %s - %s", alert.service, alert.metric_name)

    # Replan path: Verifier already advanced current_hypothesis_idx.
    # Reuse existing ranked hypotheses to avoid re-attempting previous root causes.
    existing_hypotheses = state.get("hypotheses", [])
    current_idx = state.get("current_hypothesis_idx", 0)
    if existing_hypotheses and current_idx < len(existing_hypotheses):
        logger.info(
            "Root Cause Agent: replan using existing hypothesis index %s/%s",
            current_idx,
            len(existing_hypotheses),
        )
        return {
            **state,
            "hypotheses": existing_hypotheses,
            "current_hypothesis_idx": current_idx,
        }

    logs = fetch_logs(service=alert.service, limit=MAX_LOG_LINES)
    traces = fetch_traces(service=alert.service, limit=MAX_TRACES)
    past = search_similar_incidents(query=alert.description, service=alert.service, top_k=MAX_PAST)
    logger.info(
        "Root Cause Agent: fetched %s logs, %s traces, %s past incidents",
        len(logs),
        len(traces),
        len(past),
    )

    llm = get_chat_model(size="heavy", temperature=0.1, max_tokens=2048)
    prompt = _build_user_prompt_with_budget(alert, logs, traces, past, TOKEN_BUDGET)
    logger.info(
        "Root Cause Agent: prompt token estimate=%s budget=%s", _approx_tokens(prompt), TOKEN_BUDGET
    )
    cache_key = _cache_key(alert, prompt)
    cached_hypotheses = _get_cached_hypotheses(cache_key)
    if cached_hypotheses:
        return {
            **state,
            "logs": logs,
            "traces": traces,
            "past_incidents": past,
            "hypotheses": cached_hypotheses,
            "current_hypothesis_idx": current_idx,
        }

    hypotheses: list[Hypothesis] = []
    last_error = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
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
            if not isinstance(parsed, list) or len(parsed) == 0:
                raise ValueError("LLM returned empty or non-list hypotheses payload")

            validated: list[HypothesisPayload] = []
            for item in parsed:
                payload = HypothesisPayload.model_validate(item)
                validated.append(payload)

            hypotheses = []
            for payload in validated:
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=payload.hypothesis_id,
                        description=payload.description,
                        confidence=float(payload.confidence),
                        evidence=payload.evidence,
                        suggested_fix_category=payload.suggested_fix_category
                        if payload.suggested_fix_category
                        in {"restart", "scale", "rollback", "config", "db", "cache", "unknown"}
                        else "unknown",
                        attempted=False,
                    )
                )

            hypotheses.sort(key=lambda h: h.confidence, reverse=True)
            _set_cached_hypotheses(cache_key, hypotheses)
            logger.info(
                "Root Cause Agent: generated %s hypotheses (attempt %s)", len(hypotheses), attempt
            )
            break
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, ValidationError) as exc:
            last_error = str(exc)
            logger.warning("Root Cause Agent: parse error attempt %s: %s", attempt, exc)

    if not hypotheses:
        logger.error("Root Cause Agent: failed to generate hypotheses. Last error: %s", last_error)
        return {
            **state,
            "logs": logs,
            "traces": traces,
            "past_incidents": past,
            "hypotheses": [],
            "hitl_required": True,
            "error_message": f"Root cause generation failed after {MAX_LLM_RETRIES} retries: {last_error}",
        }

    return {
        **state,
        "logs": logs,
        "traces": traces,
        "past_incidents": past,
        "hypotheses": hypotheses,
        "current_hypothesis_idx": current_idx,
    }
