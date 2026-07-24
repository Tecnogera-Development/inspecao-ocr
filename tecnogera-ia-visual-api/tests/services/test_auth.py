"""Testes do serviço de autenticação — IAVS-030."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.user import User
from app.services.auth import authenticate


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


def _create_user(db: Session, email: str, password: str, is_active: bool = True) -> User:
    import bcrypt

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=password_hash, is_active=is_active)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.mark.unit
def test_authenticate_senha_correta_retorna_usuario(db: Session) -> None:
    _create_user(db, "celio@tecnogera.com", "senha123")
    result = authenticate(db, "celio@tecnogera.com", "senha123")
    assert result is not None
    assert result.email == "celio@tecnogera.com"


@pytest.mark.unit
def test_authenticate_senha_errada_retorna_none(db: Session) -> None:
    _create_user(db, "celio@tecnogera.com", "senha123")
    result = authenticate(db, "celio@tecnogera.com", "errada")
    assert result is None


@pytest.mark.unit
def test_authenticate_usuario_inativo_retorna_none(db: Session) -> None:
    _create_user(db, "inativo@tecnogera.com", "senha123", is_active=False)
    result = authenticate(db, "inativo@tecnogera.com", "senha123")
    assert result is None


@pytest.mark.unit
def test_authenticate_usuario_inexistente_retorna_none(db: Session) -> None:
    result = authenticate(db, "naoexiste@tecnogera.com", "senha123")
    assert result is None


# ── CLI create_user ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_create_user_cli_insere_usuario(db: Session) -> None:
    from app.cli import _run_create_user

    _run_create_user("edelmar@tecnogera.com", "s3cr3t", db_factory=lambda: db)

    user = db.query(User).filter_by(email="edelmar@tecnogera.com").first()
    assert user is not None
    assert user.is_active is True


@pytest.mark.unit
def test_create_user_cli_falha_se_email_duplicado(db: Session) -> None:
    from app.cli import _run_create_user

    _run_create_user("dup@tecnogera.com", "p1", db_factory=lambda: db)
    with pytest.raises(SystemExit):
        _run_create_user("dup@tecnogera.com", "p2", db_factory=lambda: db)


@pytest.mark.unit
def test_main_delega_para_run_create_user() -> None:
    from unittest.mock import patch

    from app.cli import main

    with patch("app.cli._run_create_user") as mock_run:
        main(["create_user", "--email", "x@x.com", "--password", "pw"])

    mock_run.assert_called_once_with("x@x.com", "pw")


# ── ensure_initial_user (seed no boot) ─────────────────────────────────────


@pytest.mark.unit
def test_ensure_initial_user_cria_quando_ausente(db: Session) -> None:
    from app.cli import ensure_initial_user

    created = ensure_initial_user(db, "boot@tecnogera.com", "s3cr3t")

    assert created is True
    user = db.query(User).filter_by(email="boot@tecnogera.com").first()
    assert user is not None
    assert authenticate(db, "boot@tecnogera.com", "s3cr3t") is not None


@pytest.mark.unit
def test_ensure_initial_user_idempotente_nao_duplica(db: Session) -> None:
    from app.cli import ensure_initial_user

    assert ensure_initial_user(db, "boot@tecnogera.com", "primeira") is True
    # Segunda chamada não recria nem altera a senha existente.
    assert ensure_initial_user(db, "boot@tecnogera.com", "outra") is False

    assert db.query(User).filter_by(email="boot@tecnogera.com").count() == 1
    # Senha original preservada (a 2ª senha é ignorada).
    assert authenticate(db, "boot@tecnogera.com", "primeira") is not None
    assert authenticate(db, "boot@tecnogera.com", "outra") is None
