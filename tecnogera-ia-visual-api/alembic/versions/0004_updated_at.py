"""add updated_at to pipeline_jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-26

Adiciona coluna updated_at para suporte a ETag em endpoints do portal (IAVS-032).
Backfill: updated_at = finished_at ?? started_at ?? created_at.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_jobs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE pipeline_jobs SET updated_at = COALESCE(finished_at, started_at, created_at)"
    )
    op.alter_column("pipeline_jobs", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("pipeline_jobs", "updated_at")
