"""Testes unitários do AnthropicProvider — IAVS-002."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.llm_provider import AnthropicProvider, ClassificationResult


def _make_tool_use_response(
    field_name: str = "c0",
    confidence: float = 0.92,
    observation: str = "Campo fotografado corretamente.",
    detected_issues: list[str] | None = None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> MagicMock:
    """Cria uma resposta mock do anthropic.messages.create com tool_use."""
    response = MagicMock()

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "emit_classification"
    tool_block.input = {
        "field_name": field_name,
        "confidence": confidence,
        "observation": observation,
        "detected_issues": detected_issues or [],
    }
    response.content = [tool_block]

    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 20
    # Cache fields são extras no SDK 0.40.0
    type(usage).cache_read_input_tokens = property(lambda self: cache_read_tokens)
    type(usage).cache_creation_input_tokens = property(lambda self: cache_write_tokens)
    response.usage = usage

    return response


def _make_provider_with_mock(response: MagicMock) -> tuple[AnthropicProvider, MagicMock]:
    """Cria AnthropicProvider com cliente mock injetado."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response
    provider = AnthropicProvider(
        api_key="sk-test",
        model="claude-sonnet-4-6",
        _client=mock_client,
    )
    return provider, mock_client


# ── Tracer bullet ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_retorna_classification_result_valido() -> None:
    """AnthropicProvider.classify_image retorna ClassificationResult com campo e confiança."""
    response = _make_tool_use_response(field_name="c3", confidence=0.88)
    provider, _ = _make_provider_with_mock(response)

    result = provider.classify_image(
        image_filename="153269005_checklist_276800_c3_0_10_04_2026.jpeg",
        image_bytes=b"\xff\xd8\xff",
        field_names=["c0", "c3", "c6"],
    )

    assert isinstance(result, ClassificationResult)
    assert result.field_name == "c3"
    assert result.confidence == pytest.approx(0.88)
    assert result.is_valid is True  # 0.88 >= 0.70
    assert result.requires_human_review is False
    assert result.image_filename == "153269005_checklist_276800_c3_0_10_04_2026.jpeg"
    assert result.model_version == "claude-sonnet-4-6"


# ── Threshold ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_is_valid_false_quando_confianca_abaixo_threshold() -> None:
    """Confiança < 0.70 → is_valid=False, requires_human_review depende do floor."""
    response = _make_tool_use_response(field_name="c0", confidence=0.55)
    provider, _ = _make_provider_with_mock(response)

    result = provider.classify_image(
        image_filename="img.jpeg",
        image_bytes=b"\xff",
        field_names=["c0"],
    )

    assert result.is_valid is False
    assert result.requires_human_review is True  # 0.40 <= 0.55 < 0.70


@pytest.mark.unit
def test_classify_image_nao_requer_review_quando_confianca_muito_baixa() -> None:
    """Confiança < 0.40 → is_valid=False, mas requires_human_review=False (abaixo do floor)."""
    response = _make_tool_use_response(field_name="c0", confidence=0.30)
    provider, _ = _make_provider_with_mock(response)

    result = provider.classify_image(
        image_filename="img.jpeg",
        image_bytes=b"\xff",
        field_names=["c0"],
    )

    assert result.is_valid is False
    assert result.requires_human_review is False


