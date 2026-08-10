"""authenticate() × password_hash nulo — ticket ``usuarios-portal/01``.

Este é o ponto exato descrito no ticket: a partir da migration 0014,
`password_hash` pode ser nulo (usuário na janela de primeira senha, ainda
sem senha própria). Sem este teste, um `bcrypt.checkpw(..., None.encode())`
levantaria `AttributeError` e a rota de login devolveria 500 em vez de 401.

Arquivo novo (não em `tests/services/test_auth.py`) para não colidir com o
agente paralelo que mexe em `app/routers/portal.py` e rate limiting.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.user import User
from app.services.auth import authenticate

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


@pytest.mark.unit
def test_authenticate_com_password_hash_nulo_retorna_none_nao_levanta(db: Session) -> None:
    """O caso central: sem isso, seria AttributeError (500), não 401."""
    user = User(email="convidado@tecnogera.com", password_hash=None)
    db.add(user)
    db.commit()

    resultado = authenticate(db, "convidado@tecnogera.com", "qualquer-coisa")

    assert resultado is None


@pytest.mark.unit
def test_authenticate_com_password_hash_nulo_e_senha_vazia_tambem_retorna_none(
    db: Session,
) -> None:
    user = User(email="convidado2@tecnogera.com", password_hash=None)
    db.add(user)
    db.commit()

    resultado = authenticate(db, "convidado2@tecnogera.com", "")

    assert resultado is None


@pytest.mark.unit
def test_authenticate_com_password_hash_nulo_e_usuario_inativo_ainda_retorna_none(
    db: Session,
) -> None:
    """Não regride o que já funciona: inativo continua recusado, mesmo
    combinado com o novo estado (senha nula).
    """
    user = User(
        email="convidado3@tecnogera.com",
        password_hash=None,
        is_active=False,
    )
    db.add(user)
    db.commit()

    resultado = authenticate(db, "convidado3@tecnogera.com", "qualquer-coisa")

    assert resultado is None
