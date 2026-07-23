"""SemanticValidator — cosine similarity entre embeddings de texto.

Interface pública:
  cosine_similarity_raw(vec_a, vec_b) -> float   — cálculo puro, sem I/O
  cosine_similarity(text_a, text_b, *, provider) -> float — via embedding API

O provider "openai" usa text-embedding-3-small.
OPENAI_API_KEY ausente levanta RuntimeError (falha grácil: não quebra o
processo de classificação, apenas o score de validação semântica).
"""

from __future__ import annotations

import math
import os
from typing import Any


def cosine_similarity_raw(vec_a: list[float], vec_b: list[float]) -> float:
    """Retorna a similaridade cosseno entre dois vetores de mesmo tamanho."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(x * x for x in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _openai_embedding(text: str) -> list[float]:
    """Chama OpenAI text-embedding-3-small e retorna o vetor de embedding."""
    try:
        import openai  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "Pacote 'openai' não instalado. Execute: pip install openai"
        ) from exc

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY não configurada. Defina a variável de ambiente antes de usar "
            "cosine_similarity com provider='openai'."
        )

    client = openai.OpenAI(api_key=api_key)
    response = client.embeddings.create(model="text-embedding-3-small", input=text)
    return list(response.data[0].embedding)


def cosine_similarity(
    text_a: str,
    text_b: str,
    *,
    provider: str = "openai",
    _embed_fn: Any = None,
) -> float:
    """Retorna a similaridade cosseno entre embeddings de text_a e text_b.

    Args:
        text_a: Primeiro texto.
        text_b: Segundo texto.
        provider: Provider de embeddings (atualmente só "openai").
        _embed_fn: Injetável para testes; se fornecido, substitui o provider.

    Returns:
        Similaridade cosseno em [-1.0, 1.0].

    Raises:
        RuntimeError: OPENAI_API_KEY ausente ou pacote openai não instalado.
        ValueError: provider desconhecido.
    """
    if _embed_fn is not None:
        embed = _embed_fn
    elif provider == "openai":
        embed = _openai_embedding
    else:
        raise ValueError(f"provider {provider!r} não suportado; use 'openai'")

    vec_a = embed(text_a)
    vec_b = embed(text_b)
    return cosine_similarity_raw(vec_a, vec_b)
