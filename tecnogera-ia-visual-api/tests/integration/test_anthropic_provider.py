"""Smoke test de integração real do AnthropicProvider — IAVS-002.

Requer ANTHROPIC_API_KEY no ambiente. Skipado em CI (LLM_PROVIDER=fake por padrão).
Execute manualmente:
    ANTHROPIC_API_KEY=sk-... pytest tests/integration/test_anthropic_provider.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY não configurada — smoke test skipado")
    return key


@pytest.fixture
def fixture_image() -> bytes:
    fixture_path = Path(__file__).parent.parent / "fixtures" / "sample_image.jpg"
    if fixture_path.exists():
        return fixture_path.read_bytes()
    # Fallback: JPEG mínimo 1x1 pixel (válido para testar a API)
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00"
        b"\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00"
        b"\x08\x01\x01\x00\x00?\x00\xf5\xfc\xa8\x28\xff\xd9"
    )


def test_classify_image_smoke(api_key: str, fixture_image: bytes) -> None:
    """Smoke test: AnthropicProvider.classify_image retorna ClassificationResult válido."""
    from app.services.llm_provider import AnthropicProvider, ClassificationResult

    provider = AnthropicProvider(api_key=api_key, model="claude-sonnet-4-6")
    result = provider.classify_image(
        image_filename="153269005_checklist_276800_c0_0_10_04_2026.jpeg",
        image_bytes=fixture_image,
        field_names=["c0", "c3", "c6", "c55"],
    )

    assert isinstance(result, ClassificationResult)
    assert result.field_name in ["c0", "c3", "c6", "c55"] or result.field_name is not None
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.observation, str)
    assert isinstance(result.detected_issues, list)
    assert result.model_version == "claude-sonnet-4-6"
