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
    # Sem chave de LLM a produção não sobe (ticket mvp-c54-c57/08): o provider
    # cairia no fake e produziria laudo fictício indistinguível de um real.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-prod-fake-para-teste")


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
def test_producao_recusa_subir_sem_chave_de_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem chave, o provider cai no fake — e laudo fictício passa por real.

    Guarda-corpo do ticket mvp-c54-c57/08. É o pior modo de falha do projeto:
    o erro chega ao operador com cara de resultado válido. Não há escape hatch
    de propósito — não existe uso legítimo de resultado fictício em produção.
    """
    _prod_env(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="nenhuma chave de LLM configurada"):
        Settings(_env_file=None)


@pytest.mark.unit
def test_producao_aceita_so_a_chave_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Anthropic é plano B validado — não é fake, então a produção sobe."""
    _prod_env(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-prod")

    cfg = Settings(_env_file=None)

    assert cfg.llm_provider_efetivo == "anthropic"


@pytest.mark.unit
def test_chave_vazia_conta_como_ausente_em_producao(monkeypatch: pytest.MonkeyPatch) -> None:
    """``OPENAI_API_KEY=`` no .env é o caso real que quebrou o ticket 13."""
    _prod_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    with pytest.raises(ValueError, match="nenhuma chave de LLM configurada"):
        Settings(_env_file=None)


@pytest.mark.unit
def test_desenvolvimento_sobe_sem_chave_de_llm() -> None:
    """O guarda é só de produção: dev e CI rodam com FakeLLMProvider."""
    cfg = Settings(_env_file=None, app_env=AppEnv.DEVELOPMENT)

    assert cfg.llm_provider_efetivo == "fake"


@pytest.mark.unit
def test_freios_de_gasto_tem_defaults_conservadores() -> None:
    """Kill switch fechado, teto por rodada e orçamento mensal — ticket 08."""
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST)

    assert cfg.llm_dispatch_enabled is False
    assert cfg.llm_max_calls_per_run == 60
    assert cfg.llm_monthly_budget_usd == 25.0


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
