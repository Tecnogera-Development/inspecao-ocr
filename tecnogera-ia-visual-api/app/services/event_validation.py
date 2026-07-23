"""Gate de validação de evento — IAVS-061.

Divide-se em dois métodos conforme as notas de execução do issue:

  validate_metadata(parsed) → sync, sem baixar imagem, chamado no ingest
  validate_technical(image_bytes) → async no worker, usa bytes já baixados

Limiar de nitidez (LAPLACIAN_VARIANCE_THRESHOLD) será calibrado com o
dataset semente do IAVS-059. O valor padrão de 100.0 é conservador e pode
ser ajustado via subclasse ou parâmetro.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.dropbox import ParsedEventFilename


class ValidationReason(str, Enum):
    FOCO_INADEQUADO = "foco_inadequado"
    RESOLUCAO_BAIXA = "resolucao_baixa"
    FORMATO_INVALIDO = "formato_invalido"
    METADADOS_AUSENTES = "metadados_ausentes"


@dataclass(frozen=True)
class ValidationResult:
    processable: bool
    reason: ValidationReason | None = None


class EventValidationService:
    """Valida qualidade técnica e completude de metadados de um Evento."""

    MIN_WIDTH: int = 1280
    MIN_HEIGHT: int = 720
    LAPLACIAN_VARIANCE_THRESHOLD: float = 100.0  # calibrar em IAVS-059

    def validate_metadata(self, parsed: ParsedEventFilename) -> ValidationResult:
        """Checa presença dos metadados obrigatórios — sync, sem tocar na imagem."""
        if not parsed.has_complete_metadata:
            return ValidationResult(
                processable=False, reason=ValidationReason.METADADOS_AUSENTES
            )
        return ValidationResult(processable=True)

    def validate_technical(self, image_bytes: bytes) -> ValidationResult:
        """Valida formato, resolução e nitidez da imagem — roda no worker.

        Passos em curto-circuito (retorna ao primeiro problema):
          1. Formato: JPG ou PNG
          2. Resolução: ≥ 1280×720
          3. Nitidez: variância do Laplaciano (via PIL FIND_EDGES) ≥ threshold
        """
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.verify()  # levanta se o arquivo estiver corrompido
            img = Image.open(io.BytesIO(image_bytes))  # reabrir após verify()
        except Exception:
            return ValidationResult(
                processable=False, reason=ValidationReason.FORMATO_INVALIDO
            )

        if img.format not in ("JPEG", "PNG"):
            return ValidationResult(
                processable=False, reason=ValidationReason.FORMATO_INVALIDO
            )

        w, h = img.size
        if w < self.MIN_WIDTH or h < self.MIN_HEIGHT:
            return ValidationResult(
                processable=False, reason=ValidationReason.RESOLUCAO_BAIXA
            )

        variance = self._laplacian_variance(img)
        if variance < self.LAPLACIAN_VARIANCE_THRESHOLD:
            return ValidationResult(
                processable=False, reason=ValidationReason.FOCO_INADEQUADO
            )

        return ValidationResult(processable=True)

    @staticmethod
    def _laplacian_variance(img: object) -> float:
        """Variância da imagem filtrada por FIND_EDGES — proxy de nitidez."""
        from PIL import ImageFilter
        from PIL.ImageStat import Stat

        gray = img.convert("L")  # type: ignore[attr-defined]
        edges = gray.filter(ImageFilter.FIND_EDGES)
        return float(Stat(edges).var[0])
