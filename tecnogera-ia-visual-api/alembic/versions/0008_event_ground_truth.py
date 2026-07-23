"""add ground_truth_class to events (IAVS-066)

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("ground_truth_class", sa.String(64), nullable=True),
    )
    op.create_index("ix_events_ground_truth_class", "events", ["ground_truth_class"])


def downgrade() -> None:
    op.drop_index("ix_events_ground_truth_class", table_name="events")
    op.drop_column("events", "ground_truth_class")
