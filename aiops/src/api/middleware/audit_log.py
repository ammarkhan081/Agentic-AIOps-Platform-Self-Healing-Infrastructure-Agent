"""
Audit-log middleware compatibility helpers.
"""

from __future__ import annotations

from ...db.store import append_audit_event

__all__ = ["append_audit_event"]
