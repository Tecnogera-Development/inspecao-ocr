"""create users table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("""
        CREATE TABLE users (
            id          UUID        NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            email       CITEXT      NOT NULL,
            password_hash TEXT      NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ,
            is_active   BOOLEAN     NOT NULL DEFAULT true
        )
    """)
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS citext")
