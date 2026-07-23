"""Modelo ORM EventPair — par saída×retorno de um mesmo ativo/dia (IAVS-064).

Constraints UNIQUE por lado garantem idempotência do job de reconciliação:
  - uq_event_pairs_saida    → cada evento de saída entra em no máximo 1 par
  - uq_event_pairs_retorno  → cada evento de retorno entra em no máximo 1 par
  - uq_event_pairs_asset_date → no máximo 1 par por ativo por dia
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

PairStatus = Literal["partial", "complete"]


class EventPair(Base):
    """Par de eventos saída×retorno para o mesmo ativo no mesmo dia.

    Status machine:
      partial  → um lado presente, o outro ausente
      complete → ambos saida_event_id e retorno_event_id preenchidos
    """

    __tablename__ = "event_pairs"
    __table_args__ = (
        UniqueConstraint("saida_event_id", name="uq_event_pairs_saida"),
        UniqueConstraint("retorno_event_id", name="uq_event_pairs_retorno"),
        UniqueConstraint("asset_code", "pair_date", name="uq_event_pairs_asset_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pair_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    saida_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("events.id"), nullable=True
    )
    retorno_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("events.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="partial", index=True
    )
    annotated_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── Pydantic response schemas ──────────────────────────────────────────────


class EventPairResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_code: str
    pair_date: date
    saida_event_id: uuid.UUID | None = None
    retorno_event_id: uuid.UUID | None = None
    status: str
    annotated_image_path: str | None = None
    created_at: datetime
    updated_at: datetime
