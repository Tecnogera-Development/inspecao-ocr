"""CLI create_user × papel — ticket ``usuarios-portal/01``.

`create_user_in_db()` ganha o papel: é o bootstrap do primeiro admin — sem
isso não existe ninguém que possa criar o segundo (ver `app/cli.py`).

Arquivo novo, separado de `tests/services/test_auth.py`, para não inchar o
arquivo existente enquanto outro agente trabalha em paralelo.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli import PapelInvalidoError, _run_create_user, create_user_in_db
from app.db.base import Base
from app.models.user import ROLE_ADMIN, ROLE_OPERADOR, User

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
def test_create_user_in_db_sem_role_cria_operador(db: Session) -> None:
    create_user_in_db(db, "operador@tecnogera.com", "s3nha123")

    user = db.query(User).filter_by(email="operador@tecnogera.com").first()
    assert user is not None
    assert user.role == ROLE_OPERADOR


@pytest.mark.unit
def test_create_user_in_db_com_role_admin_cria_admin(db: Session) -> None:
    create_user_in_db(db, "admin@tecnogera.com", "s3nha123", role=ROLE_ADMIN)

    user = db.query(User).filter_by(email="admin@tecnogera.com").first()
    assert user is not None
    assert user.role == ROLE_ADMIN


@pytest.mark.unit
def test_create_user_in_db_com_role_invalido_recusa_com_mensagem_clara(db: Session) -> None:
    with pytest.raises(PapelInvalidoError, match="papel inválido"):
        create_user_in_db(db, "invalido@tecnogera.com", "s3nha123", role="superadmin")

    # Nada foi persistido: a rejeição é antes do INSERT.
    assert db.query(User).filter_by(email="invalido@tecnogera.com").first() is None


@pytest.mark.unit
def test_run_create_user_com_role_invalido_sai_com_erro_e_nao_500(db: Session) -> None:
    """Caminho de CLI: `PapelInvalidoError` vira `sys.exit(1)`, não traceback cru."""
    with pytest.raises(SystemExit) as exc_info:
        _run_create_user(
            "invalido2@tecnogera.com", "s3nha123", "role-que-nao-existe", db_factory=lambda: db
        )

    assert exc_info.value.code == 1
    assert db.query(User).filter_by(email="invalido2@tecnogera.com").first() is None


@pytest.mark.unit
def test_run_create_user_com_role_admin_persiste_admin(db: Session) -> None:
    _run_create_user("admin2@tecnogera.com", "s3nha123", ROLE_ADMIN, db_factory=lambda: db)

    user = db.query(User).filter_by(email="admin2@tecnogera.com").first()
    assert user is not None
    assert user.role == ROLE_ADMIN
