"""
Auth middleware compatibility helpers.
"""

from __future__ import annotations

from ..routes.auth import get_current_user, get_current_user_from_token

__all__ = ["get_current_user", "get_current_user_from_token"]
