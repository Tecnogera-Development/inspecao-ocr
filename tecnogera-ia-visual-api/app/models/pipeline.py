"""Modelos de pipeline_jobs — ORM SQLAlchemy + schemas Pydantic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

JobStatus = Literal["pending", "running", "done", "failed", "pending_batch"]
JobMode = Literal["sync", "batch"]


class PipelineJob(Base):
    """Registro de execução do pipeline E2E.

    Status válidos: pending, running, done, failed, pending_batch.
    Mode válidos: sync (default), batch.
    State machine batch: pending → running → pending_batch → running → done.
    """

    __tablename__ = "pipeline_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    checklist_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="sync")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    batch_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    batch_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Pydantic response schemas ──────────────────────────────────────────────

class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checklist_id: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result_pdf_path: str | None = None
    metrics: dict[str, Any] | None = None
