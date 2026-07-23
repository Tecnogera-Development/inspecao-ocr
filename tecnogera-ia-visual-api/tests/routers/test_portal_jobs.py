"""Testes de GET /api/v1/portal/jobs — IAVS-032."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
from app.models.pipeline import PipelineJob
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
def db(sqlite_engine) -> Session:
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


def _login(client: TestClient) -> None:
    client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )


def _make_job(db: Session, *, status: str = "done", checklist_id: str = "111111") -> PipelineJob:
    now = datetime.now(UTC)
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()
    return job


# ── autenticação ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_portal_jobs_sem_sessao_retorna_401(portal_client: TestClient) -> None:
    resp = portal_client.get("/api/v1/portal/jobs")
    assert resp.status_code == 401


@pytest.mark.unit
def test_portal_jobs_com_sessao_retorna_200(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)

    resp = portal_client.get("/api/v1/portal/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── ETag / 304 ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_portal_jobs_resposta_inclui_etag_header(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)
    _make_job(db)

    resp = portal_client.get("/api/v1/portal/jobs")
    assert resp.status_code == 200
    assert "ETag" in resp.headers


@pytest.mark.unit
def test_portal_jobs_304_quando_if_none_match_correto(
    portal_client: TestClient, db: Session
) -> None:
    _make_user(db)
    _login(portal_client)
    _make_job(db)

    resp = portal_client.get("/api/v1/portal/jobs")
    etag = resp.headers["ETag"]

    resp2 = portal_client.get("/api/v1/portal/jobs", headers={"If-None-Match": etag})
    assert resp2.status_code == 304
    assert resp2.content == b""


@pytest.mark.unit
def test_portal_jobs_200_quando_if_none_match_diferente(
    portal_client: TestClient, db: Session
) -> None:
    _make_user(db)
    _login(portal_client)
    _make_job(db)

    resp = portal_client.get(
        "/api/v1/portal/jobs", headers={"If-None-Match": "stale-etag"}
    )
    assert resp.status_code == 200
