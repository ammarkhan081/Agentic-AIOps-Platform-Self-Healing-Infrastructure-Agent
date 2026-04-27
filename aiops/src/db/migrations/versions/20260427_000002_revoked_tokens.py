"""Add revoked_tokens table for durable refresh token revocation."""

from __future__ import annotations

import sqlalchemy as sa  # type: ignore[reportMissingImports]
from alembic import op  # type: ignore[reportMissingImports]

# revision identifiers, used by Alembic.
revision = "20260427_000002"
down_revision = "20260410_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("token_type", sa.String(length=20), nullable=False, server_default="refresh"),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("revoked_tokens")
