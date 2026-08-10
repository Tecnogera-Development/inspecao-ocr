"""Calculadora de custo de chamadas LLM — IAVS-049.

Módulo stateless: dado modelo + tokens, retorna custo em USD.
Tabela de pricing em código; atualizar aqui quando o provedor mudar tarifas.

**Ticket mvp-c54-c57/08** acrescentou a família OpenAI. O preço do
``gpt-4.1-mini`` não é chute: bate com a medição real do ticket 15 — 12.517
tokens de entrada + 292 de saída em 3 chamadas custaram ≈US$ 0,0055, que é
exatamente ``12517×0,40/1e6 + 292×1,60/1e6``. É essa tabela que alimenta o teto
de orçamento em ``app/services/llm_budget.py``; um preço errado aqui vira um
freio que não freia.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Preços por MTok (1_000_000 tokens) em USD
# Anthropic: https://anthropic.com/pricing (verificado 2026-05)
# OpenAI:    https://openai.com/api/pricing (conferido contra a medição real
#            do ticket 15 — ver docstring do módulo)
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_creation": 3.75,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.10,
        "cache_creation": 1.25,
    },
    "claude-opus-4-7": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_creation": 18.75,
    },
    # ── OpenAI ────────────────────────────────────────────────────────────────
    # `cache_creation` = 0: na OpenAI o cache de prompt é automático e não se
    # cobra pela escrita, só o desconto na leitura.
    "gpt-4.1-mini": {
        "input": 0.40,
        "output": 1.60,
        "cache_read": 0.10,
        "cache_creation": 0.0,
    },
    "gpt-4.1-nano": {
        "input": 0.10,
        "output": 0.40,
        "cache_read": 0.025,
        "cache_creation": 0.0,
    },
    "gpt-4.1": {
        "input": 2.00,
        "output": 8.00,
        "cache_read": 0.50,
        "cache_creation": 0.0,
    },
    "gpt-4o-mini": {
        "input": 0.15,
        "output": 0.60,
        "cache_read": 0.075,
        "cache_creation": 0.0,
    },
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00,
        "cache_read": 1.25,
        "cache_creation": 0.0,
    },
}

_FALLBACK_MODEL = "claude-sonnet-4-6"
#: Modelo desconhecido de uma família conhecida cai no representante mais CARO
#: dela. Subestimar custo é o erro perigoso: o teto de orçamento deixaria passar
#: gasto que não contabilizou.
_FALLBACK_POR_FAMILIA: tuple[tuple[tuple[str, ...], str], ...] = (
    (("gpt-", "o1", "o3", "o4", "chatgpt"), "gpt-4o"),
    (("claude-",), _FALLBACK_MODEL),
)


def resolve_pricing_model(model: str) -> str:
    """Nome do modelo cuja tabela de preço será usada para ``model``.

    Exposto porque quem loga custo precisa dizer *qual* tarifa aplicou — um
    fallback silencioso é como um preço errado passa despercebido.
    """
    if model in _PRICING:
        return model
    nome = model.strip().lower()
    for prefixos, alvo in _FALLBACK_POR_FAMILIA:
        if any(nome.startswith(p) for p in prefixos):
            return alvo
    return _FALLBACK_MODEL


@dataclass
class LLMUsage:
    """Tokens acumulados de chamadas LLM para um job."""

    model: str
    input_tokens: int = field(default=0)
    output_tokens: int = field(default=0)
    cache_read_tokens: int = field(default=0)
    cache_creation_tokens: int = field(default=0)

    def accumulate(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_creation_tokens += cache_creation_tokens


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    *,
    batch_mode: bool = False,
) -> float:
    """Calcula custo estimado em USD para uma chamada LLM.

    Parâmetros:
        model: ID do modelo (ex: 'claude-sonnet-4-6').
        input_tokens: tokens de entrada (não-cache).
        output_tokens: tokens de saída.
        cache_read_tokens: tokens lidos do prompt cache.
        cache_creation_tokens: tokens escritos no prompt cache.
        batch_mode: se True, aplica desconto de 50% (Batch API — vale para
            Anthropic e OpenAI).

    Modelo desconhecido cai no representante mais caro da família (ver
    ``resolve_pricing_model``); sem família reconhecível, em claude-sonnet-4-6.
    """
    pricing = _PRICING[resolve_pricing_model(model)]
    cost = (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read_tokens * pricing["cache_read"] / 1_000_000
        + cache_creation_tokens * pricing["cache_creation"] / 1_000_000
    )
    if batch_mode:
        cost *= 0.5
    return round(cost, 6)
