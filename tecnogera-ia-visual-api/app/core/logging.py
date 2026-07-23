"""Configuração de logging estruturado em JSON via structlog.

A configuração é idempotente: chamar ``configure_logging`` mais de uma vez não
duplica processadores. Em ambiente ``development`` o output usa renderização
amigável; nos demais ambientes usa JSON.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars

from app.core.config import AppEnv, Settings, get_settings

_configured: bool = False


def configure_logging(settings: Settings | None = None) -> None:
    """Configura logging stdlib + structlog conforme settings.

    Reentrante: chamadas subsequentes apenas ajustam o nível.
    """
    global _configured  # noqa: PLW0603

    cfg = settings or get_settings()
    level = getattr(logging, cfg.log_level)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    if _configured:
        return

    processors: list[Any] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if cfg.app_env is AppEnv.DEVELOPMENT:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    """Atalho para obter um logger structlog (lazy-bound)."""
    return structlog.get_logger(name) if name else structlog.get_logger()
