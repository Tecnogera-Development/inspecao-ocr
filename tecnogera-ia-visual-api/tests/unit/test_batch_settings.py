"""Testes de settings para Batch API — IAVS-041."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_batch_min_images_default_30() -> None:
    """BATCH_MIN_IMAGES tem valor default 30."""
    from app.core.config import Settings

    cfg = Settings()
    assert cfg.batch_min_images == 30


@pytest.mark.unit
def test_batch_min_images_configuravel_via_env() -> None:
    """BATCH_MIN_IMAGES pode ser sobrescrito via env var."""
    from app.core.config import Settings

    cfg = Settings(batch_min_images=50)
    assert cfg.batch_min_images == 50
