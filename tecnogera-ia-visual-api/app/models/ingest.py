"""Estado da ingestão agendada de checklists — ticket ``mvp-c54-c57/07``.

Duas tabelas, ambas de controle (nenhum dado de negócio):

``ingest_cursors``
    Cursor do Dropbox por raiz varrida. É o que torna a varredura incremental:
    sem ele, cada rodada refaria a listagem completa de ``/Sisloc`` — medida em
    **67 minutos**, o que não cabe num cron de 30.

``checklist_ingest_state``
    Livro-razão por ``checklist_id``. Acumula os campos (``cNN``) já vistos, o
    formulário lido do Sisloc e o desfecho. A chave primária **é** o mecanismo
    de idempotência: duas rodadas sobrepostas não conseguem materializar dois
    jobs para o mesmo checklist.

Por que acumular campos: o delta do Dropbox entrega o que mudou *desde a última
rodada*. Um checklist cujas fotos caem em dois deltas apareceria incompleto nos
dois. O acumulador resolve isso sem nenhuma chamada extra ao Dropbox.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

#: Nome do cursor da raiz de checklists (chave em ``ingest_cursors``).
CURSOR_CHECKLISTS = "sisloc_checklists"

#: Estados de ``ChecklistIngestState.status``.
#:  ``pendente``      — visto, ainda sem as vistas obrigatórias (reavaliável)
#:  ``descartado``    — desfecho terminal (formulário fora da whitelist/vazio)
#:  ``materializado`` — virou ``pipeline_job``
STATUS_PENDENTE = "pendente"
STATUS_DESCARTADO = "descartado"
STATUS_MATERIALIZADO = "materializado"


class IngestCursor(Base):
    """Cursor de ``files_list_folder`` persistido entre rodadas do cron."""

    __tablename__ = "ingest_cursors"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ChecklistIngestState(Base):
    """Livro-razão de um ``checklist_id`` visto pela ingestão agendada."""

    __tablename__ = "checklist_ingest_state"

    checklist_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Campos observados, em CSV ordenado ("c54,c55,c56"). Texto simples de
    # propósito: cabe em qualquer dialeto e é legível num SELECT de suporte.
    campos: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # `varchar(30)` no Sisloc — a string vem truncada, casada por prefixo F0NN.
    formulario: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=STATUS_PENDENTE)
    motivo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    @property
    def campos_set(self) -> set[str]:
        return {c for c in self.campos.split(",") if c}
