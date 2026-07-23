"""Serviço de ingestão de eventos de avaria a partir do Dropbox.

Fluxo (sync, dentro do request de ingest):
  1. Lista paths sob dropbox_avarias_path
  2. Para cada path novo (source_path UNIQUE → dedup):
     a. parse_event_path → metadados
     b. Cria Event no banco
     c. Se has_complete_metadata: status="queued"
     d. Se não: status="metadata_missing"
  3. Retorna IngestResult para o router enfileirar os queued_ids no Arq
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
from app.core.logging import get_logger
from app.models.event import Event
from app.services.dropbox import DropboxService, parse_event_path

_log = get_logger(__name__)


@dataclass
class IngestResult:
    created: int = 0
    skipped: int = 0       # já no banco (dedup por source_path)
    metadata_missing: int = 0
    queued_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def queued(self) -> int:
        return len(self.queued_ids)


class EventIngestionService:
    """Varre Dropbox /Avarias e materializa novos Events no banco."""

    def __init__(
        self,
        db: Session,
        dropbox: DropboxService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._dropbox = dropbox or DropboxService(self._settings)

    def scan_and_ingest(self) -> IngestResult:
        """Varre Dropbox e persiste Events novos. Retorna resultado para o router."""
        avarias_root = self._settings.dropbox_avarias_path
        paths = self._dropbox.list_avarias_paths(avarias_root)
        result = IngestResult()

        for dropbox_path in paths:
            try:
                parsed = parse_event_path(dropbox_path, avarias_root=avarias_root)
            except ValueError:
                _log.warning("event_path_invalido", path=dropbox_path)
                continue

            event = self._upsert_event(parsed, dropbox_path)
            if event is None:
                result.skipped += 1
                continue

            result.created += 1
            if event.status == "queued":
                result.queued_ids.append(event.id)
            else:
                result.metadata_missing += 1

        return result

    def _upsert_event(self, parsed, dropbox_path: str) -> Event | None:
        """Insere Event se source_path ainda não existe; retorna None se já existe."""
        # Dedup: verifica antes de inserir (evita roundtrip de exceção no caso comum)
        existing = (
            self._db.query(Event)
            .filter(Event.source_path == dropbox_path)
            .first()
        )
        if existing is not None:
            return None

        status = "queued" if parsed.has_complete_metadata else "metadata_missing"
        event = Event(
            id=uuid.uuid4(),
            asset_code=parsed.asset_code,
            canonical_angle=parsed.canonical_angle,
            captured_at=parsed.captured_at,
            moment=parsed.moment,
            uploaded_by=parsed.uploaded_by,
            checklist_id=parsed.checklist_id,
            source_path=dropbox_path,
            status=status,
        )
        try:
            self._db.add(event)
            self._db.flush()  # detecta violação de UNIQUE antes do commit
        except IntegrityError:
            # Race condition entre dois requests simultâneos — trata como skip
            self._db.rollback()
            _log.warning("event_dedup_race", path=dropbox_path)
            return None

        self._db.commit()
        _log.info(
            "event_ingerido",
            event_id=str(event.id),
            asset_code=event.asset_code,
            status=status,
        )
        return event
