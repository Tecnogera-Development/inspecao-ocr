"""Testes do Orchestrator.run_batch — IAVS-042."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.models.dropbox import ImageMetadata, LocalImage, ParsedFilename
from app.models.pipeline import PipelineJob
from app.services.orchestrator import Orchestrator


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def batch_settings() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST, llm_provider="fake")


@pytest.fixture
def batch_engine():
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
def batch_db(batch_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=batch_engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _make_local_images(tmp_path: Path, n: int) -> list[LocalImage]:
    """Cria n LocalImage com arquivos reais em tmp_path."""
    images = []
    for i in range(n):
        filename = f"276800_c{i % 10}_2026-01-01_10-00.jpeg"
        p = tmp_path / filename
        p.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]))  # JPEG mínimo
        meta = ImageMetadata(
            dropbox_path=f"/Sisloc/{filename}",
            filename=filename,
            size_bytes=len(p.read_bytes()),
            parsed=ParsedFilename(
                raw=filename,
                checklist_id="276800",
                field_name=f"c{i % 10}",
                captured_at=None,
                extension=".jpeg",
            ),
        )
        images.append(LocalImage(metadata=meta, local_path=p))
    return images


def _make_dropbox_mock(local_images: list[LocalImage]) -> MagicMock:
    mock = MagicMock()
    mock.download_checklist_batch.return_value = local_images
    return mock


# ── Testes ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_run_batch_happy_path_job_fica_pending_batch(
    tmp_path: Path, batch_db: Session, batch_settings: Settings
) -> None:
    """run_batch submete batch e deixa o job em pending_batch com batch_id preenchido."""
    local_images = _make_local_images(tmp_path, n=35)
    dropbox = _make_dropbox_mock(local_images)

    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276800",
        status="pending",
        mode="batch",
    )
    batch_db.add(job)
    batch_db.commit()

    orch = Orchestrator(db=batch_db, dropbox=dropbox, settings=batch_settings)
    orch.run_batch(job_id=job.id, checklist_id="276800")

    batch_db.refresh(job)
    assert job.status == "pending_batch"
    assert job.batch_id is not None
    assert job.batch_submitted_at is not None


@pytest.mark.unit
def test_run_batch_prewarm_chamado_antes_do_batch(
    tmp_path: Path, batch_db: Session, batch_settings: Settings
) -> None:
    """Prewarm (classify_image) deve ser chamado antes de classify_image_batch."""
    local_images = _make_local_images(tmp_path, n=35)
    dropbox = _make_dropbox_mock(local_images)

    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276800",
        status="pending",
        mode="batch",
    )
    batch_db.add(job)
    batch_db.commit()

    call_order: list[str] = []

    from app.services.llm_provider import FakeLLMProvider as _Fake

    class TrackingProvider(_Fake):
        def classify_image(self, *args: Any, **kwargs: Any) -> Any:
            call_order.append("sync")
            return super().classify_image(*args, **kwargs)

        def classify_image_batch(self, *args: Any, **kwargs: Any) -> str:
            call_order.append("batch")
            return super().classify_image_batch(*args, **kwargs)

    with patch("app.services.orchestrator._make_provider", return_value=TrackingProvider()):
        orch = Orchestrator(db=batch_db, dropbox=dropbox, settings=batch_settings)
        orch.run_batch(job_id=job.id, checklist_id="276800")

    assert call_order == ["sync", "batch"], f"esperado ['sync','batch'], obtido {call_order}"


@pytest.mark.unit
def test_run_batch_erro_no_provider_deixa_job_failed(
    tmp_path: Path, batch_db: Session, batch_settings: Settings
) -> None:
    """Quando classify_image_batch falha, job fica em failed com erro batch_error."""
    local_images = _make_local_images(tmp_path, n=35)
    dropbox = _make_dropbox_mock(local_images)

    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="276800",
        status="pending",
        mode="batch",
    )
    batch_db.add(job)
    batch_db.commit()

    from app.services.llm_provider import FakeLLMProvider as _Fake

    class FailingProvider(_Fake):
        def classify_image_batch(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("api_overloaded")

    with patch("app.services.orchestrator._make_provider", return_value=FailingProvider()):
        orch = Orchestrator(db=batch_db, dropbox=dropbox, settings=batch_settings)
        orch.run_batch(job_id=job.id, checklist_id="276800")

    batch_db.refresh(job)
    assert job.status == "failed"
    assert job.error is not None
    assert "batch_error" in job.error
