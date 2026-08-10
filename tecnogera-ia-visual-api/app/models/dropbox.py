"""Schemas Pydantic para o domínio de imagens vindas do Dropbox."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ParsedEventFilename(BaseModel):
    """Representação tipada do path de um arquivo de avaria no Dropbox.

    Convenção: /Avarias/{asset_code}/{YYYYMMDD}_{HHMMSS}_{moment}_{angle}_{uploaded_by}.ext
    Ver docs/avarias/ingestao-dropbox.md.
    """

    model_config = ConfigDict(frozen=True)

    raw: str  # Dropbox path completo
    asset_code: str  # pasta diretamente sob /Avarias/
    captured_at: datetime | None = None
    moment: str | None = None  # "saida" | "retorno"
    canonical_angle: str | None = None
    uploaded_by: str | None = None
    checklist_id: str | None = None  # checklist Sisloc de origem (opcional)
    extension: str

    @property
    def has_complete_metadata(self) -> bool:
        return all([
            self.asset_code,
            self.captured_at is not None,
            self.moment,
            self.canonical_angle,
            self.uploaded_by,
        ])


class ParsedFilename(BaseModel):
    """Representação tipada da nomenclatura ``[id]_[campo]_[data]_[hora].ext``.

    Campos derivados do nome do arquivo no Dropbox. ``raw`` preserva o nome
    original para reconciliação posterior.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    checklist_id: str
    field_name: str
    captured_at: datetime | None = None
    extension: str


class ImageMetadata(BaseModel):
    """Metadados de uma imagem listada no Dropbox (sem o conteúdo binário)."""

    model_config = ConfigDict(frozen=True)

    dropbox_path: str = Field(..., description="Caminho completo no Dropbox.")
    filename: str
    size_bytes: int = Field(..., ge=0)
    parsed: ParsedFilename
    # Data que o Dropbox atribui ao arquivo. É a base do marco de corte da
    # ingestão agendada — `parsed.captured_at` vem do nome e pode faltar.
    server_modified: datetime | None = None


class DropboxDelta(BaseModel):
    """Resultado de uma leitura incremental (``files_list_folder_continue``).

    ``reset=True`` significa que o Dropbox invalidou o cursor e a listagem
    precisa recomeçar — ver ``DropboxService.list_checklist_delta``.
    """

    model_config = ConfigDict(frozen=True)

    cursor: str
    images: list[ImageMetadata] = Field(default_factory=list)
    reset: bool = False
    has_more: bool = False
    ignorados: int = 0


class LocalImage(BaseModel):
    """Imagem baixada para o disco local, com referência ao Dropbox de origem."""

    model_config = ConfigDict(frozen=True)

    metadata: ImageMetadata
    local_path: Path


class UploadedReport(BaseModel):
    """Resultado da publicação de um relatório PDF no Dropbox."""

    model_config = ConfigDict(frozen=True)

    dropbox_path: str
    shared_url: str | None = None
    size_bytes: int = Field(..., ge=0)
