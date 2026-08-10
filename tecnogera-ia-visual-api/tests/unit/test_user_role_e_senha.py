"""Modelo User × papel e janela de primeira senha — ticket ``usuarios-portal/01``.

Testes de dado real (SQLite em memória, mesmo fixture de
``tests/services/test_auth.py``), não só texto de SQL: provam o
comportamento do ORM e o CHECK constraint em execução, não apenas o que a
migration *pretende* fazer.
"""

from __future__ import annotations

from collections.abc import Generator

import bcrypt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.user import ROLE_ADMIN, ROLE_OPERADOR, ROLES, User

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ── role: ninguém vira admin por acidente ───────────────────────────────────


@pytest.mark.unit
def test_usuario_novo_sem_role_explicito_cai_em_operador(db: Session) -> None:
    """Simula o usuário "existente" migrado: nada diz o papel dele, e o
    default do modelo (espelho do DEFAULT da migration) tem que ser
    operador — nunca admin.
    """
    user = User(email="legado@tecnogera.com", password_hash="hash-qualquer")
    db.add(user)
    db.commit()
    db.refresh(user)

    assert user.role == ROLE_OPERADOR
    assert user.role != ROLE_ADMIN


@pytest.mark.unit
def test_role_precisa_ser_explicitamente_admin_para_virar_admin(db: Session) -> None:
    user = User(email="admin@tecnogera.com", password_hash="hash-qualquer", role=ROLE_ADMIN)
    db.add(user)
    db.commit()
    db.refresh(user)

    assert user.role == ROLE_ADMIN


@pytest.mark.unit
def test_check_constraint_recusa_papel_fora_de_admin_operador(db: Session) -> None:
    """O valor errado não pode entrar — nem por fora da validação de
    aplicação. Testa o CHECK do banco, não só `ROLES` em Python.
    """
    user = User(email="hacker@tecnogera.com", password_hash="hash-qualquer", role="superadmin")
    db.add(user)
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.unit
def test_roles_validos_sao_exatamente_admin_e_operador() -> None:
    assert {"admin", "operador"} == ROLES


# ── password_hash nulo: usuário recém-criado ainda não tem senha ───────────


@pytest.mark.unit
def test_password_hash_pode_ficar_nulo(db: Session) -> None:
    user = User(email="convidado@tecnogera.com", password_hash=None)
    db.add(user)
    db.commit()
    db.refresh(user)

    assert user.password_hash is None
    assert user.role == ROLE_OPERADOR  # default continua valendo


# ── código de primeira senha: só o hash, nunca o valor em claro ────────────


@pytest.mark.unit
def test_nenhuma_coluna_guarda_o_codigo_em_claro() -> None:
    """Estrutural: a única coluna relacionada ao código é o hash. Se algum
    dia alguém adicionar `password_setup_code` (sem `_hash`) para "debug",
    este teste pega.
    """
    nomes = {c.name for c in User.__table__.columns}
    assert "password_setup_code_hash" in nomes
    assert "password_setup_code" not in nomes


@pytest.mark.unit
def test_codigo_em_claro_nao_sobrevive_em_nenhuma_coluna_da_linha(db: Session) -> None:
    """Round-trip real: gera um código, grava só o hash, relê a linha do
    banco e varre TODAS as colunas texto — o valor em claro não pode
    aparecer em nenhuma delas.
    """
    codigo_em_claro = "K7QX-93ZP"  # o que o admin repassaria fora de banda
    code_hash = bcrypt.hashpw(codigo_em_claro.encode(), bcrypt.gensalt()).decode()

    user = User(
        email="novato@tecnogera.com",
        password_hash=None,
        password_setup_code_hash=code_hash,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    relido = db.query(User).filter_by(email="novato@tecnogera.com").first()
    assert relido is not None

    for coluna in User.__table__.columns:
        valor = getattr(relido, coluna.name)
        if isinstance(valor, str):
            assert codigo_em_claro not in valor, (
                f"código em claro vazou na coluna {coluna.name!r}"
            )

    # E o hash bate com o código original — prova que o hash é utilizável,
    # não só "não é o texto puro".
    assert bcrypt.checkpw(codigo_em_claro.encode(), relido.password_setup_code_hash.encode())


@pytest.mark.unit
def test_janela_de_primeira_senha_guarda_expiracao_e_tentativas(db: Session) -> None:
    from datetime import UTC, datetime, timedelta

    expira_em = datetime.now(UTC) + timedelta(minutes=30)
    user = User(
        email="janela@tecnogera.com",
        password_hash=None,
        password_setup_code_hash="hash-do-codigo",
        password_setup_expires_at=expira_em,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    assert user.password_setup_attempts == 0  # contador começa zerado
    assert user.password_setup_expires_at is not None
