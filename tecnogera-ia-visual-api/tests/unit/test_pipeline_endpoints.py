"""Testes dos endpoints de pipeline — IAVS-001."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.pipeline import PipelineJob


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
def sqlite_session(sqlite_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def pipeline_client(settings, sqlite_session: Session) -> TestClient:
    from app.core.config import get_settings

    def _override_db():
        yield sqlite_session

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_post_run_retorna_202_com_job_id(pipeline_client: TestClient) -> None:
    with patch("app.routers.pipeline.BackgroundTasks.add_task"):
        resp = pipeline_client.post(
            "/api/v1/pipeline/run", json={"checklist_id": "276800"}
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "pending"
    uuid.UUID(body["job_id"])  # deve ser UUID válido


@pytest.mark.unit
def test_post_run_sem_checklist_id_retorna_422(pipeline_client: TestClient) -> None:
    with patch("app.routers.pipeline.BackgroundTasks.add_task"):
        resp = pipeline_client.post("/api/v1/pipeline/run", json={})
    assert resp.status_code == 422


@pytest.mark.unit
def test_get_job_retorna_estado(pipeline_client: TestClient, sqlite_session: Session) -> None:
    job_id = uuid.uuid4()
    job = PipelineJob(
        id=job_id,
        checklist_id="276800",
        status="done",
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        metrics={"duration_total_ms": 5000},
    )
    sqlite_session.add(job)
    sqlite_session.commit()

    resp = pipeline_client.get(f"/api/v1/pipeline/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["checklist_id"] == "276800"
    assert body["metrics"]["duration_total_ms"] == 5000


@pytest.mark.unit
def test_get_job_inexistente_retorna_404(pipeline_client: TestClient) -> None:
    resp = pipeline_client.get(f"/api/v1/pipeline/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.unit
def test_get_jobs_lista_vazia(pipeline_client: TestClient) -> None:
    resp = pipeline_client.get("/api/v1/pipeline/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.fixture
def pipeline_client_com_key(sqlite_session: Session) -> TestClient:
    """Client com PIPELINE_API_KEY configurada (produção-like)."""
    from app.core.config import AppEnv, Settings, get_settings

    s = Settings(_env_file=None, app_env=AppEnv.TEST, pipeline_api_key="segredo-x")

    def _override_db():
        yield sqlite_session

    app = create_app(s)
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_get_jobs_exige_api_key_quando_configurada(pipeline_client_com_key: TestClient) -> None:
    resp = pipeline_client_com_key.get("/api/v1/pipeline/jobs")
    assert resp.status_code == 401


@pytest.mark.unit
def test_get_jobs_aceita_api_key_valida(pipeline_client_com_key: TestClient) -> None:
    resp = pipeline_client_com_key.get(
        "/api/v1/pipeline/jobs", headers={"X-API-Key": "segredo-x"}
    )
    assert resp.status_code == 200


@pytest.mark.unit
def test_get_job_por_id_exige_api_key(pipeline_client_com_key: TestClient) -> None:
    resp = pipeline_client_com_key.get(f"/api/v1/pipeline/jobs/{uuid.uuid4()}")
    assert resp.status_code == 401


@pytest.mark.unit
def test_get_jobs_retorna_jobs(pipeline_client: TestClient, sqlite_session: Session) -> None:
    for i in range(3):
        sqlite_session.add(
            PipelineJob(
                id=uuid.uuid4(),
                checklist_id=f"27680{i}",
                status="done",
                created_at=datetime.now(UTC),
            )
        )
    sqlite_session.commit()

    resp = pipeline_client.get("/api/v1/pipeline/jobs")
    assert resp.status_code == 200
    assert len(resp.json()) == 3
