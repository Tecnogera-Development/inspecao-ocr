"""Classificador de avarias por Vision LLM (IAVS-063).

Responsabilidade única: orquestrar a chamada ao provider e mapear
DamageClassifyResult → colunas tipadas do Event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.llm_provider import DamageClassifyResult

_log = get_logger(__name__)

_SEVERITY_LABEL: dict[int, str] = {
    1: "critica",
    2: "alta",
    3: "media",
    4: "baixa",
}


@dataclass(frozen=True)
class DamageClassifierResult:
    """Resultado mapeado da classificação de avaria de um evento."""

    event_id: str
    raw: "DamageClassifyResult"
    classes_json: list[dict[str, Any]] = field(default_factory=list)

    # ── propriedades derivadas das colunas tipadas do Event ──────────────────

    @property
    def no_conformity(self) -> bool:
        return self.raw.no_conformity

    @property
    def damage_class(self) -> str | None:
        if not self.raw.no_conformity or not self.raw.classes:
            return None
        return max(self.raw.classes, key=lambda c: c.confidence).class_name

    @property
    def damage_confidence(self) -> float | None:
        if not self.raw.no_conformity or not self.raw.classes:
            return None
        return max(self.raw.classes, key=lambda c: c.confidence).confidence

    @property
    def damage_severity(self) -> str | None:
        if not self.raw.no_conformity or not self.raw.classes:
            return None
        most_critical = min(self.raw.classes, key=lambda c: c.severity)
        return _SEVERITY_LABEL.get(most_critical.severity)

    @property
    def angle_class(self) -> str:
        return self.raw.canonical_angle

    def to_event_columns(self) -> dict[str, Any]:
        return {
            "damage_class": self.damage_class,
            "damage_confidence": self.damage_confidence,
            "damage_severity": self.damage_severity,
            "angle_class": self.angle_class,
            "angle_confidence": None,
            "result_json": {
                "no_conformity": self.raw.no_conformity,
                "classes": [c.model_dump() for c in self.raw.classes],
                "canonical_angle": self.raw.canonical_angle,
                "model_version": self.raw.model_version,
            },
        }


class DamageClassifier:
    """Classifica avarias de um evento usando o LLMProvider configurado."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def classify(
        self,
        image_bytes: bytes,
        event_id: str,
        *,
        gabarito_bytes: bytes | None = None,
        saida_bytes: bytes | None = None,
        references: list[bytes] | None = None,
    ) -> DamageClassifierResult:
        raw = self._provider.classify_event(
            image_bytes,
            gabarito_bytes=gabarito_bytes,
            saida_bytes=saida_bytes,
            references=references,
        )
        _log.info(
            "damage_classified",
            event_id=event_id,
            no_conformity=raw.no_conformity,
            n_classes=len(raw.classes),
            canonical_angle=raw.canonical_angle,
        )
        return DamageClassifierResult(
            event_id=event_id,
            raw=raw,
            classes_json=[c.model_dump() for c in raw.classes],
        )
