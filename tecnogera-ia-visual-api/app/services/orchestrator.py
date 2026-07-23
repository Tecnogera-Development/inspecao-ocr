"""Orchestrator do pipeline E2E — IAVS-001.

Sequência: download → classify → generate → render → upload
Estado persistido em pipeline_jobs a cada etapa.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.pipeline import PipelineJob
from app.services.cost_calculator import compute_cost
from app.services.classifier import Classifier, SUPPORTED_PROFILES
from app.services.dropbox import parse_filename
from app.services.equipment_profiles import EquipmentProfileService
from app.services.llm_provider import (
    AnthropicProvider,
    ClassificationResult,
    FakeLLMProvider,
    _CONFIDENCE_THRESHOLD,
    _INCONCLUSIVE_FLOOR,
)
from app.services.evaluator import Evaluator
from app.services.pdf_renderer import PdfRendererService
from app.services.report_generator import ReportGenerator
from app.services.shot_bank import ShotBank

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from app.services.dropbox import DropboxService

_log = get_logger(__name__)


_profile_svc = EquipmentProfileService()


def _detect_profile_id(image_paths: list[Path]) -> str:
    """Detecta profile_id por overlap de campos com F013 (único perfil populado).

    Se ≥50% dos campos das imagens estão no catálogo F013 → F013.
    Caso contrário → _unknown_fallback.
    """
    image_fields: set[str] = set()
    for path in image_paths[:20]:
        try:
            parsed = parse_filename(path.name)
            image_fields.add(parsed.field_name)
        except (ValueError, AttributeError):
            pass

    if not image_fields:
        return "_unknown_fallback"

    try:
        f013_fields = {
            c.field_name
            for c in _profile_svc.get_profile("F013_liberacao_gerador").campos
        }
    except Exception:
        return "_unknown_fallback"

    overlap = len(image_fields & f013_fields) / len(image_fields)
    return "F013_liberacao_gerador" if overlap >= 0.5 else "_unknown_fallback"


def _make_provider(settings: Settings) -> FakeLLMProvider | AnthropicProvider:
    mode = settings.llm_provider
    if mode == "fake":
        return FakeLLMProvider(mode="filename_oracle")
    if mode == "anthropic":
        if settings.anthropic_api_key is None:
            raise ValueError(
                "ANTHROPIC_API_KEY não configurada; defina no .env para usar LLM_PROVIDER=anthropic"
            )
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.anthropic_model,
            report_model=settings.report_model,
        )
    raise ValueError(
        f"LLM_PROVIDER='{mode}' não suportado; use 'fake' ou 'anthropic'"
    )


def _parse_batch_results(
    raw_results: list[Any],
    model_version: str,
) -> list[ClassificationResult]:
    """Converte resultados brutos da Batch API em ClassificationResult."""
    out: list[ClassificationResult] = []
    for item in raw_results:
        result = item.result
        if getattr(result, "type", None) != "succeeded":
            continue
        message = result.message
        tool_block = next(
            (b for b in message.content if getattr(b, "type", None) == "tool_use"),
            None,
        )
        if tool_block is None:
            continue
        raw: dict[str, Any] = tool_block.input
        confidence = float(raw["confidence"])
        is_valid = confidence >= _CONFIDENCE_THRESHOLD
        requires_review = _INCONCLUSIVE_FLOOR <= confidence < _CONFIDENCE_THRESHOLD
        out.append(
            ClassificationResult(
                image_filename=item.custom_id,
                field_name=raw.get("field_name"),
                confidence=confidence,
                is_valid=is_valid,
                observation=raw.get("observation", ""),
                detected_issues=raw.get("detected_issues", []),
                requires_human_review=requires_review,
                model_version=model_version,
                shot_bank_hash="",
            )
        )
    return out


class Orchestrator:
    """Executa o pipeline de inspeção e persiste estado no banco."""

    def __init__(
        self,
        db: Session,
        dropbox: DropboxService,
        *,
        settings: Settings | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self._db = db
        self._dropbox = dropbox
        self._settings = settings or get_settings()
        self._work_dir = work_dir or Path(self._settings.dropbox_local_cache_dir)

    def run(self, job_id: uuid.UUID, checklist_id: str) -> None:
        """Executa pipeline completo para um checklist. Atualiza job no DB."""
        job = self._db.get(PipelineJob, job_id)
        if job is None:
            _log.error("orchestrator_job_not_found", job_id=str(job_id))
            return

        log = _log.bind(job_id=str(job_id), checklist_id=checklist_id)
        metrics: dict[str, Any] = {}

        try:
            job.status = "running"
            job.started_at = datetime.now(UTC)
            self._db.commit()

            # 1. Download
            t0 = time.monotonic()
            dest_dir = self._work_dir / checklist_id
            local_images = self._dropbox.download_checklist_batch(
                checklist_id, dest_dir=dest_dir
            )
            metrics["download_ms"] = int((time.monotonic() - t0) * 1000)
            log.info("orchestrator_download_done", count=len(local_images))

            # 2. Classify
            t0 = time.monotonic()
            image_paths = [img.local_path for img in local_images]
            profile_id = _detect_profile_id(image_paths)
            metrics["profile_id"] = profile_id

            if profile_id in SUPPORTED_PROFILES:
                field_names = [
                    c.field_name
                    for c in _profile_svc.get_profile(profile_id).campos
                ]
            else:
                field_names = []

            provider = _make_provider(self._settings)
            classifier = Classifier(provider, field_names=field_names)
            shot_bank = (
                ShotBank.build_from_data_dir(profile_id, Path("data/checklists"))
                if profile_id in SUPPORTED_PROFILES
                else None
            )
            metrics["shot_bank_hash"] = shot_bank.compute_hash() if shot_bank else ""
            classifications = classifier.classify_checklist(
                image_paths, profile_id=profile_id, shot_bank=shot_bank
            )
            metrics["classify_ms"] = int((time.monotonic() - t0) * 1000)
            log.info("orchestrator_classify_done", count=len(classifications), profile_id=profile_id)

            # 3. Evaluate
            t0 = time.monotonic()
            eval_dir = Path("data/eval")
            partition_path = Path("data/eval/partition.json")
            eval_report = Evaluator.evaluate(
                classifications,
                partition_path=partition_path if partition_path.exists() else None,
            )
            Evaluator.save(eval_report, output_dir=eval_dir)
            metrics["eval_ms"] = int((time.monotonic() - t0) * 1000)
            metrics["eval"] = eval_report.model_dump()
            log.info(
                "orchestrator_eval_done",
                accuracy=eval_report.accuracy_global,
                n_evaluated=eval_report.n_evaluated,
            )

            # 4. Generate report
            t0 = time.monotonic()
            generator = ReportGenerator(provider)
            checklist_meta = {
                "checklist_id": checklist_id,
                "data": datetime.now(UTC).strftime("%d/%m/%Y"),
                "total_obrigatorios": len(classifications),
            }
            markdown = generator.generate(classifications, checklist_meta)
            metrics["generate_ms"] = int((time.monotonic() - t0) * 1000)

            # 4. Render PDF
            t0 = time.monotonic()
            renderer = PdfRendererService(self._settings)
            pdf_bytes = renderer.render(markdown, title=f"Checklist {checklist_id}")
            metrics["render_ms"] = int((time.monotonic() - t0) * 1000)

            # 5. Upload
            t0 = time.monotonic()
            report = self._dropbox.upload_report(
                checklist_id, pdf_bytes, captured_at=datetime.now(UTC)
            )
            metrics["upload_ms"] = int((time.monotonic() - t0) * 1000)
            log.info("orchestrator_upload_done", path=report.dropbox_path)

            job.status = "done"
            job.result_pdf_path = report.dropbox_path
            job.finished_at = datetime.now(UTC)
            metrics["duration_total_ms"] = sum(v for v in metrics.values() if isinstance(v, int))
            usage = getattr(provider, "accumulated_usage", None)
            if usage is not None:
                metrics["estimated_cost_usd"] = compute_cost(
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                    batch_mode=(job.mode == "batch"),
                )
            metrics["classifications"] = [c.model_dump(mode="json") for c in classifications]
            job.metrics = metrics
            self._db.commit()
            log.info("orchestrator_pipeline_done")

        except Exception as exc:
            _log.exception("orchestrator_pipeline_failed", error=str(exc))
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.now(UTC)
            job.metrics = metrics
            self._db.commit()

    def run_batch(self, job_id: uuid.UUID, checklist_id: str) -> None:
        """Submete classificações via Batch API. Job fica em pending_batch até o poller resolver."""
        job = self._db.get(PipelineJob, job_id)
        if job is None:
            _log.error("orchestrator_job_not_found", job_id=str(job_id))
            return

        log = _log.bind(job_id=str(job_id), checklist_id=checklist_id)
        metrics: dict[str, Any] = {}

        try:
            job.status = "running"
            job.started_at = datetime.now(UTC)
            self._db.commit()

            # 1. Download
            dest_dir = self._work_dir / checklist_id
            local_images = self._dropbox.download_checklist_batch(
                checklist_id, dest_dir=dest_dir
            )
            log.info("orchestrator_download_done", count=len(local_images))

            # 2. Profile detection + field names
            image_paths = [img.local_path for img in local_images]
            profile_id = _detect_profile_id(image_paths)
            metrics["profile_id"] = profile_id

            if profile_id in SUPPORTED_PROFILES:
                field_names = [
                    c.field_name
                    for c in _profile_svc.get_profile(profile_id).campos
                ]
            else:
                field_names = []

            provider = _make_provider(self._settings)

            # 3. Prewarm: classifica a primeira imagem de forma síncrona para aquecer o cache
            first = local_images[0]
            first_bytes = first.local_path.read_bytes()
            _ = provider.classify_image(
                first.metadata.filename, first_bytes, field_names
            )
            log.info("orchestrator_batch_prewarm_done", prewarm_image=first.metadata.filename)

            # 4. Batch: submete as demais imagens
            remaining = [
                (img.metadata.filename, img.local_path.read_bytes())
                for img in local_images[1:]
            ]
            try:
                batch_id = provider.classify_image_batch(remaining, field_names)
            except Exception as exc:
                job.status = "failed"
                job.error = f"batch_error: {exc}"
                job.finished_at = datetime.now(UTC)
                job.metrics = metrics
                self._db.commit()
                log.error("orchestrator_batch_submit_failed", error=str(exc))
                return

            # 5. Persiste estado pending_batch
            job.status = "pending_batch"
            job.batch_id = batch_id
            job.batch_submitted_at = datetime.now(UTC)
            job.metrics = metrics
            self._db.commit()

            log.info(
                "pipeline_batch_submitted",
                batch_id=batch_id,
                n_images=len(remaining),
                prewarm_image=first.metadata.filename,
            )

        except Exception as exc:
            _log.exception("orchestrator_batch_failed", error=str(exc))
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.now(UTC)
            job.metrics = metrics
            self._db.commit()

    def continue_after_batch(
        self,
        job_id: uuid.UUID,
        raw_results: list[Any],
    ) -> None:
        """Retoma o pipeline após o batch resolver: gera relatório + PDF + upload."""
        job = self._db.get(PipelineJob, job_id)
        if job is None:
            _log.error("orchestrator_job_not_found", job_id=str(job_id))
            return
        if job.status != "pending_batch":
            return  # idempotente

        log = _log.bind(job_id=str(job_id), checklist_id=job.checklist_id)
        metrics: dict[str, Any] = dict(job.metrics or {})

        try:
            job.status = "running"
            job.batch_resolved_at = datetime.now(UTC)
            self._db.commit()

            # 1. Parsear classificações do batch
            model_version = self._settings.anthropic_model
            classifications = _parse_batch_results(raw_results, model_version)
            log.info("orchestrator_batch_resolved", n_classifications=len(classifications))

            # 2. Evaluate
            eval_dir = Path("data/eval")
            partition_path = Path("data/eval/partition.json")
            eval_report = Evaluator.evaluate(
                classifications,
                partition_path=partition_path if partition_path.exists() else None,
            )
            Evaluator.save(eval_report, output_dir=eval_dir)
            metrics["eval"] = eval_report.model_dump()

            # 3. Generate report
            provider = _make_provider(self._settings)
            generator = ReportGenerator(provider)
            checklist_meta = {
                "checklist_id": job.checklist_id,
                "data": datetime.now(UTC).strftime("%d/%m/%Y"),
                "total_obrigatorios": len(classifications),
            }
            markdown = generator.generate(classifications, checklist_meta)

            # 4. Render PDF
            renderer = PdfRendererService(self._settings)
            pdf_bytes = renderer.render(markdown, title=f"Checklist {job.checklist_id}")

            # 5. Upload
            report = self._dropbox.upload_report(
                job.checklist_id, pdf_bytes, captured_at=datetime.now(UTC)
            )
            log.info("orchestrator_upload_done", path=report.dropbox_path)

            job.status = "done"
            job.result_pdf_path = report.dropbox_path
            job.finished_at = datetime.now(UTC)
            usage = getattr(provider, "accumulated_usage", None)
            if usage is not None:
                metrics["estimated_cost_usd"] = compute_cost(
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                    batch_mode=(job.mode == "batch"),
                )
            metrics["classifications"] = [c.model_dump(mode="json") for c in classifications]
            job.metrics = metrics
            self._db.commit()
            log.info("orchestrator_batch_pipeline_done")

        except Exception as exc:
            _log.exception("orchestrator_continue_batch_failed", error=str(exc))
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.now(UTC)
            job.metrics = metrics
            self._db.commit()
