"""Arq task: processamento de um Evento de avaria (validate → classify → artifact).

Cada tarefa abre sua própria Session via get_session_factory() e fecha no finally.
Nunca herda Session de request.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

_log = get_logger(__name__)


async def scheduled_ingest(ctx: dict[str, Any]) -> dict[str, int]:
    """Cron: varre o Dropbox /Avarias e enfileira eventos novos (ingestão automática).

    Idempotente — eventos já existentes são ignorados por source_path. Roda a cada
    poucos minutos para que o operador não precise disparar o ingest na mão.
    """
    from app.db.session import get_session_factory
    from app.services.dropbox import DropboxService
    from app.services.event_ingestion import EventIngestionService

    settings = get_settings()
    db = get_session_factory()()
    try:
        service = EventIngestionService(
            db=db, dropbox=DropboxService(settings), settings=settings
        )
        result = service.scan_and_ingest()
        pool = ctx.get("redis")
        if pool is not None:
            for event_id in result.queued_ids:
                await pool.enqueue_job("process_event", str(event_id))
        if result.created:
            _log.info(
                "scheduled_ingest",
                created=result.created,
                queued=result.queued,
                metadata_missing=result.metadata_missing,
            )
        return {"created": result.created, "queued": result.queued}
    finally:
        db.close()


# Quantas fotos de entrega enviar como candidatas de referência. O modelo escolhe
# a correspondente (matched_reference_index). Cacheadas → repetições do mesmo
# checklist são baratas. Amostradas uniformemente para diversidade de ângulos.
_BASELINE_MAX_REFS = 6


def _fetch_references(dropbox: object, checklist_id: str | None) -> list[tuple[str, bytes]]:
    """Puxa fotos de entrega candidatas (via checklist_id) como referências.

    Retorna lista de (dropbox_path, bytes), amostrada uniformemente até
    _BASELINE_MAX_REFS. Vazia se não houver checklist_id/fotos ou em falha.
    """
    if not checklist_id:
        return []
    try:
        imagens = dropbox.list_checklist_images(checklist_id)  # type: ignore[attr-defined]
        if not imagens:
            _log.info("baseline_sem_fotos", checklist_id=checklist_id)
            return []
        n = len(imagens)
        if n <= _BASELINE_MAX_REFS:
            escolhidas = list(imagens)
        else:
            step = n / _BASELINE_MAX_REFS
            escolhidas = [imagens[int(i * step)] for i in range(_BASELINE_MAX_REFS)]
        out: list[tuple[str, bytes]] = []
        for meta in escolhidas:
            try:
                out.append((meta.dropbox_path, dropbox.download_image(meta.dropbox_path)))  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                _log.warning("baseline_ref_download_falhou", path=meta.dropbox_path, error=str(exc))
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("baseline_fetch_falhou", checklist_id=checklist_id, error=str(exc))
        return []


def _get_llm_provider(settings: Settings) -> object:
    """Provider LLM das avarias — delega ao ponto único.

    A escolha (OpenAI → Anthropic → Fake) mora em
    ``app.services.llm_provider.get_llm_provider``, junto do
    ``Settings.llm_provider_efetivo`` que o guarda-corpo de produção consulta.
    Este alias sobrevive porque o fluxo de avarias e os testes o referenciam.
    """
    from app.services.llm_provider import get_llm_provider  # noqa: PLC0415

    return get_llm_provider(settings)


async def process_event(ctx: dict[str, Any], event_id: str) -> None:
    """Processa um Evento: valida tecnicamente e classifica avaria (IAVS-061 + IAVS-063).

    Etapas:
      1. Carrega Event do banco
      2. Baixa imagem do Dropbox
      3. Valida tecnicamente (formato, resolução, foco)
      4. Se inválido: status="nao_processavel" + razão
      5. Se válido: classifica avaria via Vision LLM
      6. Persiste colunas tipadas + status="done"
    """
    from app.db.session import get_session_factory
    from app.models.event import Event
    from app.services.artifact_service import ArtifactService
    from app.services.damage_classifier import DamageClassifier
    from app.services.dropbox import DropboxService
    from app.services.event_validation import EventValidationService
    from app.services.pairing_service import PairingService

    settings = get_settings()
    db = get_session_factory()()
    try:
        eid = uuid.UUID(event_id)
        event = db.get(Event, eid)
        if event is None:
            _log.warning("process_event_not_found", event_id=event_id)
            return

        event.status = "processing"
        db.commit()

        dropbox = DropboxService(settings)
        image_bytes = dropbox.download_image(event.source_path)

        validator = EventValidationService()
        result = validator.validate_technical(image_bytes)

        if not result.processable:
            event.status = "nao_processavel"
            event.validation_reason = result.reason.value if result.reason else None
            db.commit()
            _log.info(
                "event_nao_processavel",
                event_id=event_id,
                reason=event.validation_reason,
            )
            return

        # Base de comparação: fotos do checklist de entrega (Sisloc), puxadas
        # pelo checklist_id. O modelo escolhe a que mostra o mesmo ângulo/parte.
        references = _fetch_references(dropbox, event.checklist_id)
        ref_bytes = [b for _, b in references]

        # IAVS-063: classificação de avaria via Vision LLM
        llm_provider = _get_llm_provider(settings)
        classifier = DamageClassifier(llm_provider)
        classification = classifier.classify(
            image_bytes, event_id=event_id, references=ref_bytes
        )

        cols = classification.to_event_columns()
        event.damage_class = cols["damage_class"]
        event.damage_confidence = cols["damage_confidence"]
        event.damage_severity = cols["damage_severity"]
        event.angle_class = cols["angle_class"]
        event.angle_confidence = cols["angle_confidence"]

        # Foto de entrega que o modelo usou como base (a correspondente ao ângulo).
        if references:
            idx = classification.raw.matched_reference_index
            baseline_path = (
                references[idx][0]
                if idx is not None and 0 <= idx < len(references)
                else references[0][0]
            )
            cols["result_json"]["baseline_source_path"] = baseline_path
        event.result_json = cols["result_json"]

        event.status = "done"
        event.validation_reason = None
        db.commit()
        _log.info(
            "event_classified",
            event_id=event_id,
            no_conformity=classification.no_conformity,
            damage_class=event.damage_class,
            angle_class=event.angle_class,
        )

        # IAVS-064: pareamento inline após classificação
        pair = PairingService(db).reconcile_event(event)

        # IAVS-065: gerar composto quando o par ficar completo
        if pair is not None and pair.status == "complete":
            try:
                ArtifactService(db, dropbox, settings).generate_composite(pair)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "composite_generation_failed",
                    pair_id=str(pair.id),
                    error=str(exc),
                )

    except Exception as exc:
        _log.error("process_event_error", event_id=event_id, error=str(exc))
        try:
            from app.models.event import Event as _Event  # noqa: F401 (re-import safe)

            event_row = db.get(Event, uuid.UUID(event_id))
            if event_row is not None and event_row.status not in ("nao_processavel", "done"):
                event_row.status = "failed"
                event_row.validation_reason = "worker_error"
                db.commit()
        except Exception:  # noqa: BLE001,S110
            _log.warning("process_event_cleanup_failed", event_id=event_id)
        raise
    finally:
        db.close()
