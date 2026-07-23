"""Geração de artefatos por par de avaria (IAVS-065).

Responsabilidade: compor JPG saída×retorno com legendas PIL e publicar no Dropbox.
Sem bboxes — Vision LLM não localiza regiões, só classifica.
"""

from __future__ import annotations

import io
from datetime import date
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.models.event import Event
    from app.models.event_pair import EventPair
    from app.services.dropbox import DropboxService
    from sqlalchemy.orm import Session

_log = get_logger(__name__)

_TARGET_HEIGHT = 480        # px — altura alvo de cada metade
_HEADER_H = 48              # px — barra de cabeçalho
_CAPTION_H = 80             # px — barra de legenda abaixo das imagens
_DIVIDER_W = 3              # px — linha separadora entre metades
_BG = (24, 24, 24)
_FG = (240, 240, 240)
_GREEN = (60, 200, 90)
_RED = (210, 60, 60)
_YELLOW = (255, 220, 50)


class ArtifactService:
    """Gera e publica artefatos visuais de pares de avaria."""

    def __init__(
        self,
        db: "Session",
        dropbox: "DropboxService",
        settings: "Settings",
    ) -> None:
        self._db = db
        self._dropbox = dropbox
        self._settings = settings

    def generate_composite(self, pair: "EventPair") -> str | None:
        """Gera JPG composto saída×retorno para um par completo.

        Retorna o Dropbox path do artefato gerado, ou None se o par não está
        completo ou o artefato já existe (idempotente).
        """
        if pair.status != "complete":
            _log.debug("composite_skip_partial", pair_id=str(pair.id))
            return None
        if pair.annotated_image_path:
            return pair.annotated_image_path

        from app.models.event import Event  # noqa: PLC0415

        saida_ev = self._db.get(Event, pair.saida_event_id)
        retorno_ev = self._db.get(Event, pair.retorno_event_id)
        if saida_ev is None or retorno_ev is None:
            _log.warning("composite_missing_event", pair_id=str(pair.id))
            return None

        saida_bytes = self._dropbox.download_image(saida_ev.source_path)
        retorno_bytes = self._dropbox.download_image(retorno_ev.source_path)

        composite_bytes = _build_composite(
            saida_bytes=saida_bytes,
            retorno_bytes=retorno_bytes,
            saida_info=saida_ev.result_json or {},
            retorno_info=retorno_ev.result_json or {},
            asset_code=pair.asset_code,
            pair_date=pair.pair_date,
        )

        dropbox_path = self._dropbox.upload_annotated_image(
            asset_code=pair.asset_code,
            pair_date=pair.pair_date,
            composite_bytes=composite_bytes,
        )

        pair.annotated_image_path = dropbox_path
        self._db.commit()

        _log.info(
            "composite_generated",
            pair_id=str(pair.id),
            path=dropbox_path,
            size_bytes=len(composite_bytes),
        )
        return dropbox_path


# ── PIL composite builder ────────────────────────────────────────────────────


def _build_composite(
    saida_bytes: bytes,
    retorno_bytes: bytes,
    saida_info: dict[str, Any],
    retorno_info: dict[str, Any],
    asset_code: str,
    pair_date: "date",
) -> bytes:
    """Retorna bytes do JPG composto saída|retorno com legendas."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    saida_img = _open_rgb(saida_bytes)
    retorno_img = _open_rgb(retorno_bytes)

    saida_img = _resize_to_height(saida_img, _TARGET_HEIGHT)
    retorno_img = _resize_to_height(retorno_img, _TARGET_HEIGHT)

    total_w = saida_img.width + _DIVIDER_W + retorno_img.width
    total_h = _HEADER_H + _TARGET_HEIGHT + _CAPTION_H

    canvas = Image.new("RGB", (total_w, total_h), _BG)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    # ── header ──────────────────────────────────────────────────────────────
    header_text = f"{asset_code}  ·  {pair_date}"
    draw.text(
        (total_w // 2, _HEADER_H // 2),
        header_text,
        fill=_FG,
        font=font,
        anchor="mm",
    )

    # ── images ──────────────────────────────────────────────────────────────
    canvas.paste(saida_img, (0, _HEADER_H))
    canvas.paste(retorno_img, (saida_img.width + _DIVIDER_W, _HEADER_H))

    # divider
    draw.rectangle(
        [(saida_img.width, _HEADER_H), (saida_img.width + _DIVIDER_W - 1, _HEADER_H + _TARGET_HEIGHT)],
        fill=(80, 80, 80),
    )

    # ── side labels (top-left of each half) ─────────────────────────────────
    draw.text((8, _HEADER_H + 6), "SAÍDA", fill=_YELLOW, font=font)
    draw.text((saida_img.width + _DIVIDER_W + 8, _HEADER_H + 6), "RETORNO", fill=_YELLOW, font=font)

    # ── captions ────────────────────────────────────────────────────────────
    y0 = _HEADER_H + _TARGET_HEIGHT + 6
    _draw_caption(draw, font, saida_info, cx=saida_img.width // 2, y=y0)
    _draw_caption(draw, font, retorno_info, cx=saida_img.width + _DIVIDER_W + retorno_img.width // 2, y=y0)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


def _draw_caption(
    draw: Any,
    font: Any,
    info: dict[str, Any],
    *,
    cx: int,
    y: int,
) -> None:
    no_conf = info.get("no_conformity", False)
    status_text = "NÃO CONFORME" if no_conf else "CONFORME"
    status_color = _RED if no_conf else _GREEN
    draw.text((cx, y), status_text, fill=status_color, font=font, anchor="mt")

    if no_conf and info.get("damage_class"):
        severity = info.get("damage_severity") or ""
        detail = f"{info['damage_class']}  sev:{severity}"
        draw.text((cx, y + 16), detail, fill=_FG, font=font, anchor="mt")

    angle = info.get("canonical_angle")
    if angle:
        draw.text((cx, y + 32), f"ângulo: {angle}", fill=(180, 180, 180), font=font, anchor="mt")


def _open_rgb(image_bytes: bytes) -> Any:
    from PIL import Image  # noqa: PLC0415

    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def _resize_to_height(img: Any, target_h: int) -> Any:
    if img.height == 0:
        return img
    ratio = target_h / img.height
    new_w = max(1, int(img.width * ratio))
    from PIL import Image  # noqa: PLC0415

    return img.resize((new_w, target_h), Image.Resampling.LANCZOS)
