"""Schemas Pydantic v2 para o catálogo de checklists — IAVS-005."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldEntry(BaseModel):
    """Metadados de uma imagem de campo dentro de um checklist."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    dropbox_path: str
    filename: str
    size_bytes: int = Field(..., ge=0)
    captured_at: datetime | None = None
    resolution: tuple[int, int] | None = None
    extension: str


class ChecklistEntry(BaseModel):
    """Resultado da catalogação de um único checklist."""

    model_config = ConfigDict(frozen=True)

    checklist_id: str
    fields: list[FieldEntry] = Field(default_factory=list)
    error: str | None = None


class CatalogReport(BaseModel):
    """Relatório completo da exploração dos 9 checklists."""

    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    checklist_ids: list[str]
    entries: dict[str, Any]  # dict[str, ChecklistEntry | dict] para suportar erros serializados
