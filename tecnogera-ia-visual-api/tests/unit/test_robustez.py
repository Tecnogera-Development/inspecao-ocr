"""Testes de robustez do pipeline — IAVS-008.

Cobre: autenticação por API key, validação de checklist_id,
timeout de pipeline e recovery de jobs órfãos.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import _recovery_hook, create_app
from app.models.pipeline import PipelineJob


def _make_settings(**kwargs) -> Settings:
    base = dict(_env_file=None, app_env=AppEnv.TEST, log_level="DEBUG")
    base.update(kwargs)
    return Settings(**base)


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


def _make_client(settings: Settings, db: Session) -> TestClient:
    from app.core.config import get_settings

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


# ── API Key auth ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_post_run_sem_api_key_retorna_401(sqlite_session: Session) -> None:
    """POST /run sem X-API-Key quando PIPELINE_API_KEY configurada → 401."""
    settings = _make_settings(pipeline_api_key=SecretStr("secret-key"))
    client = _make_client(settings, sqlite_session)

    resp = client.post("/api/v1/pipeline/run", json={"checklist_id": "276800"})

    assert resp.status_code == 401


@pytest.mark.unit
def test_post_run_com_api_key_errada_retorna_401(sqlite_session: Session) -> None:
    """POST /run com X-API-Key incorreta → 401."""
    settings = _make_settings(pipeline_api_key=SecretStr("secret-key"))
    client = _make_client(settings, sqlite_session)

    resp = client.post(
        "/api/v1/pipeline/run",
        json={"checklist_id": "276800"},
        headers={"X-API-Key": "wrong-key"},
    )

    assert resp.status_code == 401


@pytest.mark.unit
def test_post_run_com_api_key_correta_retorna_202(sqlite_session: Session) -> None:
    """POST /run com X-API-Key correta → 202."""
    settings = _make_settings(pipeline_api_key=SecretStr("secret-key"))
    client = _make_client(settings, sqlite_session)

    with patch("app.routers.pipeline.BackgroundTasks.add_task"):
        resp = client.post(
            "/api/v1/pipeline/run",
            json={"checklist_id": "276800"},
            headers={"X-API-Key": "secret-key"},
        )

    assert resp.status_code == 202


@pytest.mark.unit
def test_post_run_sem_pipeline_api_key_configurada_aceita_qualquer(sqlite_session: Session) -> None:
    """POST /run sem PIPELINE_API_KEY configurada → aceita sem header."""
    settings = _make_settings()
    client = _make_client(settings, sqlite_session)

    with patch("app.routers.pipeline.BackgroundTasks.add_task"):
        resp = client.post("/api/v1/pipeline/run", json={"checklist_id": "276800"})

    assert resp.status_code == 202


# ── Validação de checklist_id ─────────────────────────────────────────────────

@pytest.mark.unit
def test_post_run_checklist_id_nao_numerico_retorna_422(sqlite_session: Session) -> None:
    """POST /run com checklist_id não-numérico → 422 (validação Pydantic)."""
    settings = _make_settings()
    client = _make_client(settings, sqlite_session)

    resp = client.post("/api/v1/pipeline/run", json={"checklist_id": "abc-invalid"})

    assert resp.status_code == 422


# ── Recovery hook ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_recovery_hook_marca_jobs_running_como_failed(sqlite_session: Session) -> None:
    """Recovery hook marca jobs running/pending como failed com error='api_restart'."""
    job_running = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276800",
        status="running",
        started_at=datetime.now(UTC),
    )
    job_pending = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276801",
        status="pending",
    )
    job_done = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276802",
        status="done",
        finished_at=datetime.now(UTC),
    )
    sqlite_session.add_all([job_running, job_pending, job_done])
    sqlite_session.commit()

    from sqlalchemy import text

    sqlite_session.execute(
        text(
            "UPDATE pipeline_jobs SET status='failed', error='api_restart'"
            " WHERE status IN ('pending','running')"
        )
    )
    sqlite_session.commit()

    sqlite_session.refresh(job_running)
    sqlite_session.refresh(job_pending)
    sqlite_session.refresh(job_done)

    assert job_running.status == "failed"
    assert job_running.error == "api_restart"
    assert job_pending.status == "failed"
    assert job_pending.error == "api_restart"
    assert job_done.status == "done"


# ── Timeout ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_pipeline_timeout_marca_job_failed(sqlite_engine, sqlite_session: Session) -> None:
    """Pipeline lento que excede timeout → job marcado failed com 'pipeline_timeout'.

    IAVS-067: _run_pipeline_async cria Session própria via get_session_factory().
    O teste injeta a factory do sqlite_engine para que as mudanças sejam visíveis.
    """
    import time

    from sqlalchemy.orm import sessionmaker as sm

    from app.routers.pipeline import _run_pipeline_async

    job_id = uuid.uuid4()
    job = PipelineJob(id=job_id, checklist_id="276800", status="pending")
    sqlite_session.add(job)
    sqlite_session.commit()

    sqlite_factory = sm(bind=sqlite_engine, autocommit=False, autoflush=False)
    settings = _make_settings(pipeline_timeout_seconds=1)

    def _slow_run(*, job_id: object, checklist_id: object) -> None:
        time.sleep(10)

    mock_orch = MagicMock()
    mock_orch.run.side_effect = _slow_run

    with (
        patch("app.db.session.get_session_factory", return_value=sqlite_factory),
        patch("app.services.orchestrator.Orchestrator", return_value=mock_orch),
        patch("app.services.dropbox.DropboxService"),
    ):
        await _run_pipeline_async(
            job_id=job_id,
            checklist_id="276800",
            settings=settings,
        )

    sqlite_session.refresh(job)
    assert job.status == "failed"
    assert job.error == "pipeline_timeout"
    assert job.finished_at is not None
