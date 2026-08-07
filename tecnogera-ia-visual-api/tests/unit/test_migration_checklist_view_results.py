"""Migration 0011 × modelo ORM — ticket mvp-c54-c57/08.

O erro que este teste pega é o clássico: alguém acrescenta uma coluna ao modelo
e esquece a migration. Em dev o SQLite dos testes cria tudo a partir do
metadata e nada quebra; em produção a coluna simplesmente não existe.

A migration é compilada em modo offline contra o dialeto **Postgres** — o banco
real. SQLite não serve aqui: a migration 0001 usa ``JSONB``, que ele não
compila.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from alembic import command
from alembic.config import Config
from app.models.checklist_analysis import ChecklistViewResult
from app.models.pipeline import PipelineJob

pytestmark = pytest.mark.unit

_POSTGRES_FALSO = "postgresql+psycopg2://u:p@localhost/db"

#: Colunas que a 0011 acrescenta a ``pipeline_jobs`` (o rollup do checklist).
_ROLLUP = (
    "conformidade",
    "severidade_max",
    "vista_determinante",
    "vistas_recebidas",
    "llm_cost_usd",
    "llm_calls",
)


def _sql(alvo: str, *, downgrade: bool = False) -> str:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", _POSTGRES_FALSO)
    buf = io.StringIO()
    with redirect_stdout(buf):
        if downgrade:
            command.downgrade(cfg, alvo, sql=True)
        else:
            command.upgrade(cfg, alvo, sql=True)
    return buf.getvalue()


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _sql("0010:0011")


#: Colunas do modelo que NÃO nascem aqui: o gabarito humano chega na 0013
#: (ticket 10). A paridade completa ORM ↔ banco é checada em
#: ``test_migration_hitl_validacao.test_paridade_completa_do_modelo_de_vista``,
#: que compila 0010→0013; esta função cobre só o que a 0011 se comprometeu a
#: criar.
_DA_0013 = frozenset(
    {
        "gt_classe",
        "gt_severidade",
        "gt_tipo_erro",
        "gt_observacao",
        "validado_por",
        "validado_em",
    }
)


def test_migration_cria_todas_as_colunas_do_modelo(upgrade_sql: str) -> None:
    """Paridade ORM ↔ migration: coluna no modelo sem coluna no banco é bug mudo."""
    faltando = [
        c.name
        for c in ChecklistViewResult.__table__.columns
        if c.name not in _DA_0013 and c.name not in upgrade_sql
    ]
    assert faltando == []


def test_migration_acrescenta_o_rollup_em_pipeline_jobs(upgrade_sql: str) -> None:
    for coluna in _ROLLUP:
        assert f"ADD COLUMN {coluna}" in upgrade_sql
        assert coluna in PipelineJob.__table__.columns


def test_custo_por_vista_e_nao_nulo_com_default_zero(upgrade_sql: str) -> None:
    """O teto mensal soma esta coluna: um NULL viraria gasto invisível."""
    assert "cost_usd FLOAT DEFAULT '0' NOT NULL" in upgrade_sql


def test_indice_de_created_at_existe_para_a_soma_do_mes(upgrade_sql: str) -> None:
    """A soma do gasto do mês roda a cada rodada do cron."""
    assert "ix_checklist_view_results_created_at" in upgrade_sql


def test_par_job_campo_e_unico(upgrade_sql: str) -> None:
    """Reprocessar um job atualiza a vista; não pode duplicar linha."""
    assert "uq_checklist_view_results_job_campo" in upgrade_sql


def test_downgrade_desfaz_tudo() -> None:
    sql = _sql("0011:0010", downgrade=True)

    assert "DROP TABLE checklist_view_results" in sql
    for coluna in _ROLLUP:
        assert f"DROP COLUMN {coluna}" in sql
