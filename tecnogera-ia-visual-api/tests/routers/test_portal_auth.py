"""Testes dos endpoints de autenticação do portal — IAVS-031."""

from __future__ import annotations

from collections.abc import Generator

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.user import User


@pytest.fixture
def portal_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        session_secret="test-secret-key-32-chars-minimum!",
    )


@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db(sqlite_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def portal_client(portal_settings: Settings, db: Session) -> TestClient:
    def _override_db():
        yield db

    app = create_app(portal_settings)
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: portal_settings
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


def _make_user(db: Session, email: str = "celio@tecnogera.com", password: str = "s3cr3t") -> User:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=hashed, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ── login ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_login_valido_retorna_200_e_seta_cookie(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    resp = portal_client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "celio@tecnogera.com"
    assert "id" in body
    assert "session" in portal_client.cookies


@pytest.mark.unit
def test_login_invalido_retorna_401(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    resp = portal_client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "errada"},
    )
    assert resp.status_code == 401


# ── /me ───────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_me_sem_sessao_retorna_401(portal_client: TestClient) -> None:
    resp = portal_client.get("/api/v1/portal/me")
    assert resp.status_code == 401


@pytest.mark.unit
def test_me_com_sessao_valida_retorna_200(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    portal_client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )
    resp = portal_client.get("/api/v1/portal/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "celio@tecnogera.com"


# ── logout ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_logout_invalida_sessao(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    # login — obtém sessão + token CSRF
    portal_client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )
    csrf_token = portal_client.get("/api/v1/portal/csrf").json()["token"]

    # logout com CSRF válido
    resp = portal_client.post("/api/v1/portal/logout", headers={"X-CSRF-Token": csrf_token})
    assert resp.status_code == 204

    # após logout, /me deve retornar 401
    resp = portal_client.get("/api/v1/portal/me")
    assert resp.status_code == 401


# ── CSRF ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_post_protegido_sem_csrf_retorna_403(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    portal_client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )
    # logout sem X-CSRF-Token deve retornar 403
    resp = portal_client.post("/api/v1/portal/logout")
    assert resp.status_code == 403
