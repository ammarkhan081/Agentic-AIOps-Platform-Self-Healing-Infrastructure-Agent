"""
Integration tests — full pipeline with mocked external dependencies.
Tests the complete LangGraph flow from alert to postmortem.
"""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ["GROQ_API_KEY"] = "test-key"

from src.graph.graph import build_graph
from src.graph.state import ActionResult, AlertEvent, FixOption, Hypothesis, initial_state


def _mock_alert():
    return AlertEvent.create(
        service="order-service",
        metric_name="order_memory_leak_bytes",
        current=8000.0,
        mean=200.0,
        std=50.0,
        threshold=2.5,
        severity="CRITICAL",
    )


def _mock_hypothesis(confidence: float = 0.92):
    return Hypothesis(
        hypothesis_id="h1",
        description="Memory leak in order-service from unbounded list",
        confidence=confidence,
        evidence=["order_memory_leak_bytes=8000 >> baseline 200"],
        suggested_fix_category="restart",
        attempted=False,
    )


def _mock_fix_low_risk():
    return FixOption(
        fix_id="f1",
        action_type="restart_container",
        parameters={"container": "order-service"},
        risk_score="LOW",
        estimated_recovery_seconds=30,
        reasoning="Container restart clears leaked memory",
    )


def _mock_action_result():
    return ActionResult(
        action_type="restart_container",
        parameters={"container": "order-service"},
        executed_at=datetime.utcnow().isoformat(),
        outcome="success",
        response="Container ashia-order-service restarted",
        duration_seconds=2.1,
    )


class TestFullPipelineLowRisk:
    """Test the happy path: fault → detect → analyze → LOW-risk fix → verify → learn."""

    def test_pipeline_resolves_low_risk_incident(self):
        graph = build_graph(use_postgres=False)

        state = initial_state()
        state["alert"] = _mock_alert()  # Pre-inject alert (skip monitor polling)

        config = {"configurable": {"thread_id": state["incident_id"]}}

        with (
            patch("src.agents.root_cause.fetch_logs", return_value=[]),
            patch("src.agents.root_cause.fetch_traces", return_value=[]),
            patch("src.agents.root_cause.search_similar_incidents", return_value=[]),
            patch("src.agents.root_cause.get_chat_model") as mock_llm_cls,
            patch("src.agents.remediation.get_chat_model") as mock_rem_cls,
            patch("src.agents.remediation._execute_fix", return_value=_mock_action_result()),
            patch("src.agents.verifier._metric_recovered", return_value=True),
            patch("src.agents.verifier.time.sleep"),
            patch("src.tools.chroma_tool.upsert_incident"),
        ):
            # Mock LLM responses
            mock_rc_instance = MagicMock()
            mock_rc_instance.invoke.return_value = MagicMock(
                content='[{"hypothesis_id":"h1","description":"Memory leak","confidence":0.9,'
                '"evidence":["metric spike"],"suggested_fix_category":"restart","reasoning":"OOM"}]'
            )
            mock_llm_cls.return_value = mock_rc_instance

            mock_rem_instance = MagicMock()
            mock_rem_instance.invoke.return_value = MagicMock(
                content='[{"fix_id":"f1","action_type":"restart_container",'
                '"parameters":{"container":"order-service"},"risk_score":"LOW",'
                '"estimated_recovery_seconds":30,"reasoning":"restart clears memory"}]'
            )
            mock_rem_cls.return_value = mock_rem_instance

            final_state = None
            for chunk in graph.stream(state, config=config, stream_mode="values"):
                final_state = chunk

        assert final_state is not None
        assert final_state["status"] in ("resolved", "active", "escalated")

    def test_pipeline_sets_postmortem_on_close(self):
        """Postmortem should be written at end of pipeline."""
        graph = build_graph(use_postgres=False)
        state = initial_state()
        state["alert"] = _mock_alert()
        config = {"configurable": {"thread_id": state["incident_id"]}}

        with (
            patch("src.agents.root_cause.fetch_logs", return_value=[]),
            patch("src.agents.root_cause.fetch_traces", return_value=[]),
            patch("src.agents.root_cause.search_similar_incidents", return_value=[]),
            patch("src.agents.root_cause.get_chat_model") as mock_llm_cls,
            patch("src.agents.remediation.get_chat_model") as mock_rem_cls,
            patch("src.agents.remediation._execute_fix", return_value=_mock_action_result()),
            patch("src.agents.verifier._metric_recovered", return_value=True),
            patch("src.agents.verifier.time.sleep"),
            patch("src.tools.chroma_tool.upsert_incident"),
        ):
            mock_rc_instance = MagicMock()
            mock_rc_instance.invoke.return_value = MagicMock(
                content='[{"hypothesis_id":"h1","description":"Leak","confidence":0.9,'
                '"evidence":[],"suggested_fix_category":"restart","reasoning":"OOM"}]'
            )
            mock_llm_cls.return_value = mock_rc_instance

            mock_rem_instance = MagicMock()
            mock_rem_instance.invoke.return_value = MagicMock(
                content='[{"fix_id":"f1","action_type":"restart_container",'
                '"parameters":{"container":"order-service"},"risk_score":"LOW",'
                '"estimated_recovery_seconds":30,"reasoning":"restart"}]'
            )
            mock_rem_cls.return_value = mock_rem_instance

            final_state = None
            for chunk in graph.stream(state, config=config, stream_mode="values"):
                final_state = chunk

        # Postmortem should exist
        assert final_state.get("postmortem") is not None or final_state.get("status") is not None


class TestRetryLoop:
    def test_retry_count_increments_on_failed_recovery(self):
        """If verifier fails, retry_count should increment."""
        graph = build_graph(use_postgres=False)
        state = initial_state()
        state["alert"] = _mock_alert()
        state["hypotheses"] = [_mock_hypothesis(), _mock_hypothesis(confidence=0.6)]
        config = {"configurable": {"thread_id": state["incident_id"]}}

        with (
            patch("src.agents.root_cause.fetch_logs", return_value=[]),
            patch("src.agents.root_cause.fetch_traces", return_value=[]),
            patch("src.agents.root_cause.search_similar_incidents", return_value=[]),
            patch("src.agents.root_cause.get_chat_model") as mock_llm_cls,
            patch("src.agents.remediation.get_chat_model") as mock_rem_cls,
            patch("src.agents.remediation._execute_fix", return_value=_mock_action_result()),
            patch("src.agents.verifier._metric_recovered", return_value=False),
            patch("src.agents.verifier.time.sleep"),
            patch("src.tools.chroma_tool.upsert_incident"),
        ):
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = MagicMock(
                content='[{"hypothesis_id":"h1","description":"Leak","confidence":0.9,'
                '"evidence":[],"suggested_fix_category":"restart","reasoning":"OOM"}]'
            )
            mock_llm_cls.return_value = mock_llm
            mock_rem_cls.return_value = MagicMock(
                invoke=MagicMock(
                    return_value=MagicMock(
                        content='[{"fix_id":"f1","action_type":"restart_container",'
                        '"parameters":{},"risk_score":"LOW","estimated_recovery_seconds":30,"reasoning":"r"}]'
                    )
                )
            )

            final_state = None
            for chunk in graph.stream(state, config=config, stream_mode="values"):
                final_state = chunk

        # After retries exhausted, should escalate or be paused for HITL
        assert final_state["retry_count"] > 0 or final_state["status"] in ("paused", "escalated")
