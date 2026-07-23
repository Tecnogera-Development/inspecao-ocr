"""Testes da configuração de logging (IAVS-002)."""

from __future__ import annotations

import logging

import pytest

from app.core.config import AppEnv, Settings
from app.core.logging import configure_logging, get_logger


@pytest.mark.unit
def test_configure_logging_define_nivel() -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST, log_level="WARNING")
    configure_logging(cfg)
    assert logging.getLogger().level == logging.WARNING


@pytest.mark.unit
def test_get_logger_loga_sem_erro(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST, log_level="DEBUG")
    configure_logging(cfg)
    log = get_logger("teste")
    log.info("evento_de_teste", chave="valor")
    out = capsys.readouterr().out
    assert "evento_de_teste" in out


@pytest.mark.unit
def test_configure_logging_idempotente() -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST, log_level="INFO")
    configure_logging(cfg)
    configure_logging(cfg)
    assert logging.getLogger().level == logging.INFO