# ── Cache payload structure ────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_inclui_cache_control_no_payload() -> None:
    """classify_image envia cache_control na última mensagem antes da imagem alvo."""
    response = _make_tool_use_response()
    provider, mock_client = _make_provider_with_mock(response)

    shots = [
        ("shot_c0_1.jpeg", b"\xff\xd8\xff"),
        ("shot_c0_2.jpeg", b"\xff\xd8\xff"),
    ]

    provider.classify_image(
        image_filename="img.jpeg",
        image_bytes=b"\xff\xd8\xff",
        field_names=["c0"],
        shots=shots,
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    messages = call_kwargs["messages"]
    # O payload de mensagens deve conter cache_control em algum bloco
    payload_str = json.dumps(messages, default=str)
    assert "cache_control" in payload_str
    assert "ephemeral" in payload_str


@pytest.mark.unit
def test_classify_image_rotula_cada_shot_com_field_name() -> None:
    """Cada shot deve ser precedido por texto 'Exemplo do campo cN (i/N):'.

    Sem rotulagem, o modelo não consegue associar shot ↔ classe (causa raiz do 0%).
    """
    response = _make_tool_use_response()
    provider, mock_client = _make_provider_with_mock(response)

    shots = [
        ("153074915_checklist_267699_c145_0_09_04_2026 18_03_00.jpeg", b"\xff\xd8"),
        ("153074915_checklist_267699_c145_0_09_04_2026 18_03_23.jpeg", b"\xff\xd8"),
    ]

    provider.classify_image(
        image_filename="153269005_checklist_276800_c145_4_10_04_2026 17_09_12.jpeg",
        image_bytes=b"\xff",
        field_names=["c0", "c145"],
        shots=shots,
    )

    messages = mock_client.messages.create.call_args.kwargs["messages"]
    payload = json.dumps(messages, default=str)
    assert "Exemplo do campo c145 (1/2)" in payload, payload
    assert "Exemplo do campo c145 (2/2)" in payload, payload


@pytest.mark.unit
def test_classify_image_tool_schema_usa_enum_com_field_names() -> None:
    """O tool emit_classification deve ter enum=field_names para forçar vocabulário."""
    response = _make_tool_use_response()
    provider, mock_client = _make_provider_with_mock(response)

    provider.classify_image(
        image_filename="img.jpeg",
        image_bytes=b"\xff",
        field_names=["c0", "c3", "c145"],
    )

    tools = mock_client.messages.create.call_args.kwargs["tools"]
    schema = tools[0]["input_schema"]["properties"]["field_name"]
    assert schema.get("enum") == ["c0", "c3", "c145"]


@pytest.mark.unit
def test_classify_image_redimensiona_imagem_para_max_1024() -> None:
    """Imagens > 1024px no lado maior devem ser reduzidas antes do b64 encode.

    Reduz tokens de input do Anthropic (~36% economia em 1280→1024).
    """
    import base64
    import io
    from PIL import Image

    big = Image.new("RGB", (2000, 1500), color="red")
    buf = io.BytesIO()
    big.save(buf, format="JPEG")
    big_bytes = buf.getvalue()

    response = _make_tool_use_response()
    provider, mock_client = _make_provider_with_mock(response)

    provider.classify_image(
        image_filename="img.jpeg", image_bytes=big_bytes, field_names=["c0"]
    )

    messages = mock_client.messages.create.call_args.kwargs["messages"]
    image_block = next(b for b in messages[0]["content"] if b["type"] == "image")
    decoded = base64.standard_b64decode(image_block["source"]["data"])
    with Image.open(io.BytesIO(decoded)) as resized:
        assert max(resized.size) == 1024, f"esperado max(size)=1024, recebido {resized.size}"


@pytest.mark.unit
def test_classify_image_nao_redimensiona_imagem_ja_pequena() -> None:
    """Imagens ≤ 1024px ficam intactas (não amplia)."""
    import base64
    import io
    from PIL import Image

    small = Image.new("RGB", (800, 600), color="blue")
    buf = io.BytesIO()
    small.save(buf, format="JPEG")
    small_bytes = buf.getvalue()

    response = _make_tool_use_response()
    provider, mock_client = _make_provider_with_mock(response)

    provider.classify_image(
        image_filename="img.jpeg", image_bytes=small_bytes, field_names=["c0"]
    )

    messages = mock_client.messages.create.call_args.kwargs["messages"]
    image_block = next(b for b in messages[0]["content"] if b["type"] == "image")
    decoded = base64.standard_b64decode(image_block["source"]["data"])
    with Image.open(io.BytesIO(decoded)) as kept:
        assert kept.size == (800, 600), f"imagem pequena foi alterada: {kept.size}"


@pytest.mark.unit
def test_classify_image_usa_max_tokens_256_para_economia() -> None:
    """classify_image envia max_tokens=256 (4 campos do tool não precisam de mais)."""
    response = _make_tool_use_response()
    provider, mock_client = _make_provider_with_mock(response)

    provider.classify_image(
        image_filename="img.jpeg", image_bytes=b"\xff", field_names=["c0"]
    )

    assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 256


@pytest.mark.unit
def test_classify_image_usa_tool_choice_forcado() -> None:
    """classify_image usa tool_choice com tipo 'tool' e nome 'emit_classification'."""
    response = _make_tool_use_response()
    provider, mock_client = _make_provider_with_mock(response)

    provider.classify_image(
        image_filename="img.jpeg",
        image_bytes=b"\xff",
        field_names=["c0"],
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"]["type"] == "tool"
    assert call_kwargs["tool_choice"]["name"] == "emit_classification"


# ── Retry ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_retenta_em_rate_limit_error() -> None:
    """RateLimitError (429) aciona retry via tenacity, recuperando na 3ª tentativa."""
    import anthropic as anthropic_sdk

    good_response = _make_tool_use_response(field_name="c0", confidence=0.90)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        anthropic_sdk.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        ),
        anthropic_sdk.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        ),
        good_response,
    ]
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", _client=mock_client)

    result = provider.classify_image(
        image_filename="img.jpeg",
        image_bytes=b"\xff",
        field_names=["c0"],
    )

    assert result.field_name == "c0"
    assert mock_client.messages.create.call_count == 3


