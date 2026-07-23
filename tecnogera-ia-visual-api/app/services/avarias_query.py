"""Camada de consulta para pares/eventos de avaria — IAVS-068.

Stateless: todas as funções recebem Session como parâmetro.
Sem lógica de negócio — apenas queries SQL via ORM.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import aliased

from app.models.event import Event
from app.models.event_pair import EventPair

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ── filtros ───────────────────────────────────────────────────────────────────


@dataclass
class PairListFilters:
    limit: int = 50
    offset: int = 0
    status: str | None = None
    asset_code: str | None = None
    date_from: date | None = None
    date_to: date | None = None


# ── DTOs de resultado ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PairSummaryRow:
    id: uuid.UUID
    asset_code: str
    pair_date: date
    status: str
    saida_event_id: uuid.UUID | None
    retorno_event_id: uuid.UUID | None
    annotated_image_path: str | None
    saida_damage_class: str | None
    saida_damage_severity: str | None
    retorno_damage_class: str | None
    retorno_damage_severity: str | None
    checklist_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class PairDetail:
    pair: EventPair
    saida_event: Event | None
    retorno_event: Event | None


# ── queries ───────────────────────────────────────────────────────────────────


def list_pairs(
    db: "Session",
    filters: PairListFilters,
) -> tuple[list[PairSummaryRow], int]:
    """Lista pares com colunas denormalizadas de dano por lado.

    Retorna (summaries, total) onde total é a contagem sem paginação.
    """
    SaidaEv = aliased(Event, name="saida_ev")
    RetornoEv = aliased(Event, name="retorno_ev")

    q = (
        db.query(
            EventPair,
            SaidaEv.damage_class.label("saida_damage_class"),
            SaidaEv.damage_severity.label("saida_damage_severity"),
            RetornoEv.damage_class.label("retorno_damage_class"),
            RetornoEv.damage_severity.label("retorno_damage_severity"),
            SaidaEv.checklist_id.label("saida_checklist_id"),
            RetornoEv.checklist_id.label("retorno_checklist_id"),
        )
        .outerjoin(SaidaEv, EventPair.saida_event_id == SaidaEv.id)
        .outerjoin(RetornoEv, EventPair.retorno_event_id == RetornoEv.id)
    )

    if filters.status:
        q = q.filter(EventPair.status == filters.status)
    if filters.asset_code:
        q = q.filter(EventPair.asset_code == filters.asset_code)
    if filters.date_from:
        q = q.filter(EventPair.pair_date >= filters.date_from)
    if filters.date_to:
        q = q.filter(EventPair.pair_date <= filters.date_to)

    total: int = q.count()
    rows = (
        q.order_by(EventPair.created_at.desc(), EventPair.asset_code)
        .offset(filters.offset)
        .limit(filters.limit)
        .all()
    )

    summaries = [
        PairSummaryRow(
            id=pair.id,
            asset_code=pair.asset_code,
            pair_date=pair.pair_date,
            status=pair.status,
            saida_event_id=pair.saida_event_id,
            retorno_event_id=pair.retorno_event_id,
            annotated_image_path=pair.annotated_image_path,
            saida_damage_class=saida_dc,
            saida_damage_severity=saida_ds,
            retorno_damage_class=retorno_dc,
            retorno_damage_severity=retorno_ds,
            checklist_id=saida_cid or retorno_cid,
            created_at=pair.created_at,
        )
        for pair, saida_dc, saida_ds, retorno_dc, retorno_ds, saida_cid, retorno_cid in rows
    ]
    return summaries, total


def get_pair_detail(db: "Session", pair_id: uuid.UUID) -> PairDetail | None:
    """Carrega par + ambos os eventos. Retorna None se não encontrado."""
    pair = db.get(EventPair, pair_id)
    if pair is None:
        return None

    saida = db.get(Event, pair.saida_event_id) if pair.saida_event_id else None
    retorno = db.get(Event, pair.retorno_event_id) if pair.retorno_event_id else None

    return PairDetail(pair=pair, saida_event=saida, retorno_event=retorno)
