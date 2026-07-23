"""create events table (IAVS-060)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("asset_code", sa.String(128), nullable=False),
        sa.Column("canonical_angle", sa.String(64), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moment", sa.String(16), nullable=True),
        sa.Column("uploaded_by", sa.String(128), nullable=True),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("damage_class", sa.String(64), nullable=True),
        sa.Column("damage_confidence", sa.Float, nullable=True),
        sa.Column("damage_severity", sa.String(32), nullable=True),
        sa.Column("angle_class", sa.String(64), nullable=True),
        sa.Column("angle_confidence", sa.Float, nullable=True),
        sa.Column("validation_reason", sa.String(64), nullable=True),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Column("annotated_image_path", sa.Text, nullable=True),
    )
    op.create_unique_constraint("uq_events_source_path", "events", ["source_path"])
    op.create_index("ix_events_asset_code", "events", ["asset_code"])
    op.create_index("ix_events_canonical_angle", "events", ["canonical_angle"])
    op.create_index("ix_events_moment", "events", ["moment"])
    op.create_index("ix_events_captured_at", "events", ["captured_at"])
    op.create_index("ix_events_status", "events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_events_status", table_name="events")
    op.drop_index("ix_events_captured_at", table_name="events")
    op.drop_index("ix_events_moment", table_name="events")
    op.drop_index("ix_events_canonical_angle", table_name="events")
    op.drop_index("ix_events_asset_code", table_name="events")
    op.drop_constraint("uq_events_source_path", "events", type_="unique")
    op.drop_table("events")
