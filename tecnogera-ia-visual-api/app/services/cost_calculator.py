"""Calculadora de custo de chamadas LLM — IAVS-049.

Módulo stateless: dado modelo + tokens, retorna custo em USD.
Tabela de pricing em código; atualizar aqui quando a Anthropic mudar tarifas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Preços por MTok (1_000_000 tokens) em USD
# Fonte: https://anthropic.com/pricing (verificado 2026-05)
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
}

_FALLBACK_MODEL = "claude-sonnet-4-6"


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
        batch_mode: se True, aplica desconto de 50% (Anthropic Batch API).

    Modelos desconhecidos usam pricing de claude-sonnet-4-6 como fallback.
    """
    pricing = _PRICING.get(model, _PRICING[_FALLBACK_MODEL])
    cost = (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read_tokens * pricing["cache_read"] / 1_000_000
        + cache_creation_tokens * pricing["cache_creation"] / 1_000_000
    )
    if batch_mode:
        cost *= 0.5
    return round(cost, 6)
