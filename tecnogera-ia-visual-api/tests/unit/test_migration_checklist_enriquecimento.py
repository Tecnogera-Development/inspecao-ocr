"""Migration 0012 × modelo ORM — ticket mvp-c54-c57/17.

O erro que este teste pega é o clássico: alguém acrescenta uma coluna ao modelo
e esquece a migration. Em dev o SQLite dos testes cria tudo a partir do metadata
e nada quebra; em produção a coluna simplesmente não existe.

Compilada em modo offline contra o dialeto **Postgres** — o banco real. SQLite
não serve: a 0001 e a 0012 usam ``JSONB``, que ele não compila.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from alembic import command
from alembic.config import Config
from app.models.pipeline import PipelineJob

pytestmark = pytest.mark.unit

_POSTGRES_FALSO = "postgresql+psycopg2://u:p@localhost/db"

#: Colunas que a 0012 acrescenta a ``pipeline_jobs``.
_ENRIQUECIMENTO = ("formulario", "patrimonio", "projeto", "n_linhas", "sisloc_snapshot")

#: As que a aplicação **consulta** — e por isso precisam de índice.
_INDEXADAS = ("formulario", "patrimonio", "projeto")


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
    return _sql("0011:0012")


def test_migration_cria_todas_as_colunas_do_modelo(upgrade_sql: str) -> None:
    """Paridade ORM ↔ migration: coluna no modelo sem coluna no banco é bug mudo."""
    for coluna in _ENRIQUECIMENTO:
        assert f"ADD COLUMN {coluna}" in upgrade_sql
        assert coluna in PipelineJob.__table__.columns


def test_snapshot_e_jsonb_e_nao_json(upgrade_sql: str) -> None:
    """O `metrics` usa JSON puro; é um erro existente e não se replica erro."""
    assert "ADD COLUMN sisloc_snapshot JSONB" in upgrade_sql


def test_larguras_espelham_as_da_view(upgrade_sql: str) -> None:
    """`varchar(30)`/`varchar(15)`/`varchar(200)` medidos em INFORMATION_SCHEMA."""
    assert "ADD COLUMN formulario VARCHAR(30)" in upgrade_sql
    assert "ADD COLUMN patrimonio VARCHAR(15)" in upgrade_sql
    assert "ADD COLUMN projeto VARCHAR(200)" in upgrade_sql


def test_as_tres_colunas_consultadas_tem_indice(upgrade_sql: str) -> None:
    """Filtro por formulário, busca por ativo, busca por cliente."""
    for coluna in _INDEXADAS:
        assert f"CREATE INDEX ix_pipeline_jobs_{coluna} ON pipeline_jobs ({coluna})" in upgrade_sql


def test_n_linhas_nao_tem_indice(upgrade_sql: str) -> None:
    """É aviso de tela, não critério de busca — índice aqui seria custo à toa."""
    assert "ix_pipeline_jobs_n_linhas" not in upgrade_sql


def test_colunas_sao_nullable(upgrade_sql: str) -> None:
    """Jobs antigos e o caminho `POST /pipeline/run` nascem sem enriquecimento."""
    for coluna in _ENRIQUECIMENTO:
        assert f"ADD COLUMN {coluna}" in upgrade_sql
        assert f"ADD COLUMN {coluna} NOT NULL" not in upgrade_sql


def test_downgrade_desfaz_tudo() -> None:
    sql = _sql("0012:0011", downgrade=True)
    for coluna in _ENRIQUECIMENTO:
        assert f"DROP COLUMN {coluna}" in sql
    for coluna in _INDEXADAS:
        assert f"DROP INDEX ix_pipeline_jobs_{coluna}" in sql
