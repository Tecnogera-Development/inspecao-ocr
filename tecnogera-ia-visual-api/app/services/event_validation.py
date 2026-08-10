"""Gate de validação de evento — IAVS-061, **calibrado** em ``mvp-c54-c57/08``.

Divide-se em dois métodos conforme as notas de execução do issue:

  validate_metadata(parsed) → sync, sem baixar imagem, chamado no ingest
  validate_technical(image_bytes) → async no worker, usa bytes já baixados

Calibração (562 imagens reais de ``data/checklists/``, 9 checklists)
--------------------------------------------------------------------

**A regra de resolução estava rejeitando 79,4% do parque — por orientação.**
``w < 1280 or h < 720`` reprova qualquer retrato: a foto de campo do Sisloc é
720×1280 ou 960×1280 (446 das 562; **as 18 fotos de `c54`–`c57`, 100% delas**).
Ou seja, a esteira do MVP marcaria todo checklist como ``resolucao_baixa`` sem
gastar um token, e o operador veria uma tela vazia. A regra agora compara lado
MAIOR contra ``MIN_WIDTH`` e lado MENOR contra ``MIN_HEIGHT``: os mesmos
números, agnósticos de orientação. Rejeição no corpus real: **0%**.

**Nitidez: 100,0 nunca foi medido; agora foi.** Distribuição da variância de
FIND_EDGES no corpus: mín 34,2 · p1 169,6 · mediana 748,5 · máx 5.266,7. As
únicas três imagens abaixo de 152,7 são quadros degenerados — dois pretos
(lente tapada, 34,2 e 35,8) e um laranja chapado (lente encostada, 80,4). Entre
80,4 e 152,7 há uma **banda vazia**: nenhuma foto real cai ali. O valor 100,0
caía dentro da banda, mas encostado no piso; **120,0 fica no centro** — +49%
acima do pior quadro degenerado, −21% abaixo da foto real mais fraca, e 3,75×
abaixo da pior vista `c54`–`c57` do corpus (450,3).

**Nitidez não é porteiro suficiente — e a calibração não conserta isso.**
O `c57` do checklist 278154 tem variância 636,7 (passa folgado) e é inútil:
contraluz severo, assunto em silhueta. O portão técnico aqui é só o piso de
quadro degenerado; quem julga se a foto é julgável é o modelo, que pode
devolver ``processavel=false`` por conta própria (taxonomia v0.2 §8). São dois
portões complementares, e o segundo é o que decide.
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

    #: Lado MAIOR mínimo (não "largura": a foto de campo é retrato).
    MIN_WIDTH: int = 1280
    #: Lado MENOR mínimo.
    MIN_HEIGHT: int = 720
    #: Piso de quadro degenerado, calibrado em 562 imagens reais — ver módulo.
    LAPLACIAN_VARIANCE_THRESHOLD: float = 120.0

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
          2. Resolução: ≥ 1280×720 **em qualquer orientação**
          3. Nitidez: variância do Laplaciano (via PIL FIND_EDGES) ≥ threshold

        Não confunda ``processable=True`` com "foto boa": este gate só barra
        quadro degenerado. Foto nítida e enquadrada mas inútil (contraluz,
        rasante, só a quina) passa aqui e é reprovada pelo modelo.
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

        # Agnóstico de orientação: 79,4% do parque é retrato e seria reprovado
        # por comparar largura contra o lado longo. Ver docstring do módulo.
        w, h = img.size
        if max(w, h) < self.MIN_WIDTH or min(w, h) < self.MIN_HEIGHT:
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
