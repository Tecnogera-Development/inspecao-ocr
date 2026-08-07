"""ensure_initial_user — seed idempotente do admin inicial via env.

Reconciliação: a v1.2.1 não tem seed por env (só CLI). Este seed cria o
PRIMEIRO admin de forma não-interativa (INITIAL_ADMIN_EMAIL/PASSWORD) e — o
ponto crítico — com papel ``admin`` (não ``operador``), senão a tela de
Usuários daria 403 e o bootstrap falharia em silêncio.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli import ensure_initial_user
from app.db.base import Base
from app.models.user import ROLE_ADMIN, User

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


def test_cria_admin_quando_ausente(db: Session) -> None:
    created = ensure_initial_user(db, "boot@tecnogera.com", "s3nha123")

    assert created is True
    user = db.query(User).filter_by(email="boot@tecnogera.com").first()
    assert user is not None
    assert user.role == ROLE_ADMIN  # CRÍTICO: nasce admin, não operador
    assert user.password_hash is not None


def test_idempotente_nao_duplica(db: Session) -> None:
    assert ensure_initial_user(db, "boot@tecnogera.com", "primeira") is True
    # Segunda chamada não recria nem altera nada.
    assert ensure_initial_user(db, "boot@tecnogera.com", "outra") is False
    assert db.query(User).filter_by(email="boot@tecnogera.com").count() == 1
