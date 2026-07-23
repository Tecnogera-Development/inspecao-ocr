"""Testes de GET /api/v1/portal/stats — IAVS-033."""

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


def _make_job(
    db: Session,
    *,
    status: str = "done",
    checklist_id: str = "111111",
    metrics: dict | None = None,
) -> PipelineJob:
    now = datetime.now(UTC)
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status=status,
        created_at=now,
        updated_at=now,
        finished_at=now if status == "done" else None,
        metrics=metrics,
    )
    db.add(job)
    db.commit()
    return job


# ── autenticação ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_portal_stats_sem_sessao_retorna_401(portal_client: TestClient) -> None:
    resp = portal_client.get("/api/v1/portal/stats")
    assert resp.status_code == 401


# ── happy path ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_portal_stats_retorna_200_com_sessao(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)

    resp = portal_client.get("/api/v1/portal/stats")
    assert resp.status_code == 200


@pytest.mark.unit
def test_portal_stats_schema_correto(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)

    resp = portal_client.get("/api/v1/portal/stats")
    body = resp.json()
    assert "total_done" in body
    assert "in_progress" in body
    assert "failed" in body
    assert "total_cost_usd" in body
    assert "accuracy_last_week" in body


@pytest.mark.unit
def test_portal_stats_contadores_corretos(portal_client: TestClient, db: Session) -> None:
    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")

    _make_user(db)
    _make_job(db, status="done")
    _make_job(db, status="running")
    _make_job(db, status="failed")
    _login(portal_client)

    resp = portal_client.get(f"/api/v1/portal/stats?month={month}")
    body = resp.json()
    assert body["total_done"] == 1
    assert body["in_progress"] == 1
    assert body["failed"] == 1


@pytest.mark.unit
def test_portal_stats_accuracy_null_sem_eval(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _make_job(db, status="done", metrics={"estimated_cost_usd": 0.10})
    _login(portal_client)

    resp = portal_client.get("/api/v1/portal/stats")
    assert resp.json()["accuracy_last_week"] is None
