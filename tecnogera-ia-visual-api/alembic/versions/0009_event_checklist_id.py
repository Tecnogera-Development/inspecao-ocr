"""add checklist_id to events (IAVS-068 — relatório: ID Checklist)

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("checklist_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_events_checklist_id", "events", ["checklist_id"])


def downgrade() -> None:
    op.drop_index("ix_events_checklist_id", table_name="events")
    op.drop_column("events", "checklist_id")
