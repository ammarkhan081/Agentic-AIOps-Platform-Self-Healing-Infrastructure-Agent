"""
Unit tests for reports routes and export helpers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from src.api.routes import reports


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self._offset = 0
        self._limit = None

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def count(self):
        return len(self.rows)

    def offset(self, value):
        self._offset = value
        return self

    def limit(self, value):
        self._limit = value
        return self

    def all(self):
        rows = self.rows[self._offset :]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


class FakeDB:
    def __init__(self, rows):
        self.rows = {row.incident_id: row for row in rows}

    def query(self, _model):
        return FakeQuery(list(self.rows.values()))

    def get(self, _model, incident_id):
        return self.rows.get(incident_id)


def build_row(incident_id: str = "inc-12345678"):
    return SimpleNamespace(
        incident_id=incident_id,
        created_at=datetime(2026, 3, 30, 12, 0, 0),
        status="resolved",
        state_snapshot={
            "alert": {"severity": "CRITICAL", "description": "order-service memory leak"},
            "hypotheses": [
                {
                    "description": "Memory leak in order-service worker",
                    "confidence": 0.92,
                    "attempted": True,
                    "evidence": ["memory", "container", "restart"],
                }
            ],
            "postmortem": {
                "incident_id": incident_id,
                "service": "order-service",
                "outcome": "resolved",
                "time_to_recovery_seconds": 48.0,
                "retry_count": 1,
                "total_cost_usd": 0.018,
                "created_at": "2026-03-30T12:00:00",
                "root_cause_confirmed": "Memory leak in order-service worker",
                "fix_applied": "restart_container",
                "alert_signature": "order-service:memory:critical",
            },
        },
        outcome="resolved",
    )


def test_list_reports_returns_postmortems():
    result = asyncio.run(reports.list_reports(user={"role": "viewer"}, db=FakeDB([build_row()])))

    assert len(result["reports"]) == 1
    assert result["reports"][0]["incident_id"] == "inc-12345678"
    assert result["reports"][0]["status"] == "resolved"
    assert result["pagination"]["total"] == 1


def test_export_report_json_returns_postmortem():
    result = asyncio.run(
        reports.export_report(
            "inc-12345678",
            format="json",
            user={"role": "viewer"},
            db=FakeDB([build_row()]),
        )
    )

    assert result["fix_applied"] == "restart_container"


def test_export_report_markdown_contains_sections(monkeypatch):
    monkeypatch.setattr(
        reports,
        "list_audit_events",
        lambda _db, _incident_id: [
            {"timestamp": "2026-03-30T12:00:00", "type": "monitor.alert_opened"}
        ],
    )

    response = asyncio.run(
        reports.export_report(
            "inc-12345678",
            format="markdown",
            user={"role": "viewer"},
            db=FakeDB([build_row()]),
        )
    )

    assert "Incident Postmortem" in response.body.decode()
    assert "Hypotheses Considered" in response.body.decode()


def test_export_report_unsupported_format_raises(monkeypatch):
    monkeypatch.setattr(reports, "list_audit_events", lambda _db, _incident_id: [])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            reports.export_report(
                "inc-12345678",
                format="xml",
                user={"role": "viewer"},
                db=FakeDB([build_row()]),
            )
        )

    assert exc.value.status_code == 400


def test_get_report_returns_postmortem():
    result = asyncio.run(
        reports.get_report("inc-12345678", user={"role": "viewer"}, db=FakeDB([build_row()]))
    )

    assert result["incident_id"] == "inc-12345678"


def test_get_report_raises_when_missing():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(reports.get_report("missing", user={"role": "viewer"}, db=FakeDB([])))

    assert exc.value.status_code == 404


def test_export_report_pdf_returns_binary(monkeypatch):
    monkeypatch.setattr(reports, "list_audit_events", lambda _db, _incident_id: [])
    real_import = __import__

    class FakeCanvas:
        def __init__(self, buffer, pagesize=None):
            self.buffer = buffer

        def setTitle(self, _title):
            return None

        def setFont(self, _font, _size):
            return None

        def drawString(self, x, y, text):
            self.buffer.write(text.encode("utf-8"))

        def showPage(self):
            return None

        def save(self):
            self.buffer.write(b"pdf")

    def fake_import(name, *args, **kwargs):
        if name == "reportlab.lib.pagesizes":
            return SimpleNamespace(letter=(612, 792))
        if name == "reportlab.pdfgen":
            return SimpleNamespace(canvas=SimpleNamespace(Canvas=FakeCanvas))
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    response = asyncio.run(
        reports.export_report(
            "inc-12345678",
            format="pdf",
            user={"role": "viewer"},
            db=FakeDB([build_row()]),
        )
    )

    assert response.media_type == "application/pdf"


def test_export_report_pdf_failure_raises(monkeypatch):
    monkeypatch.setattr(reports, "list_audit_events", lambda _db, _incident_id: [])
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("reportlab"):
            raise RuntimeError("missing reportlab")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            reports.export_report(
                "inc-12345678",
                format="pdf",
                user={"role": "viewer"},
                db=FakeDB([build_row()]),
            )
        )

    assert exc.value.status_code == 500
