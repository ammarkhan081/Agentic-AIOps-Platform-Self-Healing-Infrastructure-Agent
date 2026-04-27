"""
Structured logging utilities for the ASHIA control plane.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone

_request_id: ContextVar[str | None] = ContextVar("ashia_request_id", default=None)


def set_request_id(value: str | None):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    """Lightweight JSON formatter for consistent service logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        request_id = _request_id.get()
        if request_id:
            payload["request_id"] = request_id
        return json.dumps(payload, ensure_ascii=True)


def configure_logging() -> None:
    """Configure root logging once for the control plane."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if getattr(root, "_ashia_configured", False):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level_name, logging.INFO))
    root._ashia_configured = True  # type: ignore[attr-defined]
