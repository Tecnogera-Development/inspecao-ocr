"""Testes do BatchPoller.poll_once — IAVS-043."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.models.pipeline import PipelineJob


# ── Helpers ───────────────────────────────────────────────────────────────────


@dataclass
class _FakeBatchStatus:
    processing_status: str = "ended"


@dataclass
class _FakeToolInput:
    field_name: str = "c0"
    confidence: float = 0.90
    observation: str = "OK"
    detected_issues: list[str] = field(default_factory=list)


@dataclass
class _FakeToolBlock:
    type: str = "tool_use"
    name: str = "emit_classification"
    input: dict = field(default_factory=lambda: {
        "field_name": "c0",
        "confidence": 0.90,
        "observation": "OK",
        "detected_issues": [],
    })


@dataclass
class _FakeMessage:
    content: list[Any] = field(default_factory=lambda: [_FakeToolBlock()])


@dataclass
class _FakeSucceededResult:
    type: str = "succeeded"
    message: _FakeMessage = field(default_factory=_FakeMessage)


@dataclass
class _FakeBatchResult:
    custom_id: str
    result: Any = field(default_factory=_FakeSucceededResult)


def _make_fake_batch_results(filenames: list[str]) -> list[_FakeBatchResult]:
    return [_FakeBatchResult(custom_id=fn) for fn in filenames]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def poller_settings() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST, llm_provider="fake")


@pytest.fixture
def poller_engine():
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
def poller_db(poller_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=poller_engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _make_pending_batch_job(db: Session, *, batch_id: str = "batch_abc123") -> PipelineJob:
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276800",
        status="pending_batch",
        mode="batch",
        batch_id=batch_id,
        metrics={"profile_id": "_unknown_fallback"},
    )
    db.add(job)
    db.commit()
    return job


def _make_mock_provider(
    *,
    processing_status: str = "ended",
    results: list[Any] | None = None,
) -> MagicMock:
    provider = MagicMock()
    provider.retrieve_batch.return_value = _FakeBatchStatus(processing_status=processing_status)
    provider.get_batch_results.return_value = results if results is not None else []
    return provider


# ── Tracer bullet ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_poll_once_batch_ended_resume_pipeline(
    poller_db: Session, poller_settings: Settings
) -> None:
    """Quando batch processing_status=ended, continue_after_batch é chamado e stats refletem resolução."""
    from app.services.batch_poller import BatchPoller

    job = _make_pending_batch_job(poller_db, batch_id="batch_abc")
    provider = _make_mock_provider(processing_status="ended")
    dropbox = MagicMock()

    with patch("app.services.batch_poller.Orchestrator") as MockOrch:
        mock_orch_instance = MagicMock()
        MockOrch.return_value = mock_orch_instance

        poller = BatchPoller(poller_db, dropbox, provider, settings=poller_settings)
        stats = poller.poll_once()

    assert stats["n_polled"] == 1
    assert stats["n_resolved"] == 1
    mock_orch_instance.continue_after_batch.assert_called_once_with(job.id, [])


@pytest.mark.unit
def test_poll_once_idempotente_job_ja_resolvido(
    poller_db: Session, poller_settings: Settings
) -> None:
    """Se o job mudou de pending_batch antes do continue_after_batch, poller não chama continue."""
    from app.services.batch_poller import BatchPoller

    job = _make_pending_batch_job(poller_db, batch_id="batch_xyz")
    provider = _make_mock_provider(processing_status="ended")
    dropbox = MagicMock()

    def _change_status_side_effect(job_id: Any, results: Any) -> None:
        # Simula outro processo resolvendo o job antes deste poller
        poller_db.query(PipelineJob).filter(PipelineJob.id == job.id).update(
            {"status": "done"}
        )
        poller_db.commit()

    with patch("app.services.batch_poller.Orchestrator") as MockOrch:
        mock_orch_instance = MagicMock()
        MockOrch.return_value = mock_orch_instance

        # Simula refresh mudando o status para "done" antes de continue_after_batch
        original_refresh = poller_db.refresh
        call_count = [0]

        def patched_refresh(obj: Any) -> None:
            call_count[0] += 1
            if isinstance(obj, PipelineJob) and obj.id == job.id:
                obj.status = "done"  # simula job já resolvido
            else:
                original_refresh(obj)

        poller_db.refresh = patched_refresh  # type: ignore[method-assign]

        poller = BatchPoller(poller_db, dropbox, provider, settings=poller_settings)
        stats = poller.poll_once()

    assert stats["n_polled"] == 1
    assert stats["n_skipped"] == 1
    assert stats["n_resolved"] == 0
    mock_orch_instance.continue_after_batch.assert_not_called()


@pytest.mark.unit
def test_poll_once_erro_anthropic_marca_job_failed(
    poller_db: Session, poller_settings: Settings
) -> None:
    """Quando retrieve_batch levanta exceção, job vai para failed com batch_error:."""
    from app.services.batch_poller import BatchPoller

    job = _make_pending_batch_job(poller_db, batch_id="batch_bad")
    provider = MagicMock()
    provider.retrieve_batch.side_effect = RuntimeError("api_overloaded")
    dropbox = MagicMock()

    with patch("app.services.batch_poller.Orchestrator"):
        poller = BatchPoller(poller_db, dropbox, provider, settings=poller_settings)
        stats = poller.poll_once()

    poller_db.refresh(job)
    assert stats["n_failed"] == 1
    assert job.status == "failed"
    assert job.error is not None
    assert "batch_error" in job.error


# ── continue_after_batch ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_continue_after_batch_happy_path_job_fica_done(
    tmp_path: Any, poller_db: Session, poller_settings: Settings
) -> None:
    """continue_after_batch parseia resultados e completa o pipeline: job fica done."""
    from app.services.orchestrator import Orchestrator

    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276800",
        status="pending_batch",
        mode="batch",
        batch_id="batch_done_123",
        metrics={"profile_id": "_unknown_fallback"},
    )
    poller_db.add(job)
    poller_db.commit()

    raw_results = _make_fake_batch_results(["276800_c0_2026-01-01_10-00.jpeg"])

    dropbox = MagicMock()
    report_mock = MagicMock()
    report_mock.dropbox_path = "/comparativo/276800.pdf"
    dropbox.upload_report.return_value = report_mock

    with patch("app.services.orchestrator.PdfRendererService") as MockRenderer:
        MockRenderer.return_value.render.return_value = b"%PDF fake"
        orch = Orchestrator(db=poller_db, dropbox=dropbox, settings=poller_settings)
        orch.continue_after_batch(job.id, raw_results)

    poller_db.refresh(job)
    assert job.status == "done"
    assert job.batch_resolved_at is not None
    assert job.result_pdf_path == "/comparativo/276800.pdf"


@pytest.mark.unit
def test_continue_after_batch_idempotente_job_nao_pending_batch(
    poller_db: Session, poller_settings: Settings
) -> None:
    """continue_after_batch é no-op se job não está em pending_batch."""
    from app.services.orchestrator import Orchestrator

    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276800",
        status="done",
        mode="batch",
        batch_id="batch_already_done",
        metrics={},
    )
    poller_db.add(job)
    poller_db.commit()

    dropbox = MagicMock()
    orch = Orchestrator(db=poller_db, dropbox=dropbox, settings=poller_settings)
    orch.continue_after_batch(job.id, [])

    dropbox.upload_report.assert_not_called()
    poller_db.refresh(job)
    assert job.status == "done"  # unchanged
