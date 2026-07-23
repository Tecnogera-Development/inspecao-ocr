"""Endpoints de eventos de avaria — IAVS-060 / IAVS-064 / IAVS-066.

POST /api/v1/events/ingest                    — varre Dropbox /Avarias e materializa Events
POST /api/v1/events/reconcile                 — reconcilia pares saída×retorno (IAVS-064)
GET  /api/v1/events/eval                      — eval P/R/F1 por classe (IAVS-066)
PATCH /api/v1/events/{event_id}/ground-truth  — anota ground truth (IAVS-066)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.services.damage_evaluator import DamageEvaluator
from app.services.dropbox import DropboxService
from app.services.event_ingestion import EventIngestionService
from app.services.pairing_service import PairingService

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/events", tags=["events"])
_log = get_logger(__name__)


def _verify_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.pipeline_api_key is None:
        return
    expected = settings.pipeline_api_key.get_secret_value()
    if x_api_key is None or x_api_key != expected:
        raise HTTPException(status_code=401, detail="X-API-Key inválida ou ausente")


class IngestResponse(BaseModel):
    created: int
    skipped: int
    metadata_missing: int
    queued: int


class ReconcileResponse(BaseModel):
    pairs_created: int
    pairs_completed: int
    pairs_skipped: int


@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: None = Depends(_verify_api_key),
) -> IngestResponse:
    """Varre /Avarias no Dropbox e materializa Events novos no banco.

    Eventos com metadados completos são enfileirados no Arq para processamento.
    Eventos com metadados incompletos ficam em status=metadata_missing.
    Eventos já existentes (source_path UNIQUE) são silenciosamente ignorados.
    """
    dropbox = DropboxService(settings)
    service = EventIngestionService(db=db, dropbox=dropbox, settings=settings)
    result = service.scan_and_ingest()

    pool = getattr(request.app.state, "arq_pool", None)
    if pool is not None and result.queued_ids:
        for event_id in result.queued_ids:
            await pool.enqueue_job("process_event", str(event_id))

    _log.info(
        "events_ingest_concluido",
        created=result.created,
        skipped=result.skipped,
        metadata_missing=result.metadata_missing,
        queued=result.queued,
    )
    return IngestResponse(
        created=result.created,
        skipped=result.skipped,
        metadata_missing=result.metadata_missing,
        queued=result.queued,
    )


@router.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_pairs(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_api_key),
) -> ReconcileResponse:
    """Reconcilia todos os eventos done não-pareados.

    Idempotente — pode ser chamado múltiplas vezes sem efeito duplicado.
    Útil para recuperação após falhas do worker ou reprocessamento manual.
    """
    result = PairingService(db).reconcile_all()
    _log.info(
        "reconcile_concluido",
        pairs_created=result.pairs_created,
        pairs_completed=result.pairs_completed,
        pairs_skipped=result.pairs_skipped,
    )
    return ReconcileResponse(
        pairs_created=result.pairs_created,
        pairs_completed=result.pairs_completed,
        pairs_skipped=result.pairs_skipped,
    )


@router.get("/eval")
async def eval_damage(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_api_key),
):
    """Computa P/R/F1 por classe usando eventos com ground_truth_class anotado.

    Retorna 422 se não há eventos anotados ainda.
    """
    records = DamageEvaluator.records_from_db(db)
    if not records:
        raise HTTPException(status_code=422, detail="Nenhum evento com ground truth anotado")
    report = DamageEvaluator.evaluate(records)
    _log.info("eval_solicitado", n_evaluated=report.n_evaluated)
    return report


class GroundTruthRequest(BaseModel):
    ground_truth_class: str


@router.patch("/{event_id}/ground-truth")
async def set_ground_truth(
    event_id: uuid.UUID,
    body: GroundTruthRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_api_key),
):
    """Define ground_truth_class em um evento para uso no eval harness (IAVS-066)."""
    from app.models.event import Event  # noqa: PLC0415

    _VALID = frozenset({"ausencia_item", "fora_padrao_visual", "dano_visivel", "conforme"})
    if body.ground_truth_class not in _VALID:
        raise HTTPException(
            status_code=422,
            detail=f"ground_truth_class deve ser um de: {sorted(_VALID)}",
        )

    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Evento não encontrado")

    event.ground_truth_class = body.ground_truth_class
    db.commit()
    _log.info(
        "ground_truth_set",
        event_id=str(event_id),
        ground_truth_class=body.ground_truth_class,
    )
    return {"event_id": str(event_id), "ground_truth_class": body.ground_truth_class}
