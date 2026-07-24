"""Testes do rate limiter de login — IAVS-030."""

from __future__ import annotations

import pytest

from app.services.rate_limit import RateLimiter


@pytest.mark.unit
def test_bloqueia_apos_atingir_o_limite() -> None:
    rl = RateLimiter(max_attempts=3, window_seconds=300)
    key = "user@x.com"

    assert rl.is_blocked(key) is False
    for _ in range(3):
        assert rl.is_blocked(key) is False
        rl.register_failure(key)
    # 3 falhas registradas → agora bloqueado.
    assert rl.is_blocked(key) is True


@pytest.mark.unit
def test_reset_libera_a_chave() -> None:
    rl = RateLimiter(max_attempts=2, window_seconds=300)
    key = "user@x.com"
    rl.register_failure(key)
    rl.register_failure(key)
    assert rl.is_blocked(key) is True

    rl.reset(key)
    assert rl.is_blocked(key) is False


@pytest.mark.unit
def test_chaves_sao_independentes() -> None:
    rl = RateLimiter(max_attempts=1, window_seconds=300)
    rl.register_failure("a@x.com")
    assert rl.is_blocked("a@x.com") is True
    assert rl.is_blocked("b@x.com") is False


@pytest.mark.unit
def test_janela_expira_libera_a_chave() -> None:
    # window_seconds=0 → qualquer hit já está fora da janela na próxima checagem.
    rl = RateLimiter(max_attempts=1, window_seconds=0)
    rl.register_failure("user@x.com")
    assert rl.is_blocked("user@x.com") is False
