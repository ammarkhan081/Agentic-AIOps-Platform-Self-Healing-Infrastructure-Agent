"""
Conditional routing functions for the ASHIA LangGraph.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from .state import AIOpsState

logger = logging.getLogger("graph.edges")
MAX_RETRIES = int(os.getenv("MAX_RETRY_COUNT", "3"))


def route_after_monitor(state: AIOpsState) -> Literal["root_cause", "__end__"]:
    if state.get("alert"):
        return "root_cause"
    return "__end__"


def route_after_remediation(state: AIOpsState) -> Literal["hitl", "verifier"]:
    if state.get("hitl_required"):
        return "hitl"
    return "verifier"


def route_after_hitl(state: AIOpsState) -> Literal["learning", "verifier", "__end__"]:
    response = state.get("hitl_response")
    if not response:
        # First HITL entry pauses for human input and ends this execution pass.
        return "__end__"

    decision = getattr(response, "decision", None) or response.get("decision", "approve")
    if decision == "abort":
        return "learning"
    return "verifier"


def route_after_verifier(state: AIOpsState) -> Literal["learning", "root_cause", "hitl"]:
    if state.get("recovery_confirmed"):
        return "learning"

    retry_count = state.get("retry_count", 0)
    if retry_count >= MAX_RETRIES:
        logger.warning("Graph: max retries exhausted, escalating to HITL")
        return "hitl"

    hypotheses = state.get("hypotheses", [])
    current_idx = state.get("current_hypothesis_idx", 0)
    if current_idx >= len(hypotheses):
        logger.warning("Graph: no more hypotheses available, escalating to HITL")
        return "hitl"

    return "root_cause"
