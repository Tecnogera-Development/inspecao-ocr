"""Testes unitários do AnthropicProvider.classify_image_batch — IAVS-041."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.llm_provider import AnthropicProvider


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_batch_response(batch_id: str = "batch_01abc") -> MagicMock:
    """Mock de messages.batches.create retornando um batch_id."""
    response = MagicMock()
    response.id = batch_id
    return response


def _make_provider(batch_response: MagicMock | None = None) -> tuple[AnthropicProvider, MagicMock]:
    mock_client = MagicMock()
    if batch_response is not None:
        mock_client.messages.batches.create.return_value = batch_response
    provider = AnthropicProvider(
        api_key="sk-test",
        model="claude-sonnet-4-6",
        _client=mock_client,
    )
    return provider, mock_client


_FAKE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"

# ── Tracer bullet ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_batch_retorna_batch_id() -> None:
    """classify_image_batch retorna o batch_id da resposta da Anthropic."""
    provider, _ = _make_provider(_make_batch_response("batch_abc123"))

    batch_id = provider.classify_image_batch(
        images=[
            ("img1_c0.jpeg", _FAKE_JPEG),
            ("img2_c3.jpeg", _FAKE_JPEG),
        ],
        field_names=["c0", "c3"],
    )

    assert batch_id == "batch_abc123"


# ── Custom IDs ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_batch_usa_filename_como_custom_id() -> None:
    """Cada imagem gera um request com custom_id = filename."""
    provider, mock_client = _make_provider(_make_batch_response())

    provider.classify_image_batch(
        images=[
            ("checklist_276800_c0.jpeg", _FAKE_JPEG),
            ("checklist_276800_c3.jpeg", _FAKE_JPEG),
        ],
        field_names=["c0", "c3"],
    )

    call_kwargs = mock_client.messages.batches.create.call_args.kwargs
    requests = call_kwargs["requests"]
    custom_ids = [r["custom_id"] for r in requests]
    assert "checklist_276800_c0.jpeg" in custom_ids
    assert "checklist_276800_c3.jpeg" in custom_ids


@pytest.mark.unit
def test_classify_image_batch_gera_um_request_por_imagem() -> None:
    """Número de requests no batch = número de imagens."""
    provider, mock_client = _make_provider(_make_batch_response())

    images = [
        (f"img_{i}.jpeg", _FAKE_JPEG) for i in range(5)
    ]
    provider.classify_image_batch(images=images, field_names=["c0"])

    requests = mock_client.messages.batches.create.call_args.kwargs["requests"]
    assert len(requests) == 5


# ── System cache_control ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_batch_inclui_cache_control_no_system() -> None:
    """Cada request do batch tem cache_control no bloco system (ephemeral)."""
    provider, mock_client = _make_provider(_make_batch_response())

    provider.classify_image_batch(
        images=[("img.jpeg", _FAKE_JPEG)],
        field_names=["c0"],
    )

    requests = mock_client.messages.batches.create.call_args.kwargs["requests"]
    req = requests[0]
    system_blocks = req["params"]["system"]
    cache_blocks = [b for b in system_blocks if b.get("cache_control")]
    assert len(cache_blocks) >= 1
    assert cache_blocks[0]["cache_control"]["type"] == "ephemeral"


# ── Tool schema ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_batch_usa_emit_classification_tool() -> None:
    """Cada request usa a mesma tool emit_classification com enum=field_names."""
    provider, mock_client = _make_provider(_make_batch_response())

    provider.classify_image_batch(
        images=[("img.jpeg", _FAKE_JPEG)],
        field_names=["c0", "c55", "c145"],
    )

    requests = mock_client.messages.batches.create.call_args.kwargs["requests"]
    req = requests[0]
    tools = req["params"]["tools"]
    assert tools[0]["name"] == "emit_classification"
    enum = tools[0]["input_schema"]["properties"]["field_name"]["enum"]
    assert enum == ["c0", "c55", "c145"]


@pytest.mark.unit
def test_classify_image_batch_usa_tool_choice_forcado() -> None:
    """Cada request usa tool_choice={'type':'tool','name':'emit_classification'}."""
    provider, mock_client = _make_provider(_make_batch_response())

    provider.classify_image_batch(
        images=[("img.jpeg", _FAKE_JPEG)],
        field_names=["c0"],
    )

    requests = mock_client.messages.batches.create.call_args.kwargs["requests"]
    req = requests[0]
    tc = req["params"]["tool_choice"]
    assert tc["type"] == "tool"
    assert tc["name"] == "emit_classification"
