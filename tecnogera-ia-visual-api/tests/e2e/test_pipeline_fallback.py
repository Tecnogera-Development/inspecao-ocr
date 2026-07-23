"""Teste E2E do pipeline para checklist com formulário desconhecido (IAVS-005).

Usa checklist 278724 (F180) com FakeLLMProvider — Dropbox mockado, SQLite in-memory.
Valida que o pipeline completa sem erro e classifica via _unknown_fallback.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.models.dropbox import ImageMetadata, LocalImage, ParsedFilename, UploadedReport
from app.models.pipeline import PipelineJob
from app.services.orchestrator import Orchestrator


@pytest.fixture
def e2e_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        llm_provider="fake",
    )


@pytest.fixture
def e2e_engine():
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
def e2e_db(e2e_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=e2e_engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def f180_images(tmp_path: Path) -> list[Path]:
    """Cria imagens sintéticas com nomes F180 (checklist 278724, campos NÃO-F013)."""
    paths = []
    # Campos exclusivos F180 que não aparecem em F013
    for campo in ["c1", "c15", "c31", "c45", "c51"]:
        p = tmp_path / f"153664205_checklist_278724_{campo}_0_15_04_2026 14_00_00.jpeg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        paths.append(p)
    return paths


@pytest.fixture
def mock_dropbox_f180(f180_images: list[Path]) -> MagicMock:
    dbx = MagicMock()

    local_images = []
    for p in f180_images:
        field = p.stem.split("_")[4]
        parsed = ParsedFilename(
            raw=p.name,
            checklist_id="278724",
            field_name=field,
            extension=".jpeg",
        )
        meta = ImageMetadata(
            dropbox_path=f"/Sisloc/{p.name}",
            filename=p.name,
            size_bytes=p.stat().st_size,
            parsed=parsed,
        )
        local_images.append(LocalImage(metadata=meta, local_path=p))

    dbx.download_checklist_batch.return_value = local_images
    dbx.upload_report.return_value = UploadedReport(
        dropbox_path="/comparativo_de_imagem/278724_20260525_120000.pdf",
        shared_url=None,
        size_bytes=1024,
    )
    return dbx


@pytest.mark.e2e
def test_pipeline_e2e_checklist_278724_fallback_completa(
    e2e_db: Session,
    mock_dropbox_f180: MagicMock,
    e2e_settings: Settings,
    tmp_path: Path,
) -> None:
    """Pipeline E2E para 278724 (F180): completa sem erro usando _unknown_fallback.

    Verifica que o Orchestrator detecta o perfil como não-F013 e usa fallback —
    evidenciado por profile_id='_unknown_fallback' em job.metrics.
    """
    job_id = uuid.uuid4()
    job = PipelineJob(id=job_id, checklist_id="278724", status="pending")
    e2e_db.add(job)
    e2e_db.commit()

    orch = Orchestrator(
        db=e2e_db,
        dropbox=mock_dropbox_f180,
        settings=e2e_settings,
        work_dir=tmp_path,
    )

    with patch("weasyprint.HTML") as mock_html:
        mock_html.return_value.write_pdf.return_value = b"%PDF-fake"
        orch.run(job_id=job_id, checklist_id="278724")

    e2e_db.refresh(job)
    assert job.status == "done", f"esperado done, recebido {job.status!r} (error: {job.error})"
    assert job.result_pdf_path is not None
    assert job.metrics is not None
    assert job.metrics.get("profile_id") == "_unknown_fallback", (
        f"Esperado profile_id=_unknown_fallback em metrics, obtido: {job.metrics}"
    )
