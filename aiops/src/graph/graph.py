"""
LangGraph Graph Definition for the ASHIA AIOps pipeline.
"""

from __future__ import annotations

import logging
import os

from langgraph.graph import END, StateGraph

from ..agents.hitl import hitl_supervisor
from ..agents.learning import learning_agent
from ..agents.monitor import monitor_agent
from ..agents.remediation import remediation_agent
from ..agents.root_cause import root_cause_agent
from ..agents.verifier import verifier_agent
from .edges import (
    route_after_hitl,
    route_after_monitor,
    route_after_remediation,
    route_after_verifier,
)
from .state import AIOpsState

logger = logging.getLogger("graph")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://ashia:ashia_secret@localhost:5432/ashia_db")


def build_graph(use_postgres: bool = True):
    """
    Build and compile the ASHIA LangGraph.
    use_postgres=True in production (persistent checkpoints).
    use_postgres=False for testing (in-memory).
    """
    builder = StateGraph(AIOpsState)

    builder.add_node("monitor", monitor_agent)
    builder.add_node("root_cause", root_cause_agent)
    builder.add_node("remediation", remediation_agent)
    builder.add_node("hitl", hitl_supervisor)
    builder.add_node("verifier", verifier_agent)
    builder.add_node("learning", learning_agent)

    builder.set_entry_point("monitor")

    builder.add_conditional_edges(
        "monitor", route_after_monitor, {"root_cause": "root_cause", "__end__": END}
    )
    builder.add_edge("root_cause", "remediation")
    builder.add_conditional_edges(
        "remediation", route_after_remediation, {"hitl": "hitl", "verifier": "verifier"}
    )
    builder.add_conditional_edges(
        "hitl",
        route_after_hitl,
        {
            "verifier": "verifier",
            "learning": "learning",
            "__end__": END,
        },
    )
    builder.add_conditional_edges(
        "verifier",
        route_after_verifier,
        {"learning": "learning", "root_cause": "root_cause", "hitl": "hitl"},
    )
    builder.add_edge("learning", END)

    if use_postgres:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            checkpointer = PostgresSaver.from_conn_string(DATABASE_URL)
            checkpointer.setup()
            graph = builder.compile(checkpointer=checkpointer)
            logger.info("Graph compiled with PostgreSQL checkpointer")
            return graph
        except Exception as exc:
            logger.warning("PostgreSQL checkpointer failed (%s); falling back to memory", exc)

    from langgraph.checkpoint.memory import MemorySaver

    graph = builder.compile(checkpointer=MemorySaver())
    logger.info("Graph compiled with in-memory checkpointer")
    return graph


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph(use_postgres=True)
    return _graph
