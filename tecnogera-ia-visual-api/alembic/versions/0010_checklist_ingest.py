"""ingestão agendada de checklists: cursor do Dropbox + livro-razão


Revision ID: 0010
Revises: 0009
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_cursors",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("cursor", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "checklist_ingest_state",
        sa.Column("checklist_id", sa.String(64), primary_key=True),
        sa.Column("campos", sa.Text(), nullable=False, server_default=""),
        sa.Column("formulario", sa.String(30), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pendente"),
        sa.Column("motivo", sa.String(64), nullable=True),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # A rodada busca os `pendente` para reavaliar — índice composto serve.
    op.create_index(
        "ix_checklist_ingest_state_status",
        "checklist_ingest_state",
        ["status", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_checklist_ingest_state_status", table_name="checklist_ingest_state")
    op.drop_table("checklist_ingest_state")
    op.drop_table("ingest_cursors")
