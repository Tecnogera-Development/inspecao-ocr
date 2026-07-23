"""Testes de segurança do thumb cache — path traversal (auditoria)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.thumb_cache import _find_original, _is_safe_photo_id, get_thumb


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd_x.jpg",
        "..%2F..%2Fpasswd",
        "a/b/c.jpg",
        "..\\windows",
        "sub/153074915_checklist_276800_c145_0_01_02_2025 10_00_00.jpeg",
    ],
)
def test_is_safe_photo_id_rejeita_traversal(bad: str) -> None:
    assert _is_safe_photo_id(bad) is False


@pytest.mark.unit
def test_is_safe_photo_id_aceita_nome_valido() -> None:
    assert _is_safe_photo_id("153074915_checklist_276800_c145_0_01_02_2025 10_00_00.jpeg") is True


@pytest.mark.unit
def test_find_original_rejeita_traversal(tmp_path: Path) -> None:
    assert _find_original("../../etc/passwd_x.jpg", tmp_path) is None


@pytest.mark.unit
def test_get_thumb_rejeita_traversal(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        get_thumb(
            "../../etc/passwd_x.jpg",
            width=240,
            work_dir=tmp_path / "w",
            cache_dir=tmp_path / "c",
        )
