"""Teste E2E do pipeline com FakeLLMProvider — offline, sem credenciais reais.

Dropbox é mockado; SQLite in-memory substitui Postgres.
Valida que o pipeline completo (download→classify→generate→render→upload)
funciona e persiste métricas no pipeline_jobs.
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
from app.services.classifier import Classifier
from app.services.orchestrator import Orchestrator
from app.services.shot_bank import ShotBank


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
def fake_images(tmp_path: Path) -> list[Path]:
    """Cria 3 imagens de fixture com nomes no formato Sisloc."""
    paths = []
    for campo in ["c0", "c3", "c6"]:
        p = tmp_path / f"153269005_checklist_276800_{campo}_0_10_04_2026 12_00_00.jpeg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # JPEG header fake
        paths.append(p)
    return paths


@pytest.fixture
def mock_dropbox(fake_images: list[Path]) -> MagicMock:
    dbx = MagicMock()

    local_images = []
    for p in fake_images:
        parsed = ParsedFilename(
            raw=p.name,
            checklist_id="276800",
            field_name=p.stem.split("_")[4],
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
        dropbox_path="/comparativo_de_imagem/276800_20260525_120000.pdf",
        shared_url=None,
        size_bytes=1024,
    )
    return dbx


@pytest.mark.e2e
def test_pipeline_e2e_completo(
    e2e_db: Session,
    mock_dropbox: MagicMock,
    e2e_settings: Settings,
    fake_images: list[Path],
    tmp_path: Path,
) -> None:
    job_id = uuid.uuid4()
    job = PipelineJob(id=job_id, checklist_id="276800", status="pending")
    e2e_db.add(job)
    e2e_db.commit()

    orch = Orchestrator(
        db=e2e_db,
        dropbox=mock_dropbox,
        settings=e2e_settings,
        work_dir=tmp_path,
    )

    with patch("weasyprint.HTML") as mock_html:
        mock_html.return_value.write_pdf.return_value = b"%PDF-fake"
        orch.run(job_id=job_id, checklist_id="276800")

    e2e_db.refresh(job)
    assert job.status == "done", f"esperado done, recebido {job.status!r} (error: {job.error})"
    assert job.result_pdf_path is not None
    assert job.metrics is not None
    assert "duration_total_ms" in job.metrics
    assert "classify_ms" in job.metrics
    assert "generate_ms" in job.metrics

    mock_dropbox.download_checklist_batch.assert_called_once_with(
        "276800", dest_dir=tmp_path / "276800"
    )
    mock_dropbox.upload_report.assert_called_once()


@pytest.mark.e2e
def test_pipeline_e2e_falha_dropbox_marca_job_failed(
    e2e_db: Session,
    e2e_settings: Settings,
    tmp_path: Path,
) -> None:
    job_id = uuid.uuid4()
    job = PipelineJob(id=job_id, checklist_id="276800", status="pending")
    e2e_db.add(job)
    e2e_db.commit()

    bad_dropbox = MagicMock()
    bad_dropbox.download_checklist_batch.side_effect = RuntimeError("Dropbox timeout")

    orch = Orchestrator(
        db=e2e_db,
        dropbox=bad_dropbox,
        settings=e2e_settings,
        work_dir=tmp_path,
    )
    orch.run(job_id=job_id, checklist_id="276800")

    e2e_db.refresh(job)
    assert job.status == "failed"
    assert "Dropbox timeout" in (job.error or "")


@pytest.mark.e2e
def test_pipeline_orchestrator_injeta_shot_bank_no_classifier(
    e2e_db: Session,
    mock_dropbox: MagicMock,
    e2e_settings: Settings,
    tmp_path: Path,
) -> None:
    """Regressão IAVS-009: Orchestrator deve injetar ShotBank no Classifier.

    Bug: orchestrator.py chamava classify_checklist(...) sem shot_bank=,
    fazendo o AnthropicProvider rodar sem few-shot em produção.
    """
    job_id = uuid.uuid4()
    job = PipelineJob(id=job_id, checklist_id="276800", status="pending")
    e2e_db.add(job)
    e2e_db.commit()

    fake_bank = MagicMock(spec=ShotBank)
    fake_bank.compute_hash.return_value = "fakehash123"
    fake_bank.select_shots.return_value = []

    received: dict[str, object] = {}
    real_classify = Classifier.classify_checklist

    def classify_spy(self, image_paths, profile_id="F013_liberacao_gerador", shot_bank=None):
        received["shot_bank"] = shot_bank
        return real_classify(self, image_paths, profile_id=profile_id, shot_bank=shot_bank)

    orch = Orchestrator(
        db=e2e_db, dropbox=mock_dropbox, settings=e2e_settings, work_dir=tmp_path
    )

    with (
        patch.object(Classifier, "classify_checklist", classify_spy),
        patch(
            "app.services.orchestrator.ShotBank.build_from_data_dir",
            return_value=fake_bank,
        ),
        patch("weasyprint.HTML") as mock_html,
    ):
        mock_html.return_value.write_pdf.return_value = b"%PDF-fake"
        orch.run(job_id=job_id, checklist_id="276800")

    assert received.get("shot_bank") is fake_bank, (
        f"Orchestrator não passou shot_bank ao Classifier; recebido: {received!r}"
    )
