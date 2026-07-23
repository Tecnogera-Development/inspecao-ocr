"""create event_pairs table (IAVS-064)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_pairs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("asset_code", sa.String(128), nullable=False),
        sa.Column("pair_date", sa.Date, nullable=False),
        sa.Column(
            "saida_event_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=True,
        ),
        sa.Column(
            "retorno_event_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="partial"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint("uq_event_pairs_saida", "event_pairs", ["saida_event_id"])
    op.create_unique_constraint(
        "uq_event_pairs_retorno", "event_pairs", ["retorno_event_id"]
    )
    op.create_unique_constraint(
        "uq_event_pairs_asset_date", "event_pairs", ["asset_code", "pair_date"]
    )
    op.create_index("ix_event_pairs_asset_code", "event_pairs", ["asset_code"])
    op.create_index("ix_event_pairs_pair_date", "event_pairs", ["pair_date"])
    op.create_index("ix_event_pairs_status", "event_pairs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_event_pairs_status", table_name="event_pairs")
    op.drop_index("ix_event_pairs_pair_date", table_name="event_pairs")
    op.drop_index("ix_event_pairs_asset_code", table_name="event_pairs")
    op.drop_constraint("uq_event_pairs_asset_date", "event_pairs", type_="unique")
    op.drop_constraint("uq_event_pairs_retorno", "event_pairs", type_="unique")
    op.drop_constraint("uq_event_pairs_saida", "event_pairs", type_="unique")
    op.drop_table("event_pairs")
