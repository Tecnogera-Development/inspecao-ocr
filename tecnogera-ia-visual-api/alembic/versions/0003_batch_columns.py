"""add batch columns to pipeline_jobs

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-26

Adiciona suporte ao Batch API da Anthropic (IAVS-041).
Status válidos após esta migration: pending, running, done, failed, pending_batch.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_jobs",
        sa.Column("mode", sa.String(16), nullable=False, server_default="sync"),
    )
    op.add_column(
        "pipeline_jobs",
        sa.Column("batch_id", sa.Text, nullable=True),
    )
    op.add_column(
        "pipeline_jobs",
        sa.Column("batch_submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_jobs",
        sa.Column("batch_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_jobs", "batch_resolved_at")
    op.drop_column("pipeline_jobs", "batch_submitted_at")
    op.drop_column("pipeline_jobs", "batch_id")
    op.drop_column("pipeline_jobs", "mode")
