"""Testes do limitador de rate limiting em memória — ticket usuarios-portal/03."""

from __future__ import annotations

import pytest

from app.core import ratelimit as ratelimit_module
from app.core.ratelimit import RateLimitPair, SlidingWindowLimiter


class _FakeClock:
    """Relógio monotônico controlável — evita testes com time.sleep de verdade."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr(ratelimit_module.time, "monotonic", clock)
    return clock


# ── SlidingWindowLimiter ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_limiter_libera_abaixo_do_limite(fake_clock: _FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=60)
    assert limiter.retry_after("k") is None
    limiter.record_failure("k")
    limiter.record_failure("k")
    assert limiter.retry_after("k") is None  # 2 falhas, limite é 3


@pytest.mark.unit
def test_limiter_bloqueia_no_limite(fake_clock: _FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.record_failure("k")
    retry_after = limiter.retry_after("k")
    assert retry_after is not None
    assert retry_after > 0


@pytest.mark.unit
def test_limiter_chaves_diferentes_sao_independentes(fake_clock: _FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_attempts=2, window_seconds=60)
    limiter.record_failure("a")
    limiter.record_failure("a")
    assert limiter.retry_after("a") is not None
    assert limiter.retry_after("b") is None  # chave "b" nunca falhou


@pytest.mark.unit
def test_limiter_janela_expira_e_libera(fake_clock: _FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_attempts=2, window_seconds=60)
    limiter.record_failure("k")
    limiter.record_failure("k")
    assert limiter.retry_after("k") is not None

    fake_clock.advance(61)  # passou da janela de 60s

    assert limiter.retry_after("k") is None


@pytest.mark.unit
def test_limiter_reset_limpa_o_rastro(fake_clock: _FakeClock) -> None:
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)
    limiter.record_failure("k")
    assert limiter.retry_after("k") is not None
    limiter.reset("k")
    assert limiter.retry_after("k") is None


@pytest.mark.unit
def test_limiter_rejeita_parametros_invalidos() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(max_attempts=0, window_seconds=60)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(max_attempts=1, window_seconds=0)


# ── RateLimitPair ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_pair_bloqueia_por_identidade(fake_clock: _FakeClock) -> None:
    pair = RateLimitPair(
        identity=SlidingWindowLimiter(2, 60),
        origin=SlidingWindowLimiter(100, 60),
    )
    pair.record_failure(identity_key="a@b.com", origin_key="1.1.1.1")
    pair.record_failure(identity_key="a@b.com", origin_key="2.2.2.2")  # origem MUDA
    retry = pair.retry_after(identity_key="a@b.com", origin_key="3.3.3.3")
    assert retry is not None  # bloqueado pela IDENTIDADE, mesmo com origem nova


@pytest.mark.unit
def test_pair_bloqueia_por_origem(fake_clock: _FakeClock) -> None:
    pair = RateLimitPair(
        identity=SlidingWindowLimiter(100, 60),
        origin=SlidingWindowLimiter(2, 60),
    )
    pair.record_failure(identity_key="a@b.com", origin_key="1.1.1.1")
    pair.record_failure(identity_key="z@z.com", origin_key="1.1.1.1")  # identidade MUDA
    retry = pair.retry_after(identity_key="novo@b.com", origin_key="1.1.1.1")
    assert retry is not None  # bloqueado pela ORIGEM, mesmo com identidade nova


@pytest.mark.unit
def test_pair_sucesso_nunca_soma_tentativa(fake_clock: _FakeClock) -> None:
    """Critério de aceite: login legítimo repetido não pode ser bloqueado."""
    pair = RateLimitPair(
        identity=SlidingWindowLimiter(2, 60),
        origin=SlidingWindowLimiter(2, 60),
    )
    for _ in range(50):
        pair.record_success(identity_key="a@b.com")
        assert pair.retry_after(identity_key="a@b.com", origin_key="1.1.1.1") is None


@pytest.mark.unit
def test_pair_sucesso_limpa_so_identidade_nao_origem(fake_clock: _FakeClock) -> None:
    """Sucesso reseta a identidade que acertou, mas não a origem (IP).

    Cenário: usuário erra a senha duas vezes e acerta na terceira. A conta
    dele fica livre para tentar de novo (identidade resetada) — mas o IP, que
    pode ser compartilhado (NAT de escritório), continua "sujo" para
    qualquer OUTRA identidade tentando a partir dali.
    """
    pair = RateLimitPair(
        identity=SlidingWindowLimiter(5, 60),
        origin=SlidingWindowLimiter(2, 60),
    )
    pair.record_failure(identity_key="user@b.com", origin_key="1.1.1.1")
    pair.record_failure(identity_key="user@b.com", origin_key="1.1.1.1")
    assert pair.retry_after(identity_key="user@b.com", origin_key="1.1.1.1") is not None

    pair.record_success(identity_key="user@b.com")

    # a própria conta, de outro IP, está livre — identidade foi resetada
    assert pair.retry_after(identity_key="user@b.com", origin_key="9.9.9.9") is None
    # mas o IP 1.1.1.1 continua bloqueado para QUALQUER outra identidade
    assert pair.retry_after(identity_key="outro@b.com", origin_key="1.1.1.1") is not None
