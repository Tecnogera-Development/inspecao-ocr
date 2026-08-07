"""enriquecimento do Sisloc em pipeline_jobs (persistência híbrida)

Medição: docs/exploracao/enriquecimento-checklist-produto.md

Três colunas **tipadas e indexadas** — as únicas que a aplicação consulta:
``formulario`` (filtro e agrupamento), ``patrimonio`` (busca do operador pelo
ativo) e ``projeto`` (busca por cliente). Larguras iguais às da view
(``varchar(30)``, ``varchar(15)``, ``varchar(200)``).

``n_linhas`` é tipada mas não indexada: é aviso de tela ("este checklist cobre N
equipamentos"), não critério de busca. Sem ela o sistema nomearia o equipamento
errado em silêncio em 0,36% dos casos.

``sisloc_snapshot`` é **JSONB**, não o ``JSON`` genérico que ``metrics`` usa — o
``metrics`` é um erro existente e não se replica erro. Guarda as 11 colunas
cruas, o ``projeto`` parseado e ``lido_em``, validados por
``app.models.sisloc.SislocSnapshot``.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_jobs", sa.Column("formulario", sa.String(30), nullable=True))
    op.add_column("pipeline_jobs", sa.Column("patrimonio", sa.String(15), nullable=True))
    op.add_column("pipeline_jobs", sa.Column("projeto", sa.String(200), nullable=True))
    op.add_column("pipeline_jobs", sa.Column("n_linhas", sa.Integer(), nullable=True))
    op.add_column(
        "pipeline_jobs",
        sa.Column("sisloc_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_index("ix_pipeline_jobs_formulario", "pipeline_jobs", ["formulario"])
    op.create_index("ix_pipeline_jobs_patrimonio", "pipeline_jobs", ["patrimonio"])
    op.create_index("ix_pipeline_jobs_projeto", "pipeline_jobs", ["projeto"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_jobs_projeto", table_name="pipeline_jobs")
    op.drop_index("ix_pipeline_jobs_patrimonio", table_name="pipeline_jobs")
    op.drop_index("ix_pipeline_jobs_formulario", table_name="pipeline_jobs")

    op.drop_column("pipeline_jobs", "sisloc_snapshot")
    op.drop_column("pipeline_jobs", "n_linhas")
    op.drop_column("pipeline_jobs", "projeto")
    op.drop_column("pipeline_jobs", "patrimonio")
    op.drop_column("pipeline_jobs", "formulario")
