"""Configuração da aplicação via Pydantic Settings.

A configuração é lida do ambiente uma única vez no boot. Variáveis essenciais
são obrigatórias; credenciais de integrações externas (Dropbox, provedores de
IA) são opcionais e validadas no ponto de uso pelo serviço correspondente.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Defaults inseguros — proibidos em produção (ver _validar_producao).
_DEFAULT_SESSION_SECRET = "dev-session-secret-change-in-production!"  # noqa: S105
_DEFAULT_POSTGRES_PASSWORD = "changeme"  # noqa: S105


class AppEnv(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Settings raiz lida das variáveis de ambiente.

    Usa-se ``model_config`` em vez do antigo ``Config`` (Pydantic v2).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: AppEnv = AppEnv.DEVELOPMENT
    app_name: str = "tecnogera-ia-visual-api"
    app_version: str = "0.1.0"
    log_level: LogLevel = "INFO"

    api_host: str = "0.0.0.0"  # noqa: S104  bind para Docker
    api_port: int = Field(default=8000, ge=1, le=65535)

    cors_allow_origins: list[str] = Field(default_factory=lambda: ["*"])

    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "ia_visual"
    postgres_user: str = "ia_visual"
    postgres_password: SecretStr = SecretStr(_DEFAULT_POSTGRES_PASSWORD)

    redis_host: str = "redis"
    redis_port: int = Field(default=6379, ge=1, le=65535)

    dropbox_app_key: SecretStr | None = None
    dropbox_app_secret: SecretStr | None = None
    dropbox_refresh_token: SecretStr | None = None
    # Access token de curta duração (~4h). Use só para testes rápidos;
    # produção deve usar refresh_token.
    dropbox_access_token: SecretStr | None = None
    dropbox_root_path: str = "/Sisloc"
    dropbox_reports_path: str = "/comparativo_de_imagem"
    dropbox_local_cache_dir: str = "/tmp/checklists"  # noqa: S108  caminho dentro do container, configurável

    azure_cv_endpoint: str | None = None
    azure_cv_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"

    llm_provider: str = "fake"
    llm_confidence_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    llm_inconclusive_floor: float = Field(default=0.40, ge=0.0, le=1.0)

    pipeline_timeout_seconds: int = Field(default=1800, ge=1)
    pipeline_api_key: SecretStr | None = None
    batch_min_images: int = Field(default=30, ge=1)
    emit_quality_score: bool = False
    report_generator: str = "hybrid"
    report_model: str = "claude-haiku-4-5"

    alert_failed_jobs_threshold: int = Field(default=3, ge=1)
    alert_email_to: str | None = None

    session_secret: SecretStr = SecretStr(_DEFAULT_SESSION_SECRET)

    event_queue_concurrency: int = Field(default=30, ge=1)
    event_queue_max_retries: int = Field(default=3, ge=0)
    dropbox_avarias_path: str = "/Avarias"
    dropbox_annotated_path: str = "/Avarias/_anotados"

    @field_validator(
        "dropbox_app_key",
        "dropbox_app_secret",
        "dropbox_refresh_token",
        "dropbox_access_token",
        "azure_cv_endpoint",
        "azure_cv_key",
        "anthropic_api_key",
        "openai_api_key",
        "pipeline_api_key",
        mode="before",
    )
    @classmethod
    def _empty_string_para_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @model_validator(mode="after")
    def _validar_producao(self) -> Settings:
        """Recusa o boot em produção com segredos default / config insegura.

        Documentação não bastou (ver auditoria): a checagem é fail-safe no boot.
        """
        if self.app_env is not AppEnv.PRODUCTION:
            return self
        problemas: list[str] = []
        if self.session_secret.get_secret_value() == _DEFAULT_SESSION_SECRET:
            problemas.append("SESSION_SECRET usa o default inseguro do código")
        if self.postgres_password.get_secret_value() == _DEFAULT_POSTGRES_PASSWORD:
            problemas.append("POSTGRES_PASSWORD ainda é 'changeme'")
        if self.pipeline_api_key is None:
            problemas.append("PIPELINE_API_KEY é obrigatória em produção")
        if self.cors_allow_origins == ["*"]:
            problemas.append("CORS_ALLOW_ORIGINS não pode ser '*' em produção")
        if problemas:
            raise ValueError(
                "configuração inválida para APP_ENV=production: " + "; ".join(problemas)
            )
        return self

    @property
    def database_url(self) -> str:
        pwd = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.app_env is AppEnv.DEVELOPMENT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna instância única de ``Settings`` (cache de processo)."""
    return Settings()
