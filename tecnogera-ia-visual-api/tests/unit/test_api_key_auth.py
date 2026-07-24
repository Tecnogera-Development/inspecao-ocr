"""Testes do fail-closed da autenticação por X-API-Key — IAVS-008."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import AppEnv, Settings
from app.routers.pipeline import verify_api_key


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


@pytest.mark.unit
@pytest.mark.parametrize("env", [AppEnv.DEVELOPMENT, AppEnv.TEST])
def test_sem_chave_libera_em_dev_e_test(env: AppEnv) -> None:
    settings = _settings(app_env=env)  # pipeline_api_key = None
    assert verify_api_key(None, settings) is None


@pytest.mark.unit
def test_sem_chave_bloqueia_em_staging_com_503() -> None:
    settings = _settings(app_env=AppEnv.STAGING)  # pipeline_api_key = None
    with pytest.raises(HTTPException) as exc:
        verify_api_key(None, settings)
    assert exc.value.status_code == 503


@pytest.mark.unit
def test_chave_errada_retorna_401() -> None:
    settings = _settings(app_env=AppEnv.STAGING, pipeline_api_key="segredo")
    with pytest.raises(HTTPException) as exc:
        verify_api_key("errada", settings)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_chave_ausente_com_chave_configurada_retorna_401() -> None:
    settings = _settings(app_env=AppEnv.STAGING, pipeline_api_key="segredo")
    with pytest.raises(HTTPException) as exc:
        verify_api_key(None, settings)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_chave_correta_passa() -> None:
    settings = _settings(app_env=AppEnv.STAGING, pipeline_api_key="segredo")
    assert verify_api_key("segredo", settings) is None
