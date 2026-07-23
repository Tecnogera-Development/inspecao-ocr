"""Testes de thumb_cache — IAVS-036."""

from __future__ import annotations

import io
import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image as PilImage


def _make_jpeg(path: Path, width: int = 600, height: int = 400) -> None:
    """Cria JPEG de cor sólida para usar nos testes."""
    img = PilImage.new("RGB", (width, height), color=(100, 150, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="JPEG")


# --------------------------------------------------------------------------- #
# Cycle 1 — cache miss gera thumb e retorna bytes JPEG                        #
# --------------------------------------------------------------------------- #


def test_cache_miss_gera_arquivo_e_retorna_bytes(tmp_path: Path) -> None:
    from app.services.thumb_cache import get_thumb

    work_dir = tmp_path / "checklists"
    cache_dir = tmp_path / "thumbs"
    photo_id = "153074915_checklist_276800_c145_0_09_04_2026 18_03_00.jpeg"
    checklist_dir = work_dir / "276800"
    _make_jpeg(checklist_dir / photo_id)

    result = get_thumb(photo_id, width=240, work_dir=work_dir, cache_dir=cache_dir)

    assert isinstance(result, bytes)
    img = PilImage.open(io.BytesIO(result))
    assert img.format == "JPEG"
    cached = cache_dir / f"{photo_id}_240.jpg"
    assert cached.exists()


# --------------------------------------------------------------------------- #
# Cycle 2 — cache hit não regenera (lê do disco)                              #
# --------------------------------------------------------------------------- #


def test_cache_hit_nao_regera_arquivo(tmp_path: Path) -> None:
    from app.services.thumb_cache import get_thumb

    work_dir = tmp_path / "checklists"
    cache_dir = tmp_path / "thumbs"
    photo_id = "153074915_checklist_276800_c145_0_09_04_2026 18_03_00.jpeg"
    checklist_dir = work_dir / "276800"
    _make_jpeg(checklist_dir / photo_id)

    result1 = get_thumb(photo_id, width=240, work_dir=work_dir, cache_dir=cache_dir)
    cached = cache_dir / f"{photo_id}_240.jpg"
    mtime_after_first = cached.stat().st_mtime

    result2 = get_thumb(photo_id, width=240, work_dir=work_dir, cache_dir=cache_dir)
    mtime_after_second = cached.stat().st_mtime

    assert result1 == result2
    assert mtime_after_first == mtime_after_second  # arquivo não foi reescrito


# --------------------------------------------------------------------------- #
# Cycle 3 — dimensão do thumb ≤ width solicitado                              #
# --------------------------------------------------------------------------- #


def test_dimensao_thumb_respeitada(tmp_path: Path) -> None:
    from app.services.thumb_cache import get_thumb

    work_dir = tmp_path / "checklists"
    cache_dir = tmp_path / "thumbs"
    photo_id = "153074915_checklist_276800_c145_0_09_04_2026 18_03_00.jpeg"
    _make_jpeg(work_dir / "276800" / photo_id, width=600, height=400)

    result = get_thumb(photo_id, width=120, work_dir=work_dir, cache_dir=cache_dir)

    img = PilImage.open(io.BytesIO(result))
    assert img.width <= 120
    assert img.height <= 120


# --------------------------------------------------------------------------- #
# Cycle 4 — lock evita duplicate work em chamadas concorrentes                #
# --------------------------------------------------------------------------- #


def test_lock_evita_duplicate_work(tmp_path: Path) -> None:
    from app.services.thumb_cache import get_thumb

    work_dir = tmp_path / "checklists"
    cache_dir = tmp_path / "thumbs"
    photo_id = "153074915_checklist_276800_c145_0_09_04_2026 18_03_00.jpeg"
    _make_jpeg(work_dir / "276800" / photo_id)

    write_count: list[int] = []
    orig_write_bytes = Path.write_bytes

    def counting_write(self: Path, data: bytes) -> int:
        if "thumbs" in str(self):
            write_count.append(1)
        return orig_write_bytes(self, data)

    results: list[bytes] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            b = get_thumb(photo_id, width=240, work_dir=work_dir, cache_dir=cache_dir)
            results.append(b)
        except Exception as exc:
            errors.append(exc)

    import unittest.mock as mock

    with mock.patch.object(Path, "write_bytes", counting_write):
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not errors
    assert len(results) == 5
    assert len(write_count) == 1, f"thumb gerado {len(write_count)} vezes; esperado 1"


# --------------------------------------------------------------------------- #
# Cycle 5 — FileNotFoundError quando original não existe                      #
# --------------------------------------------------------------------------- #


def test_foto_original_ausente_levanta_file_not_found(tmp_path: Path) -> None:
    from app.services.thumb_cache import get_thumb

    work_dir = tmp_path / "checklists"
    cache_dir = tmp_path / "thumbs"
    photo_id = "153074915_checklist_276800_c145_0_09_04_2026 18_03_00.jpeg"
    # não cria a foto

    with pytest.raises(FileNotFoundError):
        get_thumb(photo_id, width=240, work_dir=work_dir, cache_dir=cache_dir)


# --------------------------------------------------------------------------- #
# Cycle 6 — ValueError para width inválido                                    #
# --------------------------------------------------------------------------- #


def test_width_invalido_levanta_value_error(tmp_path: Path) -> None:
    from app.services.thumb_cache import get_thumb

    with pytest.raises(ValueError, match="999"):
        get_thumb("qualquer.jpeg", width=999, work_dir=tmp_path, cache_dir=tmp_path)