@pytest.mark.unit
def test_classify_image_falha_apos_3_tentativas() -> None:
    """Após 3 falhas consecutivas de RateLimitError, exceção é propagada."""
    import anthropic as anthropic_sdk
    from tenacity import RetryError

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = anthropic_sdk.RateLimitError(
        message="rate limit",
        response=MagicMock(status_code=429, headers={}),
        body={},
    )
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", _client=mock_client)

    with pytest.raises((anthropic_sdk.RateLimitError, RetryError)):
        provider.classify_image(
            image_filename="img.jpeg",
            image_bytes=b"\xff",
            field_names=["c0"],
        )

    assert mock_client.messages.create.call_count == 3


# ── Token logging ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_loga_tokens_de_uso(capfd: pytest.CaptureFixture[str]) -> None:
    """classify_image loga input/output/cache tokens após chamada bem-sucedida."""
    import logging

    response = _make_tool_use_response(
        cache_read_tokens=500,
        cache_write_tokens=100,
    )
    provider, _ = _make_provider_with_mock(response)

    with patch("app.services.llm_provider._log") as mock_log:
        provider.classify_image(
            image_filename="img.jpeg",
            image_bytes=b"\xff",
            field_names=["c0"],
        )
        # Deve ter chamado algum método de log com dados de token
        assert mock_log.info.called or mock_log.debug.called


# ── generate_report ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_generate_report_retorna_string_markdown() -> None:
    """generate_report retorna string não-vazia com o conteúdo do template preenchido."""
    text_response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "# Relatório\n\nChecklist 276800\n\nForam analisadas 2 imagens."
    text_response.content = [text_block]
    text_response.usage = MagicMock(input_tokens=50, output_tokens=30)
    type(text_response.usage).cache_read_input_tokens = property(lambda self: 0)
    type(text_response.usage).cache_creation_input_tokens = property(lambda self: 0)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = text_response
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", _client=mock_client)

    result = provider.generate_report(
        classifications=[],
        checklist_meta={"checklist_id": "276800"},
        template="# Relatório\n",
    )

    assert isinstance(result, str)
    assert len(result) > 0


