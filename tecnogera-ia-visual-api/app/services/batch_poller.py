"""BatchPoller — itera jobs pending_batch e retoma o pipeline quando o batch resolve.

Uso: instanciar com db + dropbox + provider e chamar poll_once() a cada tick de cron.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.pipeline import PipelineJob
from app.services.orchestrator import Orchestrator

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.services.dropbox import DropboxService

_log = get_logger(__name__)


class BatchPoller:
    """Consulta a Anthropic sobre batches pendentes e retoma o pipeline quando prontos."""

    def __init__(
        self,
        db: Session,
        dropbox: DropboxService,
        provider: Any,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._dropbox = dropbox
        self._provider = provider
        self._settings = settings or get_settings()
        self._orchestrator = Orchestrator(
            db=db, dropbox=dropbox, settings=self._settings
        )

    def poll_once(self) -> dict[str, int]:
        """Itera todos os jobs pending_batch e resolve os que o batch finalizou.

        Returns:
            stats: {n_polled, n_resolved, n_failed, n_skipped}
        """
        stats: dict[str, int] = {
            "n_polled": 0,
            "n_resolved": 0,
            "n_failed": 0,
            "n_skipped": 0,
        }

        jobs = (
            self._db.query(PipelineJob)
            .filter(PipelineJob.status == "pending_batch")
            .all()
        )

        for job in jobs:
            stats["n_polled"] += 1
            log = _log.bind(job_id=str(job.id), batch_id=job.batch_id)
            try:
                batch_status = self._provider.retrieve_batch(job.batch_id)
                if batch_status.processing_status != "ended":
                    continue

                raw_results = self._provider.get_batch_results(job.batch_id)

                # Idempotência: reconfirma status antes de continuar
                self._db.refresh(job)
                if job.status != "pending_batch":
                    log.info("batch_poller_skipped_already_resolved")
                    stats["n_skipped"] += 1
                    continue

                self._orchestrator.continue_after_batch(job.id, raw_results)
                stats["n_resolved"] += 1
                log.info("batch_poller_resolved")

            except Exception as exc:
                log.exception("batch_poller_error", error=str(exc))
                self._db.refresh(job)
                if job.status == "pending_batch":
                    job.status = "failed"
                    job.error = f"batch_error: {exc}"
                    job.finished_at = datetime.now(UTC)
                    self._db.commit()
                stats["n_failed"] += 1

        _log.info("batch_poller_done", **stats)
        return stats
