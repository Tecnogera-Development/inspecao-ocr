"""gabarito humano por vista + rollup da validação no checklist


A validação humana é a **única fonte do F1 que o contrato exige** (Anexo I §8).
Esta migration cria onde ela mora.

O gabarito fica em ``checklist_view_results``, na MESMA linha da predição, e não
numa tabela de anotações. A linha já é única por ``(job_id, campo)``, então
validar duas vezes é UPDATE — a idempotência que o ticket pede sai da chave que
já existia, não de código de aplicação. E o eval lê ``(classe predita, gt_classe)``
sem join, o que impede registro órfão de entrar na conta.

``gt_tipo_erro`` é o que separa este ticket de um contador: "corrigido" sem dizer
*o quê* só serve para somar; com o tipo (falso positivo / classe errada /
severidade errada / foto não julgável), vira insumo de calibragem do prompt.

Em ``pipeline_jobs`` entram três colunas de **rollup** da validação. Elas não são
verdade nova — são derivadas das vistas — e existem porque a lista filtra por
``validacao`` e conta "a validar" em SQL. ``ix_pipeline_jobs_validacao`` serve
exatamente essas duas consultas.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── gabarito por vista ────────────────────────────────────────────────────
    op.add_column(
        "checklist_view_results", sa.Column("gt_classe", sa.String(32), nullable=True)
    )
    op.add_column(
        "checklist_view_results", sa.Column("gt_severidade", sa.Integer(), nullable=True)
    )
    op.add_column(
        "checklist_view_results", sa.Column("gt_tipo_erro", sa.String(24), nullable=True)
    )
    op.add_column(
        "checklist_view_results", sa.Column("gt_observacao", sa.Text(), nullable=True)
    )
    op.add_column(
        "checklist_view_results", sa.Column("validado_por", sa.String(255), nullable=True)
    )
    op.add_column(
        "checklist_view_results",
        sa.Column("validado_em", sa.DateTime(timezone=True), nullable=True),
    )
    # O eval varre "todas as vistas com gabarito" — é a consulta que o índice
    # serve. Sem ele, medir o F1 vira full scan da tabela de laudos.
    op.create_index(
        "ix_checklist_view_results_gt_classe", "checklist_view_results", ["gt_classe"]
    )

    # ── rollup da validação no checklist ──────────────────────────────────────
    op.add_column("pipeline_jobs", sa.Column("validacao", sa.String(16), nullable=True))
    op.add_column("pipeline_jobs", sa.Column("validado_por", sa.String(255), nullable=True))
    op.add_column(
        "pipeline_jobs", sa.Column("validado_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_pipeline_jobs_validacao", "pipeline_jobs", ["validacao"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_jobs_validacao", table_name="pipeline_jobs")
    op.drop_column("pipeline_jobs", "validado_em")
    op.drop_column("pipeline_jobs", "validado_por")
    op.drop_column("pipeline_jobs", "validacao")

    op.drop_index(
        "ix_checklist_view_results_gt_classe", table_name="checklist_view_results"
    )
    op.drop_column("checklist_view_results", "validado_em")
    op.drop_column("checklist_view_results", "validado_por")
    op.drop_column("checklist_view_results", "gt_observacao")
    op.drop_column("checklist_view_results", "gt_tipo_erro")
    op.drop_column("checklist_view_results", "gt_severidade")
    op.drop_column("checklist_view_results", "gt_classe")
