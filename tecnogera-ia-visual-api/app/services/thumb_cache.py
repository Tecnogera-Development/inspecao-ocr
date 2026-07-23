"""Thumbnail cache — gera e serve JPGs redimensionados via Pillow.

Cache em disco: cache_dir/{photo_id}_{width}.jpg.
Lock por photo_id evita duplicate work em chamadas concorrentes.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

from PIL import Image as PilImage

from app.services.dropbox import parse_filename

ALLOWED_WIDTHS: frozenset[int] = frozenset({120, 240, 480})

_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()


def _get_lock(photo_id: str) -> threading.Lock:
    with _locks_meta:
        if photo_id not in _locks:
            _locks[photo_id] = threading.Lock()
        return _locks[photo_id]


def _is_safe_photo_id(photo_id: str) -> bool:
    """Rejeita photo_id com separadores/traversal (a rota usa ``{photo_id:path}``)."""
    return not ("/" in photo_id or "\\" in photo_id or ".." in photo_id)


def _find_original(photo_id: str, work_dir: Path) -> Path | None:
    """Deriva o path da foto original a partir do photo_id e do work_dir."""
    if not _is_safe_photo_id(photo_id):
        return None
    try:
        parsed = parse_filename(photo_id)
        checklist_id = parsed.checklist_id
    except ValueError:
        return None
    work_root = work_dir.resolve()
    candidate = (work_root / checklist_id / photo_id).resolve()
    # Defesa em profundidade: garante que o path final não escapa de work_dir.
    if not candidate.is_relative_to(work_root):
        return None
    return candidate if candidate.exists() else None


def get_thumb(
    photo_id: str,
    width: int = 240,
    *,
    work_dir: Path,
    cache_dir: Path,
) -> bytes:
    """Retorna bytes JPEG do thumbnail da foto.

    Parâmetros
    ----------
    photo_id:
        Nome do arquivo da foto (ex: ``153074915_checklist_276800_c145_0_...jpeg``).
    width:
        Largura máxima do thumb. Deve estar em ``ALLOWED_WIDTHS``.
    work_dir:
        Raiz dos checklists baixados (ex: ``/tmp/checklists``).
    cache_dir:
        Diretório de cache dos thumbs (ex: ``data/cache/thumbs``).

    Raises
    ------
    ValueError
        Se *width* não está em ``ALLOWED_WIDTHS``.
    FileNotFoundError
        Se a foto original não foi encontrada em work_dir.
    """
    if width not in ALLOWED_WIDTHS:
        raise ValueError(f"width {width} não permitido; use um de {sorted(ALLOWED_WIDTHS)}")

    # photo_id vem da URL: rejeita antes de compor qualquer path de cache.
    if not _is_safe_photo_id(photo_id):
        raise FileNotFoundError(f"Foto não encontrada: {photo_id}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{photo_id}_{width}.jpg"

    if cache_path.exists():
        return cache_path.read_bytes()

    lock = _get_lock(photo_id)
    with lock:
        # double-check inside lock
        if cache_path.exists():
            return cache_path.read_bytes()

        original = _find_original(photo_id, work_dir)
        if original is None:
            raise FileNotFoundError(f"Foto não encontrada: {photo_id}")

        img = PilImage.open(original)
        img.thumbnail((width, width))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        data = buf.getvalue()
        cache_path.write_bytes(data)
        return data
