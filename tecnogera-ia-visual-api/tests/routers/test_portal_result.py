"""Testes de GET /api/v1/portal/jobs/{job_id}/result — IAVS-035."""

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
    error: str | None = None,
) -> PipelineJob:
    now = datetime.now(UTC)
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status=status,
        created_at=now,
        updated_at=now,
        metrics=metrics,
        error=error,
    )
    db.add(job)
    db.commit()
    return job


# ── autenticação ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_portal_result_sem_sessao_retorna_401(portal_client: TestClient) -> None:
    job_id = uuid.uuid4()
    resp = portal_client.get(f"/api/v1/portal/jobs/{job_id}/result")
    assert resp.status_code == 401


@pytest.mark.unit
def test_portal_result_job_inexistente_retorna_404(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)

    resp = portal_client.get(f"/api/v1/portal/jobs/{uuid.uuid4()}/result")
    assert resp.status_code == 404


@pytest.mark.unit
def test_portal_result_job_done_retorna_200_com_estrutura_correta(
    portal_client: TestClient, db: Session
) -> None:
    _make_user(db)
    _login(portal_client)

    job = _make_job(
        db,
        status="done",
        checklist_id="276800",
        metrics={
            "estimated_cost_usd": 0.45,
            "classifications": [
                {
                    "image_filename": "foto_c0.jpg",
                    "field_name": "c0",
                    "confidence": 0.92,
                    "is_valid": True,
                    "requires_human_review": False,
                    "second_best_field": None,
                    "second_best_confidence": None,
                },
                {
                    "image_filename": "foto_c3.jpg",
                    "field_name": "c3",
                    "confidence": 0.55,
                    "is_valid": False,
                    "requires_human_review": True,
                    "second_best_field": "c4",
                    "second_best_confidence": 0.42,
                },
            ],
        },
    )

    resp = portal_client.get(f"/api/v1/portal/jobs/{job.id}/result")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == str(job.id)
    assert body["checklist_id"] == "276800"
    assert body["status"] == "done"
    assert body["estimated_cost_usd"] == pytest.approx(0.45)
    assert len(body["classifications"]) == 2
    assert body["classifications"][0]["photo_id"] == "foto_c0.jpg"
    assert body["classifications"][0]["status"] == "valid"
    assert body["classifications"][1]["status"] == "inconclusive"
    assert body["classifications"][1]["second_best_field"] == "c4"
    assert len(body["inconclusivas"]) == 1
    assert body["inconclusivas"][0]["second_best_field"] == "c4"


@pytest.mark.unit
def test_portal_result_resposta_inclui_etag_header(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)
    job = _make_job(db)

    resp = portal_client.get(f"/api/v1/portal/jobs/{job.id}/result")
    assert resp.status_code == 200
    assert "ETag" in resp.headers


@pytest.mark.unit
def test_portal_result_304_quando_if_none_match_correto(
    portal_client: TestClient, db: Session
) -> None:
    _make_user(db)
    _login(portal_client)
    job = _make_job(db)

    resp = portal_client.get(f"/api/v1/portal/jobs/{job.id}/result")
    etag = resp.headers["ETag"]

    resp2 = portal_client.get(
        f"/api/v1/portal/jobs/{job.id}/result", headers={"If-None-Match": etag}
    )
    assert resp2.status_code == 304
    assert resp2.content == b""


@pytest.mark.unit
def test_portal_result_200_quando_if_none_match_diferente(
    portal_client: TestClient, db: Session
) -> None:
    _make_user(db)
    _login(portal_client)
    job = _make_job(db)

    resp = portal_client.get(
        f"/api/v1/portal/jobs/{job.id}/result", headers={"If-None-Match": "stale-etag"}
    )
    assert resp.status_code == 200
