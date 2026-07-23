"""Queries do portal admin — filtros, paginação, ETag, stats e resultado de job — IAVS-032/033/035."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func

from app.models.pipeline import PipelineJob

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class JobFilters:
    limit: int = 50
    offset: int = 0
    status_filter: list[str] = field(default_factory=list)
    date_from: datetime | None = None
    date_to: datetime | None = None


def list_jobs(db: Session, filters: JobFilters) -> tuple[list[PipelineJob], str]:
    """Retorna jobs filtrados e um ETag determinístico do resultset."""
    q = db.query(PipelineJob)

    if filters.status_filter:
        q = q.filter(PipelineJob.status.in_(filters.status_filter))
    if filters.date_from is not None:
        q = q.filter(PipelineJob.created_at >= filters.date_from)
    if filters.date_to is not None:
        q = q.filter(PipelineJob.created_at <= filters.date_to)

    total = q.count()
    max_updated: datetime | None = q.with_entities(func.max(PipelineJob.updated_at)).scalar()

    jobs = (
        q.order_by(PipelineJob.created_at.desc())
        .offset(filters.offset)
        .limit(filters.limit)
        .all()
    )

    etag = _compute_etag(max_updated, total)
    return jobs, etag


def _compute_etag(max_updated: datetime | None, count: int) -> str:
    raw = f"{max_updated.isoformat() if max_updated else 'none'}:{count}"
    return hashlib.md5(raw.encode()).hexdigest()  # noqa: S324  ETag, not security


# ── compute_stats ─────────────────────────────────────────────────────────────

_IN_PROGRESS_STATUSES = {"pending", "running", "pending_batch"}


@dataclass
class PortalStats:
    total_done: int
    in_progress: int
    failed: int
    total_cost_usd: float
    accuracy_last_week: float | None


def compute_stats(db: Session, month: str) -> PortalStats:
    """Retorna contadores e custo agregado do mês e accuracy da última semana."""
    year, mon = int(month[:4]), int(month[5:7])
    month_start = datetime(year, mon, 1, tzinfo=UTC)
    if mon == 12:
        month_end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        month_end = datetime(year, mon + 1, 1, tzinfo=UTC)

    jobs_in_month = (
        db.query(PipelineJob)
        .filter(PipelineJob.created_at >= month_start, PipelineJob.created_at < month_end)
        .all()
    )

    total_done = sum(1 for j in jobs_in_month if j.status == "done")
    in_progress = sum(1 for j in jobs_in_month if j.status in _IN_PROGRESS_STATUSES)
    failed = sum(1 for j in jobs_in_month if j.status == "failed")

    total_cost_usd = sum(
        (j.metrics or {}).get("estimated_cost_usd", 0.0)
        for j in jobs_in_month
        if j.status == "done"
    )

    week_ago = datetime.now(UTC) - timedelta(days=7)
    recent_done = (
        db.query(PipelineJob)
        .filter(PipelineJob.status == "done", PipelineJob.finished_at >= week_ago)
        .all()
    )
    accuracies = [
        (j.metrics or {}).get("eval", {}).get("accuracy_global")
        for j in recent_done
        if (j.metrics or {}).get("eval") is not None
    ]
    accuracies_clean = [a for a in accuracies if a is not None]
    accuracy_last_week = sum(accuracies_clean) / len(accuracies_clean) if accuracies_clean else None

    return PortalStats(
        total_done=total_done,
        in_progress=in_progress,
        failed=failed,
        total_cost_usd=total_cost_usd,
        accuracy_last_week=accuracy_last_week,
    )


# ── get_job_result ─────────────────────────────────────────────────────────────


@dataclass
class ClassificationItem:
    photo_id: str
    field_name: str | None
    confidence: float
    status: str  # valid | inconclusive | excluded
    label_display: str
    second_best_field: str | None = None
    second_best_confidence: float | None = None


@dataclass
class JobResult:
    job_id: str
    checklist_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    estimated_cost_usd: float | None
    result_pdf_path: str | None
    error: str | None
    classifications: list[ClassificationItem]
    inconclusivas: list[ClassificationItem]
    etag: str


def _classification_status(raw: dict[str, Any]) -> str:
    if raw.get("is_valid"):
        return "valid"
    if raw.get("requires_human_review"):
        return "inconclusive"
    return "excluded"


def _job_etag(job: PipelineJob) -> str:
    raw = f"{job.updated_at.isoformat() if job.updated_at else 'none'}:{job.status}"
    return hashlib.md5(raw.encode()).hexdigest()  # noqa: S324  ETag, not security


def get_job_result(db: Session, job_id: uuid.UUID) -> JobResult | None:
    """Retorna o resultado detalhado de um job, incluindo classificações expandidas."""
    job = db.get(PipelineJob, job_id)
    if job is None:
        return None

    raw_classifications: list[dict[str, Any]] = (job.metrics or {}).get("classifications", [])

    classifications: list[ClassificationItem] = []
    for raw in raw_classifications:
        item = ClassificationItem(
            photo_id=raw.get("image_filename", ""),
            field_name=raw.get("field_name"),
            confidence=raw.get("confidence", 0.0),
            status=_classification_status(raw),
            label_display=raw.get("field_name") or "",
            second_best_field=raw.get("second_best_field"),
            second_best_confidence=raw.get("second_best_confidence"),
        )
        classifications.append(item)

    inconclusivas = [c for c in classifications if c.status == "inconclusive"]

    return JobResult(
        job_id=str(job.id),
        checklist_id=job.checklist_id,
        status=job.status,
        started_at=job.started_at,
        finished_at=job.finished_at,
        estimated_cost_usd=(job.metrics or {}).get("estimated_cost_usd"),
        result_pdf_path=job.result_pdf_path,
        error=job.error,
        classifications=classifications,
        inconclusivas=inconclusivas,
        etag=_job_etag(job),
    )
