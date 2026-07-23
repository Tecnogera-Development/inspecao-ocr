"""add annotated_image_path to event_pairs (IAVS-065)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_pairs",
        sa.Column("annotated_image_path", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("event_pairs", "annotated_image_path")
