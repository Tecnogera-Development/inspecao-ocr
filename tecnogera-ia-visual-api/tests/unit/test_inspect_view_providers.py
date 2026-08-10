"""``inspect_view`` nos três providers — ticket mvp-c54-c57/08.

Clientes são dublês: **nenhuma chamada de API real**. O que se verifica é o que
sai errado na integração de verdade — tool forçada, prompt certo, tokens lidos
do lugar certo (a OpenAI chama ``prompt_tokens``, a Anthropic ``input_tokens``)
e a escolha de provider num ponto só.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.config import AppEnv, Settings
from app.services import view_inspection as vi
from app.services.llm_provider import (
    AnthropicProvider,
    FakeLLMProvider,
    OpenAIProvider,
    get_llm_provider,
)

pytestmark = pytest.mark.unit

_LAUDO = {
    "processavel": True,
    "conteudo_observado": "lateral direita da cabine, portas fechadas",
    "vista_confere": True,
    "conformidade": "nao_conforme",
    "achados": [
        {
            "classe": "dano_visivel",
            "tipo_defeito": "corrosao_ferrugem",
            "severidade": 3,
            "local": "aresta do teto",
            "observacao": "mancha laranja com textura na junta",
            "confianca": 0.77,
        }
    ],
}


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(120, 120, 120)).save(buf, format="JPEG")
    return buf.getvalue()


# ── OpenAI ────────────────────────────────────────────────────────────────────


def _openai_client(laudo: dict[str, Any] | None = None) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=vi.TOOL_NAME,
                                arguments=json.dumps(laudo or _LAUDO),
                            )
                        )
                    ]
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=4378, completion_tokens=97),
    )
    return client


def test_openai_inspect_view_devolve_laudo_parseado() -> None:
    provider = OpenAIProvider(api_key="x", model="gpt-4.1-mini", _client=_openai_client())

    laudo = provider.inspect_view(_png(), "c54")

    assert laudo.campo == "c54"
    assert laudo.conformidade == "nao_conforme"
    assert laudo.achados[0].tipo_defeito == "corrosao_ferrugem"
    assert laudo.model_version == "gpt-4.1-mini"


def test_openai_le_tokens_do_campo_certo() -> None:
    """A OpenAI chama ``prompt_tokens``; ler ``input_tokens`` daria custo zero."""
    provider = OpenAIProvider(api_key="x", model="gpt-4.1-mini", _client=_openai_client())

    laudo = provider.inspect_view(_png(), "c54")

    assert laudo.input_tokens == 4378
    assert laudo.output_tokens == 97
    assert provider.accumulated_usage.input_tokens == 4378


def test_openai_forca_a_tool_e_manda_o_prompt_da_taxonomia() -> None:
    client = _openai_client()
    provider = OpenAIProvider(api_key="x", model="gpt-4.1-mini", _client=client)

    provider.inspect_view(_png(), "c56")

    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["tool_choice"]["function"]["name"] == vi.TOOL_NAME
    assert kwargs["tools"][0]["function"]["name"] == vi.TOOL_NAME
    assert kwargs["messages"][0]["content"] == vi.SYSTEM_PROMPT_V02
    # A vista declarada vai junto: sem ela o modelo não sabe o que esperar.
    assert "painel de comando" in kwargs["messages"][1]["content"][0]["text"]


def test_openai_manda_uma_imagem_so_por_chamada() -> None:
    """Uma chamada por vista: mandar as 3–4 juntas perderia a atribuição."""
    client = _openai_client()
    provider = OpenAIProvider(api_key="x", model="gpt-4.1-mini", _client=client)

    provider.inspect_view(_png(), "c54")

    blocos = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert sum(1 for b in blocos if b["type"] == "image_url") == 1


def test_openai_normaliza_laudo_incoerente() -> None:
    """Coerência é do ``parse_inspecao`` — o provider não pode contorná-la."""
    incoerente = {**_LAUDO, "processavel": False, "motivo_nao_processavel": "foto_escura"}
    provider = OpenAIProvider(
        api_key="x", model="gpt-4.1-mini", _client=_openai_client(incoerente)
    )

    laudo = provider.inspect_view(_png(), "c54")

    assert laudo.conformidade == "nao_processavel"
    assert laudo.achados == []


# ── Anthropic ─────────────────────────────────────────────────────────────────


def _anthropic_client() -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name=vi.TOOL_NAME, input=dict(_LAUDO))],
        usage=SimpleNamespace(
            input_tokens=3900,
            output_tokens=88,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )
    return client


def test_anthropic_inspect_view_funciona_como_plano_b() -> None:
    """Sem este método, cair no fallback viraria falha em toda vista."""
    client = _anthropic_client()
    provider = AnthropicProvider(api_key="x", model="claude-sonnet-4-6", _client=client)

    laudo = provider.inspect_view(_png(), "c55")

    assert laudo.conformidade == "nao_conforme"
    assert laudo.input_tokens == 3900
    assert laudo.output_tokens == 88
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": vi.TOOL_NAME}
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


# ── Fake ──────────────────────────────────────────────────────────────────────


def test_fake_devolve_conforme_e_se_identifica() -> None:
    """O ``model_version`` é como um laudo fictício é rastreável depois."""
    laudo = FakeLLMProvider().inspect_view(_png(), "c57")

    assert laudo.conformidade == "conforme"
    assert laudo.achados == []
    assert laudo.model_version == "fake-inspecao-1.0"
    assert laudo.input_tokens == 0


# ── escolha do provider ───────────────────────────────────────────────────────


def _cfg(**extra: Any) -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST, **extra)


def test_openai_tem_prioridade() -> None:
    assert isinstance(get_llm_provider(_cfg(openai_api_key="sk-x")), OpenAIProvider)


def test_anthropic_e_o_plano_b() -> None:
    cfg = _cfg(anthropic_api_key="sk-ant")
    assert isinstance(get_llm_provider(cfg), AnthropicProvider)


def test_sem_chave_nenhuma_cai_no_fake() -> None:
    assert isinstance(get_llm_provider(_cfg()), FakeLLMProvider)


def test_provider_efetivo_espelha_a_escolha_sem_instanciar() -> None:
    """O guarda-corpo de produção decide no boot, onde não dá para instanciar."""
    assert _cfg(openai_api_key="sk-x").llm_provider_efetivo == "openai"
    assert _cfg(anthropic_api_key="sk-a").llm_provider_efetivo == "anthropic"
    assert _cfg().llm_provider_efetivo == "fake"


def test_modelo_efetivo_acompanha_o_provider() -> None:
    assert _cfg(openai_api_key="sk-x").llm_model_efetivo == "gpt-4.1-mini"
    assert _cfg(anthropic_api_key="sk-a").llm_model_efetivo == "claude-sonnet-4-6"
    assert _cfg().llm_model_efetivo == "fake"


def test_event_tasks_reusa_o_ponto_unico() -> None:
    """Duplicar a escolha é como dois fluxos passam a usar providers diferentes."""
    from app.tasks.event_tasks import _get_llm_provider

    assert isinstance(_get_llm_provider(_cfg(openai_api_key="sk-x")), OpenAIProvider)
