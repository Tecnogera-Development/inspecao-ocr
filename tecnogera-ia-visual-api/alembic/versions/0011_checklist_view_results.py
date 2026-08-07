"""inspeção por vista + rollup do checklist


Duas coisas: a tabela ``checklist_view_results`` (uma linha por vista, com o
custo MEDIDO da chamada) e as colunas de rollup em ``pipeline_jobs``.

``cost_usd`` não é detalhe de auditoria: é a coluna que o teto mensal de
orçamento soma antes de liberar cada chamada (``app/services/llm_budget.py``).
O índice em ``created_at`` existe para essa soma.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checklist_view_results",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("pipeline_jobs.id"),
            nullable=False,
        ),
        sa.Column("checklist_id", sa.String(64), nullable=False),
        sa.Column("campo", sa.String(16), nullable=False),
        sa.Column("dropbox_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("conformidade", sa.String(24), nullable=True),
        sa.Column("motivo_nao_processavel", sa.String(32), nullable=True),
        sa.Column("vista_confere", sa.Boolean(), nullable=True),
        sa.Column("conteudo_observado", sa.Text(), nullable=True),
        sa.Column("achados", sa.JSON(), nullable=True),
        sa.Column("severidade_max", sa.Integer(), nullable=True),
        sa.Column("classe", sa.String(32), nullable=True),
        sa.Column("tipo_defeito", sa.String(48), nullable=True),
        sa.Column("confianca", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("job_id", "campo", name="uq_checklist_view_results_job_campo"),
    )
    op.create_index(
        "ix_checklist_view_results_job_id", "checklist_view_results", ["job_id"]
    )
    op.create_index(
        "ix_checklist_view_results_checklist_id",
        "checklist_view_results",
        ["checklist_id"],
    )
    # Soma do gasto do mês corrente — consulta feita uma vez por rodada do cron.
    op.create_index(
        "ix_checklist_view_results_created_at", "checklist_view_results", ["created_at"]
    )

    op.add_column("pipeline_jobs", sa.Column("conformidade", sa.String(24), nullable=True))
    op.add_column("pipeline_jobs", sa.Column("severidade_max", sa.Integer(), nullable=True))
    op.add_column(
        "pipeline_jobs", sa.Column("vista_determinante", sa.String(16), nullable=True)
    )
    op.add_column(
        "pipeline_jobs", sa.Column("vistas_recebidas", sa.String(64), nullable=True)
    )
    op.add_column(
        "pipeline_jobs",
        sa.Column("llm_cost_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "pipeline_jobs",
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("pipeline_jobs", "llm_calls")
    op.drop_column("pipeline_jobs", "llm_cost_usd")
    op.drop_column("pipeline_jobs", "vistas_recebidas")
    op.drop_column("pipeline_jobs", "vista_determinante")
    op.drop_column("pipeline_jobs", "severidade_max")
    op.drop_column("pipeline_jobs", "conformidade")
    op.drop_index(
        "ix_checklist_view_results_created_at", table_name="checklist_view_results"
    )
    op.drop_index(
        "ix_checklist_view_results_checklist_id", table_name="checklist_view_results"
    )
    op.drop_index("ix_checklist_view_results_job_id", table_name="checklist_view_results")
    op.drop_table("checklist_view_results")
