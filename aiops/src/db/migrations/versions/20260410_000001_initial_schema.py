"""Initial ASHIA schema (incidents, audit_log, users)."""

from __future__ import annotations

import sqlalchemy as sa  # type: ignore[reportMissingImports]
from alembic import op  # type: ignore[reportMissingImports]

# revision identifiers, used by Alembic.
revision = "20260410_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("service", sa.String(length=100), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("alert_signature", sa.String(length=200), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("fix_applied", sa.String(length=500), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("time_to_recovery", sa.Float(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True, server_default="0"),
        sa.Column("hitl_required", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("hitl_decision", sa.String(length=20), nullable=True),
        sa.Column("hitl_decided_by", sa.String(length=100), nullable=True),
        sa.Column("state_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_audit_log_incident_id", "audit_log", ["incident_id"], unique=False)

    op.create_table(
        "users",
        sa.Column("username", sa.String(length=100), primary_key=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="viewer"),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_index("ix_audit_log_incident_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("incidents")
