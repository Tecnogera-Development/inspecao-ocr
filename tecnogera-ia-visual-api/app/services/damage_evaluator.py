"""Eval harness de avarias — IAVS-066.

Computa P/R/F1 por classe de dano a partir de eventos com ground truth anotado.
Não depende de numpy/sklearn — aritmética pura para zero dependências extras.

Uso típico:
    records = DamageEvaluator.records_from_db(db)
    report  = DamageEvaluator.evaluate(records)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_log = get_logger(__name__)

# Classe especial que representa "sem avaria"
_CONFORME = "conforme"
_ALL_CLASSES = ("ausencia_item", "fora_padrao_visual", "dano_visivel", _CONFORME)


# ── schemas de resultado ──────────────────────────────────────────────────────


class ClassMetrics(BaseModel):
    """P/R/F1 para uma classe de dano."""

    precision: float = Field(..., ge=0.0, le=1.0)
    recall: float = Field(..., ge=0.0, le=1.0)
    f1: float = Field(..., ge=0.0, le=1.0)
    support: int  # contagem de exemplos verdadeiros desta classe no GT


class DamageEvalReport(BaseModel):
    """Resultado de uma rodada de avaliação de avarias."""

    n_evaluated: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, ClassMetrics]
    per_moment: dict[str, float] = Field(default_factory=dict)   # accuracy por momento
    per_angle: dict[str, float] = Field(default_factory=dict)    # accuracy por ângulo
    confusion_matrix: list[dict[str, Any]] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── record de entrada ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DamageEvalRecord:
    """Par (predito, verdadeiro) para um evento anotado."""

    event_id: str
    predicted_class: str   # damage_class ou "conforme" se None
    true_class: str        # ground_truth_class
    moment: str | None = None
    angle: str | None = None


# ── evaluator ─────────────────────────────────────────────────────────────────


class DamageEvaluator:
    """Avaliador stateless de classificação de avarias."""

    @staticmethod
    def records_from_db(db: "Session") -> list[DamageEvalRecord]:
        """Carrega eventos anotados do banco para avaliação."""
        from app.models.event import Event  # noqa: PLC0415

        events = (
            db.query(Event)
            .filter(
                Event.ground_truth_class.isnot(None),
                Event.status == "done",
            )
            .all()
        )
        return [
            DamageEvalRecord(
                event_id=str(e.id),
                predicted_class=e.damage_class or _CONFORME,
                true_class=e.ground_truth_class,  # type: ignore[arg-type]
                moment=e.moment,
                angle=e.angle_class,
            )
            for e in events
        ]

    @staticmethod
    def evaluate(records: list[DamageEvalRecord]) -> DamageEvalReport:
        """Avalia lista de registros e retorna métricas."""
        if not records:
            return DamageEvalReport(
                n_evaluated=0,
                accuracy=0.0,
                macro_f1=0.0,
                per_class={},
            )

        classes = sorted(
            {r.true_class for r in records} | {r.predicted_class for r in records}
        )

        # Contadores por classe
        tp: dict[str, int] = defaultdict(int)
        fp: dict[str, int] = defaultdict(int)
        fn: dict[str, int] = defaultdict(int)
        support: dict[str, int] = defaultdict(int)

        correct = 0
        for r in records:
            support[r.true_class] += 1
            if r.predicted_class == r.true_class:
                tp[r.predicted_class] += 1
                correct += 1
            else:
                fp[r.predicted_class] += 1
                fn[r.true_class] += 1

        per_class: dict[str, ClassMetrics] = {}
        f1_sum = 0.0
        for cls in classes:
            p, r, f = _prf1(tp[cls], fp[cls], fn[cls])
            per_class[cls] = ClassMetrics(precision=p, recall=r, f1=f, support=support[cls])
            f1_sum += f

        macro_f1 = f1_sum / len(classes) if classes else 0.0
        accuracy = correct / len(records)

        per_moment = _accuracy_by_slice(records, "moment")
        per_angle = _accuracy_by_slice(records, "angle")
        confusion = _build_confusion_matrix(records, classes)

        _log.info(
            "damage_eval_done",
            n=len(records),
            accuracy=round(accuracy, 4),
            macro_f1=round(macro_f1, 4),
        )
        return DamageEvalReport(
            n_evaluated=len(records),
            accuracy=accuracy,
            macro_f1=macro_f1,
            per_class=per_class,
            per_moment=per_moment,
            per_angle=per_angle,
            confusion_matrix=confusion,
        )


# ── helpers ───────────────────────────────────────────────────────────────────


def _prf1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return precision, recall, f1


def _accuracy_by_slice(
    records: list[DamageEvalRecord],
    attr: str,
) -> dict[str, float]:
    buckets: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        val = getattr(r, attr)
        if val:
            buckets[val].append(r.predicted_class == r.true_class)
    return {
        k: sum(v) / len(v)
        for k, v in buckets.items()
        if v
    }


def _build_confusion_matrix(
    records: list[DamageEvalRecord],
    classes: list[str],
) -> list[dict[str, Any]]:
    """Retorna lista de {true, predicted, count} para serialização."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in records:
        counts[(r.true_class, r.predicted_class)] += 1
    return [
        {"true": t, "predicted": p, "count": c}
        for (t, p), c in sorted(counts.items())
    ]
