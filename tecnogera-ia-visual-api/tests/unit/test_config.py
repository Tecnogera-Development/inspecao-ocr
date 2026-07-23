"""Testes da configuração via Pydantic Settings (IAVS-002)."""

from __future__ import annotations

import pytest

from app.core.config import AppEnv, Settings


@pytest.mark.unit
def test_settings_valores_default() -> None:
    cfg = Settings(_env_file=None)
    assert cfg.app_env is AppEnv.DEVELOPMENT
    assert cfg.app_name == "tecnogera-ia-visual-api"
    assert cfg.api_port == 8000
    assert cfg.is_development is True
    assert cfg.is_production is False


def _prod_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Define os segredos obrigatórios de produção (ver _validar_producao)."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "prod-session-secret-32-chars-min!")
    monkeypatch.setenv("POSTGRES_PASSWORD", "prod-strong-password")
    monkeypatch.setenv("PIPELINE_API_KEY", "prod-pipeline-key")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://inspecao.polarisprod.com.br"]')


@pytest.mark.unit
def test_settings_carrega_de_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("API_PORT", "9000")
    cfg = Settings(_env_file=None)
    assert cfg.app_env is AppEnv.PRODUCTION
    assert cfg.is_production is True
    assert cfg.log_level == "WARNING"
    assert cfg.api_port == 9000


# ── guards de produção (auditoria de segurança) ───────────────────────────────


@pytest.mark.unit
def test_producao_rejeita_session_secret_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch)
    monkeypatch.delenv("SESSION_SECRET", raising=False)  # volta ao default inseguro
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        Settings(_env_file=None)


@pytest.mark.unit
def test_producao_rejeita_postgres_password_changeme(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_PASSWORD", "changeme")
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        Settings(_env_file=None)


@pytest.mark.unit
def test_producao_exige_pipeline_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch)
    monkeypatch.delenv("PIPELINE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="PIPELINE_API_KEY"):
        Settings(_env_file=None)


@pytest.mark.unit
def test_producao_rejeita_cors_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_env(monkeypatch)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["*"]')
    with pytest.raises(ValueError, match="CORS_ALLOW_ORIGINS"):
        Settings(_env_file=None)


@pytest.mark.unit
def test_desenvolvimento_permite_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fora de produção, os defaults de dev continuam válidos (sem travar o boot)."""
    cfg = Settings(_env_file=None, app_env=AppEnv.DEVELOPMENT)
    assert cfg.is_production is False


@pytest.mark.unit
def test_settings_porta_invalida_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "70000")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


@pytest.mark.unit
def test_settings_credenciais_opcionais_default_none() -> None:
    cfg = Settings(_env_file=None)
    assert cfg.dropbox_app_key is None
    assert cfg.azure_cv_key is None
    assert cfg.anthropic_api_key is None
    assert cfg.openai_api_key is None
