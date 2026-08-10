"""Migration 0013 × modelo ORM — ticket ``mvp-c54-c57/10``.

O erro clássico que este teste pega: alguém acrescenta coluna ao modelo e
esquece a migration. Em dev o SQLite dos testes cria tudo do metadata e nada
quebra; em produção a coluna simplesmente não existe — e aqui a coluna que não
existe é a que guarda a **única fonte do F1 do contrato**.

Compilada em modo offline contra o dialeto Postgres (o banco real). SQLite não
serve: a 0012 usa ``JSONB``, que ele não compila.
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

#: Colunas do gabarito por vista — onde o julgamento humano mora.
_GABARITO = (
    "gt_classe",
    "gt_severidade",
    "gt_tipo_erro",
    "gt_observacao",
    "validado_por",
    "validado_em",
)

#: Rollup da validação no checklist — cache de consulta, não verdade nova.
_ROLLUP = ("validacao", "validado_por", "validado_em")


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
    return _sql("0012:0013")


def test_gabarito_por_vista_existe_no_banco(upgrade_sql: str) -> None:
    for coluna in _GABARITO:
        assert f"ALTER TABLE checklist_view_results ADD COLUMN {coluna}" in upgrade_sql
        assert coluna in ChecklistViewResult.__table__.columns


def test_rollup_da_validacao_existe_no_banco(upgrade_sql: str) -> None:
    for coluna in _ROLLUP:
        assert f"ALTER TABLE pipeline_jobs ADD COLUMN {coluna}" in upgrade_sql
        assert coluna in PipelineJob.__table__.columns


def test_paridade_completa_do_modelo_de_vista(upgrade_sql: str) -> None:
    """Nenhuma coluna do ORM pode faltar na soma 0011 + 0013."""
    criacao = _sql("0010:0013")
    faltando = [
        c.name for c in ChecklistViewResult.__table__.columns if c.name not in criacao
    ]
    assert faltando == []


def test_indice_da_validacao_serve_o_filtro_e_o_contador(upgrade_sql: str) -> None:
    """A lista filtra por `validacao` e conta "a validar" em toda abertura."""
    assert "ix_pipeline_jobs_validacao" in upgrade_sql


def test_indice_do_gabarito_serve_o_eval(upgrade_sql: str) -> None:
    """O eval varre "todas as vistas com gabarito" — sem índice, full scan."""
    assert "ix_checklist_view_results_gt_classe" in upgrade_sql


def test_gabarito_e_nulo_por_padrao(upgrade_sql: str) -> None:
    """Ausência de gabarito É o estado inicial: pendência não é dado gravado."""
    assert "gt_classe VARCHAR(32)" in upgrade_sql
    assert "gt_classe VARCHAR(32) NOT NULL" not in upgrade_sql


def test_downgrade_desfaz_tudo() -> None:
    sql = _sql("0013:0012", downgrade=True)

    for coluna in _GABARITO:
        assert f"ALTER TABLE checklist_view_results DROP COLUMN {coluna}" in sql
    for coluna in _ROLLUP:
        assert f"ALTER TABLE pipeline_jobs DROP COLUMN {coluna}" in sql
