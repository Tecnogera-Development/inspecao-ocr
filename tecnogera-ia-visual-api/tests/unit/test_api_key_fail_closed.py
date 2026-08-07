"""verify_api_key fail-closed — reconciliação de segurança sobre a v1.2.1.

Sem PIPELINE_API_KEY, o acesso só é liberado em development/test; em
staging/produção vira 503 (nunca fail-open). Chave comparada em tempo constante.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import AppEnv, Settings
from app.routers.events import _verify_api_key
from app.routers.pipeline import verify_api_key

pytestmark = pytest.mark.unit

# As duas guardas (pipeline e events) devem se comportar identicamente.
GUARDS = [verify_api_key, _verify_api_key]


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)


@pytest.mark.parametrize("guard", GUARDS)
@pytest.mark.parametrize("env", [AppEnv.DEVELOPMENT, AppEnv.TEST])
def test_sem_chave_libera_em_dev_e_test(guard, env: AppEnv) -> None:
    assert guard(None, _settings(app_env=env)) is None


@pytest.mark.parametrize("guard", GUARDS)
def test_sem_chave_bloqueia_em_staging_503(guard) -> None:
    with pytest.raises(HTTPException) as exc:
        guard(None, _settings(app_env=AppEnv.STAGING))
    assert exc.value.status_code == 503


@pytest.mark.parametrize("guard", GUARDS)
def test_chave_ausente_com_chave_configurada_401(guard) -> None:
    with pytest.raises(HTTPException) as exc:
        guard(None, _settings(app_env=AppEnv.STAGING, pipeline_api_key="segredo"))
    assert exc.value.status_code == 401


@pytest.mark.parametrize("guard", GUARDS)
def test_chave_errada_401(guard) -> None:
    with pytest.raises(HTTPException) as exc:
        guard("errada", _settings(app_env=AppEnv.STAGING, pipeline_api_key="segredo"))
    assert exc.value.status_code == 401


@pytest.mark.parametrize("guard", GUARDS)
def test_chave_correta_passa(guard) -> None:
    assert guard("segredo", _settings(app_env=AppEnv.STAGING, pipeline_api_key="segredo")) is None
