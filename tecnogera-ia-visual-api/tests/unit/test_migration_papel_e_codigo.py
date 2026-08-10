"""Migration 0014 × modelo ORM — ticket ``usuarios-portal/01``.

Mesmo padrão de ``test_migration_hitl_validacao.py``: compilada em modo
offline contra o dialeto Postgres (o banco real), porque é o dialeto que
``users`` sempre usou (``CITEXT``, ``gen_random_uuid()`` na 0002) e o SQLite
dos testes de dado real não serve de prova para DDL de produção.

O que este arquivo prova, em texto SQL:

1. Usuário existente vira ``operador`` — nunca ``admin`` — porque o
   ``DEFAULT`` aplicado a toda linha já existente é ``'operador'``. Não há
   nenhum ``DEFAULT 'admin'`` em lugar nenhum do upgrade.
2. O CHECK garante no banco (não só na aplicação) que só ``admin`` e
   ``operador`` entram em ``role``.
3. ``password_hash`` deixa de ser NOT NULL — sem isso a janela de primeira
   senha não tem onde morar.
4. As colunas do código de primeira senha guardam hash, não o valor em
   claro: não existe ``password_setup_code`` (sem sufixo ``_hash``) em
   lugar nenhum do schema.
5. O downgrade desfaz as colunas novas, mas **não** reimpõe NOT NULL em
   ``password_hash`` — reimpor perderia a garantia de "sem perder dado" se
   algum usuário estiver na janela de primeira senha no momento do rollback.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from alembic import command
from alembic.config import Config
from app.models.user import ROLE_ADMIN, ROLE_OPERADOR, User

pytestmark = pytest.mark.unit

_POSTGRES_FALSO = "postgresql+psycopg2://u:p@localhost/db"

_COLUNAS_NOVAS = (
    "role",
    "password_setup_code_hash",
    "password_setup_expires_at",
    "password_setup_attempts",
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
    return _sql("0013:0014")


def test_colunas_novas_existem_no_banco_e_no_orm(upgrade_sql: str) -> None:
    for coluna in _COLUNAS_NOVAS:
        assert f"ALTER TABLE users ADD COLUMN {coluna}" in upgrade_sql
        assert coluna in User.__table__.columns


def test_usuario_existente_vira_operador_nunca_admin(upgrade_sql: str) -> None:
    """A prova central do ticket: o DEFAULT aplicado às linhas já existentes
    é `operador`. Não pode haver nenhum caminho no upgrade que grave `admin`.
    """
    assert f"ALTER TABLE users ADD COLUMN role VARCHAR(16) DEFAULT '{ROLE_OPERADOR}' NOT NULL" in (
        upgrade_sql
    )
    assert f"DEFAULT '{ROLE_ADMIN}'" not in upgrade_sql


def test_check_constraint_impede_valor_fora_de_admin_operador(upgrade_sql: str) -> None:
    assert (
        "ALTER TABLE users ADD CONSTRAINT ck_users_role_valido "
        "CHECK (role IN ('admin', 'operador'))"
    ) in upgrade_sql


def test_password_hash_passa_a_aceitar_nulo(upgrade_sql: str) -> None:
    assert "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL" in upgrade_sql


def test_codigo_de_primeira_senha_so_guarda_hash(upgrade_sql: str) -> None:
    """Não pode existir coluna para o código em claro — só o hash."""
    assert "password_setup_code_hash" in upgrade_sql
    assert "password_setup_code TEXT" not in upgrade_sql
    assert "ADD COLUMN password_setup_code " not in upgrade_sql
    nomes_colunas = {c.name for c in User.__table__.columns}
    assert "password_setup_code" not in nomes_colunas
    assert "password_setup_code_hash" in nomes_colunas


def test_contador_de_tentativas_comeca_zero(upgrade_sql: str) -> None:
    assert "password_setup_attempts INTEGER DEFAULT '0' NOT NULL" in upgrade_sql


def test_paridade_completa_do_modelo_de_usuario(upgrade_sql: str) -> None:
    """Nenhuma coluna do ORM pode faltar na soma 0002 (create table) + 0014."""
    criacao = _sql("0002:0014")
    faltando = [
        c.name
        for c in User.__table__.columns
        if c.name not in criacao
        and c.name not in {"id", "email", "created_at", "is_active", "last_login_at"}
    ]
    assert faltando == []


def test_downgrade_remove_colunas_novas_mas_nao_reimpoe_not_null(upgrade_sql: str) -> None:
    del upgrade_sql  # só para garantir que o upgrade acima já rodou antes
    sql = _sql("0014:0013", downgrade=True)

    for coluna in _COLUNAS_NOVAS:
        assert f"ALTER TABLE users DROP COLUMN {coluna}" in sql
    assert "ALTER TABLE users DROP CONSTRAINT ck_users_role_valido" in sql
    # Decisão: não reimpõe NOT NULL — reverter perderia dado se alguém
    # estiver na janela de primeira senha (password_hash nulo por desenho).
    assert "password_hash" not in sql
