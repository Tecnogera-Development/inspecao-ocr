"""Testes de EventValidationService — IAVS-061.

Fixtures de imagem sintética via PIL (sem dependência de arquivo externo).
Cobre: formato, resolução, nitidez, metadados.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.models.dropbox import ParsedEventFilename
from app.services.event_validation import EventValidationService, ValidationReason


# ── helpers de fixture ────────────────────────────────────────────────────────

def _make_jpeg_bytes(width: int, height: int, *, sharp: bool = True) -> bytes:
    """Cria um JPEG sintético com ou sem nitidez."""
    img = Image.new("RGB", (width, height), color=(180, 180, 180))
    if sharp:
        draw = ImageDraw.Draw(img)
        # Grade de linhas finas = muito contraste = alta variância de Laplaciano
        for x in range(0, width, 4):
            draw.line([(x, 0), (x, height)], fill=(0, 0, 0), width=1)
        for y in range(0, height, 4):
            draw.line([(0, y), (width, y)], fill=(0, 0, 0), width=1)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _make_png_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 4):
        draw.line([(x, 0), (x, height)], fill=(0, 0, 0), width=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _parsed(
    *,
    asset_code: str = "FROTA001",
    moment: str | None = "saida",
    canonical_angle: str | None = "frontal",
    uploaded_by: str | None = "joao",
    captured_at_set: bool = True,
) -> ParsedEventFilename:
    from datetime import datetime
    return ParsedEventFilename(
        raw="/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg",
        asset_code=asset_code,
        moment=moment,
        canonical_angle=canonical_angle,
        uploaded_by=uploaded_by,
        captured_at=datetime(2026, 6, 1, 14, 30, 22) if captured_at_set else None,
        extension=".jpg",
    )


# ── validate_metadata ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidateMetadata:
    svc = EventValidationService()

    def test_metadata_completa_ok(self) -> None:
        result = self.svc.validate_metadata(_parsed())
        assert result.processable is True
        assert result.reason is None

    def test_moment_ausente(self) -> None:
        result = self.svc.validate_metadata(_parsed(moment=None))
        assert result.processable is False
        assert result.reason == ValidationReason.METADADOS_AUSENTES

    def test_angulo_ausente(self) -> None:
        result = self.svc.validate_metadata(_parsed(canonical_angle=None))
        assert result.processable is False
        assert result.reason == ValidationReason.METADADOS_AUSENTES

    def test_uploaded_by_ausente(self) -> None:
        result = self.svc.validate_metadata(_parsed(uploaded_by=None))
        assert result.processable is False
        assert result.reason == ValidationReason.METADADOS_AUSENTES

    def test_captured_at_ausente(self) -> None:
        result = self.svc.validate_metadata(_parsed(captured_at_set=False))
        assert result.processable is False
        assert result.reason == ValidationReason.METADADOS_AUSENTES


# ── validate_technical ────────────────────────────────────────────────────────

@pytest.mark.unit
class TestValidateTechnical:
    svc = EventValidationService()

    def test_jpeg_1280x720_nitido_ok(self) -> None:
        img_bytes = _make_jpeg_bytes(1280, 720, sharp=True)
        result = self.svc.validate_technical(img_bytes)
        assert result.processable is True

    def test_png_1920x1080_ok(self) -> None:
        img_bytes = _make_png_bytes(1920, 1080)
        result = self.svc.validate_technical(img_bytes)
        assert result.processable is True

    def test_resolucao_baixa_640x480(self) -> None:
        img_bytes = _make_jpeg_bytes(640, 480, sharp=True)
        result = self.svc.validate_technical(img_bytes)
        assert result.processable is False
        assert result.reason == ValidationReason.RESOLUCAO_BAIXA

    def test_resolucao_limite_exato_1280x720_ok(self) -> None:
        img_bytes = _make_jpeg_bytes(1280, 720, sharp=True)
        result = self.svc.validate_technical(img_bytes)
        assert result.processable is True

    def test_resolucao_1280x719_reprovada(self) -> None:
        img_bytes = _make_jpeg_bytes(1280, 719, sharp=True)
        result = self.svc.validate_technical(img_bytes)
        assert result.processable is False
        assert result.reason == ValidationReason.RESOLUCAO_BAIXA

    def test_bytes_invalidos_formato_invalido(self) -> None:
        result = self.svc.validate_technical(b"isso nao e uma imagem")
        assert result.processable is False
        assert result.reason == ValidationReason.FORMATO_INVALIDO

    def test_imagem_borrada_foco_inadequado(self) -> None:
        # Imagem sólida sem nenhuma borda = variância de Laplaciano ≈ 0
        img = Image.new("RGB", (1280, 720), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        img_bytes = buf.getvalue()
        result = self.svc.validate_technical(img_bytes)
        assert result.processable is False
        assert result.reason == ValidationReason.FOCO_INADEQUADO

    def test_threshold_customizavel(self) -> None:
        """Threshold pode ser sobrescrito por subclasse."""
        class StrictSvc(EventValidationService):
            LAPLACIAN_VARIANCE_THRESHOLD = 999999.0

        img_bytes = _make_jpeg_bytes(1280, 720, sharp=True)
        result = StrictSvc().validate_technical(img_bytes)
        # mesmo uma imagem nítida vai falhar com threshold absurdo
        assert result.processable is False
        assert result.reason == ValidationReason.FOCO_INADEQUADO


# ── calibração com imagens reais (ticket mvp-c54-c57/08) ─────────────────────

@pytest.mark.unit
class TestCalibracaoComParqueReal:
    """Medido em 562 imagens de ``data/checklists/`` — ver docstring do módulo."""

    svc = EventValidationService()

    @pytest.mark.parametrize(
        ("w", "h"),
        [(720, 1280), (960, 1280), (961, 1280), (1280, 960)],
    )
    def test_resolucoes_reais_do_sisloc_passam(self, w: int, h: int) -> None:
        """As 4 resoluções do corpus. A regra antiga reprovava 79,4% delas.

        A foto de campo é RETRATO (720×1280, 960×1280): comparar largura contra
        o lado longo marcava todo `c54`–`c57` como `resolucao_baixa` e a esteira
        inteira entregaria tela vazia sem gastar um token.
        """
        result = self.svc.validate_technical(_make_jpeg_bytes(w, h, sharp=True))
        assert result.processable is True

    def test_retrato_pequeno_demais_continua_reprovado(self) -> None:
        """Agnóstico de orientação não é 'aceita qualquer coisa'."""
        result = self.svc.validate_technical(_make_jpeg_bytes(480, 640, sharp=True))
        assert result.processable is False
        assert result.reason == ValidationReason.RESOLUCAO_BAIXA

    def test_threshold_de_nitidez_esta_na_banda_vazia_medida(self) -> None:
        """Pior quadro degenerado: 80,4. Foto real mais fraca: 152,7."""
        assert 80.4 < EventValidationService.LAPLACIAN_VARIANCE_THRESHOLD < 152.6

    def test_quadro_preto_chapado_e_barrado(self) -> None:
        """Os dois piores do corpus (variância 34,2 e 35,8) são lente tapada."""
        img = Image.new("RGB", (960, 1280), color=(2, 2, 2))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)

        result = self.svc.validate_technical(buf.getvalue())

        assert result.processable is False
        assert result.reason == ValidationReason.FOCO_INADEQUADO

    def test_foto_nitida_mas_injulgavel_passa_no_gate(self) -> None:
        """Nitidez NÃO é porteiro suficiente — o modelo é o segundo portão.

        Caso real: o `c57` do checklist 278154 tem variância 636,7 e é inútil
        por contraluz severo. Este gate a aprova, de propósito; quem a reprova
        é o modelo, via ``processavel=false`` (taxonomia v0.2 §8).
        """
        result = self.svc.validate_technical(_make_jpeg_bytes(960, 1280, sharp=True))
        assert result.processable is True


# ── ValidationResult ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validation_result_imutavel() -> None:
    from dataclasses import FrozenInstanceError

    from app.services.event_validation import ValidationResult as VR

    r = VR(processable=True)
    with pytest.raises((FrozenInstanceError, TypeError)):
        r.processable = False  # type: ignore[misc]
