"""Testes puros do SemanticValidator — IAVS-048."""

from __future__ import annotations

import pytest

from app.services.semantic_validator import cosine_similarity, cosine_similarity_raw


@pytest.mark.unit
def test_vetores_identicos_retornam_1() -> None:
    """Vetores idênticos têm similaridade cosseno = 1.0."""
    vec = [1.0, 0.5, 0.25]
    result = cosine_similarity_raw(vec, vec)
    assert result == pytest.approx(1.0)


@pytest.mark.unit
def test_vetores_ortogonais_retornam_0() -> None:
    """Vetores ortogonais têm similaridade cosseno = 0.0."""
    result = cosine_similarity_raw([1.0, 0.0], [0.0, 1.0])
    assert result == pytest.approx(0.0)


@pytest.mark.unit
def test_cosine_similarity_usa_embed_fn_injetavel() -> None:
    """cosine_similarity aceita _embed_fn para testes sem OpenAI."""
    calls: list[str] = []

    def fake_embed(text: str) -> list[float]:
        calls.append(text)
        return [1.0, 0.0] if text == "a" else [0.0, 1.0]

    result = cosine_similarity("a", "b", _embed_fn=fake_embed)
    assert result == pytest.approx(0.0)
    assert calls == ["a", "b"]


@pytest.mark.unit
def test_cosine_similarity_sem_configuracao_levanta_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cosine_similarity sem openai instalado ou OPENAI_API_KEY levanta RuntimeError."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        cosine_similarity("texto a", "texto b")


@pytest.mark.unit
def test_cosine_similarity_provider_desconhecido_levanta_value_error() -> None:
    """cosine_similarity com provider desconhecido levanta ValueError."""
    with pytest.raises(ValueError, match="provider"):
        cosine_similarity("a", "b", provider="desconhecido")


@pytest.mark.unit
def test_cosine_similarity_vetores_identicos_via_embed_fn() -> None:
    """cosine_similarity com embed_fn que retorna vetores idênticos = 1.0."""
    result = cosine_similarity("x", "x", _embed_fn=lambda t: [0.3, 0.4])
    assert result == pytest.approx(1.0)