# ── Provider factory ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_provider_retorna_anthropic_quando_llm_provider_e_anthropic() -> None:
    """_make_provider instancia AnthropicProvider quando LLM_PROVIDER=anthropic."""
    from app.core.config import Settings
    from app.services.orchestrator import _make_provider

    settings = Settings(
        llm_provider="anthropic",
        anthropic_api_key="sk-test-key",  # type: ignore[arg-type]
    )
    provider = _make_provider(settings)
    assert isinstance(provider, AnthropicProvider)


@pytest.mark.unit
def test_make_provider_levanta_value_error_sem_api_key() -> None:
    """_make_provider levanta ValueError se LLM_PROVIDER=anthropic mas sem chave."""
    from app.core.config import Settings
    from app.services.orchestrator import _make_provider

    settings = Settings(llm_provider="anthropic", anthropic_api_key=None)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _make_provider(settings)


# ── Usage accumulation (IAVS-049) ─────────────────────────────────────────────


@pytest.mark.unit
def test_classify_image_acumula_usage() -> None:
    """Após classify_image, accumulated_usage.input_tokens reflete tokens da chamada."""
    from app.services.cost_calculator import LLMUsage

    response = _make_tool_use_response(cache_read_tokens=50, cache_write_tokens=200)
    provider, _ = _make_provider_with_mock(response)

    provider.classify_image(
        image_filename="img.jpeg",
        image_bytes=b"\xff\xd8\xff",
        field_names=["c0"],
    )

    usage = provider.accumulated_usage
    assert isinstance(usage, LLMUsage)
    assert usage.input_tokens == 100  # _make_tool_use_response usa 100
    assert usage.output_tokens == 20
    assert usage.cache_read_tokens == 50
    assert usage.cache_creation_tokens == 200


@pytest.mark.unit
def test_usage_acumula_entre_chamadas() -> None:
    """Chamadas múltiplas somam tokens no accumulated_usage."""
    response = _make_tool_use_response()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-4-6", _client=mock_client)

    provider.classify_image("a.jpeg", b"\xff\xd8\xff", ["c0"])
    provider.classify_image("b.jpeg", b"\xff\xd8\xff", ["c0"])

    assert provider.accumulated_usage.input_tokens == 200  # 100 + 100
    assert provider.accumulated_usage.output_tokens == 40   # 20 + 20


# ── IAVS-045: REPORT_MODEL via settings ───────────────────────────────────────


def _make_text_response() -> MagicMock:
    """Cria resposta mock com bloco de texto (para generate_report)."""
    response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "# Relatório gerado"
    response.content = [text_block]
    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 50
    type(usage).cache_read_input_tokens = property(lambda self: 0)
    type(usage).cache_creation_input_tokens = property(lambda self: 0)
    response.usage = usage
    return response


@pytest.mark.unit
def test_generate_report_usa_report_model_configurado() -> None:
    """generate_report deve usar o report_model do provider, não o modelo de classificação."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_text_response()

    provider = AnthropicProvider(
        api_key="sk-test",
        model="claude-sonnet-4-6",
        report_model="claude-haiku-4-5",
        _client=mock_client,
    )

    provider.generate_report([], {"checklist_id": "276800"}, "# Template")

    call_kwargs = mock_client.messages.create.call_args
    model_used = call_kwargs.kwargs.get("model") or call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("model")
    # Extrai o model do kwargs da chamada
    assert mock_client.messages.create.called
    called_model = mock_client.messages.create.call_args[1].get("model") or mock_client.messages.create.call_args[0][0]
    assert called_model == "claude-haiku-4-5", f"Esperado haiku, usado: {called_model}"


@pytest.mark.unit
def test_generate_report_usa_modelo_classify_quando_sem_report_model() -> None:
    """Sem report_model configurado, generate_report usa o modelo padrão."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_text_response()

    provider = AnthropicProvider(
        api_key="sk-test",
        model="claude-sonnet-4-6",
        _client=mock_client,
    )

    provider.generate_report([], {"checklist_id": "276800"}, "# Template")

    called_model = mock_client.messages.create.call_args[1].get("model")
    assert called_model == "claude-sonnet-4-6"
