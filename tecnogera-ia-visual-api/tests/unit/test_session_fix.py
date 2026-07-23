"""Regressão IAVS-067: Session de request não pode cruzar fronteira do background.

Garante que _run_pipeline_async e _run_batch_async:
  - não aceitam Session como parâmetro
  - criam a própria Session via get_session_factory()
  - 30 chamadas concorrentes não levantam exceção
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.pipeline import PipelineJob
from app.routers.pipeline import _run_batch_async, _run_pipeline_async


# ── assinaturas não aceitam mais db ──────────────────────────────────────────

@pytest.mark.unit
def test_run_pipeline_async_sem_parametro_db() -> None:
    sig = inspect.signature(_run_pipeline_async)
    assert "db" not in sig.parameters, (
        "IAVS-067: _run_pipeline_async não deve aceitar Session de request"
    )


@pytest.mark.unit
def test_run_batch_async_sem_parametro_db() -> None:
    sig = inspect.signature(_run_batch_async)
    assert "db" not in sig.parameters, (
        "IAVS-067: _run_batch_async não deve aceitar Session de request"
    )


# ── background cria Session própria ──────────────────────────────────────────

@pytest.fixture
def in_memory_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_pipeline_async_usa_sessao_propria(in_memory_engine) -> None:
    """Background task cria Session própria — sem DetachedInstanceError."""
    factory = sessionmaker(bind=in_memory_engine, autocommit=False, autoflush=False)
    job_id = uuid.uuid4()

    # Cria o job e fecha a request-session (simula FastAPI retornando 202)
    with factory() as req_db:
        req_db.add(PipelineJob(
            id=job_id, checklist_id="999", status="pending",
            created_at=datetime.now(UTC),
        ))
        req_db.commit()

    settings_mock = MagicMock()
    settings_mock.pipeline_timeout_seconds = 5

    # Patch nos módulos onde as classes são originalmente definidas
    with (
        patch("app.db.session.get_session_factory", return_value=factory),
        patch("app.services.dropbox.DropboxService"),
        patch("app.services.orchestrator.Orchestrator") as MockOrch,
    ):
        MockOrch.return_value.run = MagicMock(return_value=None)
        # Não deve levantartDetachedInstanceError nem qualquer exceção de sessão
        await _run_pipeline_async(job_id, "999", settings_mock)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_30_tasks_concorrentes_sem_excecao(in_memory_engine) -> None:
    """30 tarefas concorrentes, cada uma com Session própria, sem exaustão."""
    factory = sessionmaker(bind=in_memory_engine, autocommit=False, autoflush=False)
    job_ids: list[uuid.UUID] = []

    with factory() as setup_db:
        for _ in range(30):
            jid = uuid.uuid4()
            job_ids.append(jid)
            setup_db.add(PipelineJob(
                id=jid, checklist_id="multi", status="pending",
                created_at=datetime.now(UTC),
            ))
        setup_db.commit()

    settings_mock = MagicMock()
    settings_mock.pipeline_timeout_seconds = 5

    async def _run_one(jid: uuid.UUID) -> None:
        with (
            patch("app.db.session.get_session_factory", return_value=factory),
            patch("app.services.dropbox.DropboxService"),
            patch("app.services.orchestrator.Orchestrator") as MockOrch,
        ):
            MockOrch.return_value.run = MagicMock(return_value=None)
            await _run_pipeline_async(jid, "multi", settings_mock)

    # Nenhuma tarefa deve levantar exceção
    await asyncio.gather(*[_run_one(jid) for jid in job_ids])


# ── get_session_factory exposta como API pública ──────────────────────────────

@pytest.mark.unit
def test_get_session_factory_exportada() -> None:
    from app.db import session as sess_mod

    assert hasattr(sess_mod, "get_session_factory")
    assert callable(sess_mod.get_session_factory)
