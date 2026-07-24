"""Rate limiter em memória para proteção contra brute-force no login — IAVS-030.

Janela deslizante simples, thread-safe, sem dependências externas. Adequado
para um único processo/replica da API (o deploy atual roda um container).
Para múltiplas réplicas, migrar o backend de contagem para Redis.

Nota de segurança: o bloqueio é temporário (expira ao fim da janela), não um
lockout permanente — isso limita o brute-force sem permitir que um atacante
deixe uma conta permanentemente travada (DoS por lockout).
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Conta tentativas por chave numa janela deslizante de tempo."""

    def __init__(self, *, max_attempts: int, window_seconds: float) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune_locked(self, key: str, now: float) -> list[float]:
        """Remove hits fora da janela. O chamador deve segurar o lock."""
        recent = [t for t in self._hits.get(key, ()) if now - t < self._window]
        if recent:
            self._hits[key] = recent
        else:
            self._hits.pop(key, None)
        return recent

    def is_blocked(self, key: str) -> bool:
        """True se ``key`` já atingiu o limite de tentativas na janela atual."""
        now = time.monotonic()
        with self._lock:
            return len(self._prune_locked(key, now)) >= self._max

    def register_failure(self, key: str) -> None:
        """Registra uma tentativa falha para ``key``."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(key, now)
            self._hits.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        """Zera o contador de ``key`` (ex.: após login bem-sucedido)."""
        with self._lock:
            self._hits.pop(key, None)
