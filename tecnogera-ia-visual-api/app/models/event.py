"""Modelo ORM Event — unidade de avaria (1 imagem + metadados).

Coexiste com pipeline_jobs (checklist Sisloc) sem refatorar Orchestrator.
Colunas de classificação são tipadas (não blob JSON) para o eval do IAVS-066
agregar via GROUP BY. result_json mantém o payload completo do artefato (IAVS-065).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, DateTime, Float, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

EventStatus = Literal[
    "pending",
    "metadata_missing",
    "queued",
    "processing",
    "nao_processavel",
    "done",
    "failed",
]


class Event(Base):
    """Registro de um evento de avaria.

    Status machine:
      pending → queued (metadata ok, enfileirado ao ingest)
      pending → metadata_missing (campos obrigatórios ausentes no filename)
      queued  → processing → nao_processavel (falha de qualidade técnica)
      queued  → processing → done (validado, futuro: classificado em IAVS-063)
      *       → failed (erro inesperado no worker)
    """

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source_path", name="uq_events_source_path"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    canonical_angle: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    moment: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )  # "saida" | "retorno"
    uploaded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checklist_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )  # checklist Sisloc de origem (IAVS-068 — relatório)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)  # UNIQUE via constraint acima
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Colunas tipadas de classificação (IAVS-063) — para GROUP BY no eval
    damage_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    damage_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    damage_severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    angle_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    angle_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Validação (IAVS-061)
    validation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Ground truth para eval (IAVS-066) — preenchido via HITL/IAVS-059
    ground_truth_class: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Artefato completo (IAVS-065)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    annotated_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Pydantic response schemas ──────────────────────────────────────────────

class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_code: str
    canonical_angle: str | None = None
    captured_at: datetime | None = None
    moment: str | None = None
    uploaded_by: str | None = None
    source_path: str
    status: str
    created_at: datetime
    validation_reason: str | None = None
