"""Serviço de pareamento saída×retorno de eventos (IAVS-064).

Lógica:
  - Chave de par: (asset_code, pair_date) onde pair_date = date(captured_at)
  - Constraints UNIQUE por lado garantem idempotência total
  - reconcile_event: chamado inline pelo worker após classificação
  - reconcile_all: varredura completa dos eventos não-pareados
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.models.event import Event
from app.models.event_pair import EventPair

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_log = get_logger(__name__)


@dataclass
class ReconcileResult:
    pairs_created: int = 0
    pairs_completed: int = 0
    pairs_skipped: int = 0
    event_ids: list[uuid.UUID] = field(default_factory=list)


class PairingService:
    """Cria e completa pares saída×retorno de eventos de avaria."""

    def __init__(self, db: "Session") -> None:
        self._db = db

    # ── API pública ─────────────────────────────────────────────────────────

    def reconcile_event(self, event: Event) -> EventPair | None:
        """Cria ou completa o par para um evento recém-classificado. Idempotente.

        Retorna None se o evento não é elegível (status ≠ done, moment ausente,
        captured_at ausente).
        """
        if not self._is_eligible(event):
            return None

        pair_date = event.captured_at.date()  # type: ignore[union-attr]
        existing = self._find_pair(event.asset_code, pair_date)

        if existing is not None:
            changed = self._fill_side(existing, event)
            if changed:
                self._maybe_complete(existing)
                existing.updated_at = datetime.now(UTC)
                self._db.commit()
                _log.info(
                    "pair_updated",
                    pair_id=str(existing.id),
                    status=existing.status,
                    event_id=str(event.id),
                )
            return existing

        # Cria novo par parcial
        pair = EventPair(
            id=uuid.uuid4(),
            asset_code=event.asset_code,
            pair_date=pair_date,
            saida_event_id=event.id if event.moment == "saida" else None,
            retorno_event_id=event.id if event.moment == "retorno" else None,
            status="partial",
        )
        try:
            self._db.add(pair)
            self._db.flush()
        except IntegrityError:
            # Race condition: outro worker criou o par antes — re-busca e preenche
            self._db.rollback()
            existing = self._find_pair(event.asset_code, pair_date)
            if existing is None:
                _log.error("pair_race_condition_unfixable", event_id=str(event.id))
                return None
            self._fill_side(existing, event)
            self._maybe_complete(existing)
            existing.updated_at = datetime.now(UTC)
            self._db.commit()
            _log.info(
                "pair_race_resolved",
                pair_id=str(existing.id),
                event_id=str(event.id),
            )
            return existing

        self._db.commit()
        _log.info(
            "pair_created",
            pair_id=str(pair.id),
            asset_code=event.asset_code,
            pair_date=str(pair_date),
            moment=event.moment,
        )
        return pair

    def reconcile_all(self) -> ReconcileResult:
        """Reconcilia todos os eventos done não-pareados ainda.

        Útil para recuperação após falhas ou para execução periódica.
        """
        paired_saida = self._paired_ids("saida")
        paired_retorno = self._paired_ids("retorno")

        events = (
            self._db.query(Event)
            .filter(
                Event.status == "done",
                Event.moment.in_(["saida", "retorno"]),
                Event.captured_at.isnot(None),
            )
            .all()
        )

        result = ReconcileResult()
        for event in events:
            if event.moment == "saida" and event.id in paired_saida:
                result.pairs_skipped += 1
                continue
            if event.moment == "retorno" and event.id in paired_retorno:
                result.pairs_skipped += 1
                continue

            before_status = self._find_pair_status(event.asset_code, event.captured_at.date())  # type: ignore[union-attr]
            pair = self.reconcile_event(event)
            if pair is None:
                result.pairs_skipped += 1
                continue

            result.event_ids.append(event.id)
            if before_status is None:
                result.pairs_created += 1
            elif pair.status == "complete":
                result.pairs_completed += 1

        return result

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _is_eligible(event: Event) -> bool:
        return (
            event.status == "done"
            and event.moment in ("saida", "retorno")
            and event.captured_at is not None
        )

    def _find_pair(self, asset_code: str, pair_date: object) -> EventPair | None:
        return (
            self._db.query(EventPair)
            .filter(
                EventPair.asset_code == asset_code,
                EventPair.pair_date == pair_date,
            )
            .first()
        )

    def _find_pair_status(self, asset_code: str, pair_date: object) -> str | None:
        row = (
            self._db.query(EventPair.status)
            .filter(
                EventPair.asset_code == asset_code,
                EventPair.pair_date == pair_date,
            )
            .first()
        )
        return row[0] if row else None

    @staticmethod
    def _fill_side(pair: EventPair, event: Event) -> bool:
        """Preenche o lado correto se ainda vazio. Retorna True se houve mudança."""
        if event.moment == "saida" and pair.saida_event_id is None:
            pair.saida_event_id = event.id
            return True
        if event.moment == "retorno" and pair.retorno_event_id is None:
            pair.retorno_event_id = event.id
            return True
        return False

    @staticmethod
    def _maybe_complete(pair: EventPair) -> None:
        if pair.saida_event_id is not None and pair.retorno_event_id is not None:
            pair.status = "complete"

    def _paired_ids(self, moment: str) -> set[uuid.UUID]:
        col = EventPair.saida_event_id if moment == "saida" else EventPair.retorno_event_id
        return {
            row[0]
            for row in self._db.query(col).filter(col.isnot(None)).all()
        }
