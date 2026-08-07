"""Configuração da aplicação via Pydantic Settings.

A configuração é lida do ambiente uma única vez no boot. Variáveis essenciais
são obrigatórias; credenciais de integrações externas (Dropbox, provedores de
IA) são opcionais e validadas no ponto de uso pelo serviço correspondente.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 — Pydantic resolve a anotação em runtime
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

    # ── Controle de gasto de LLM (ticket mvp-c54-c57/08) ──────────────────────
    # A chave OpenAI é real e paga. Antes deste ticket não havia teto nenhum:
    # um backfill descuidado viraria fatura surpresa. Os três freios abaixo são
    # avaliados em ``app/services/llm_budget.py`` e valem para a esteira de
    # checklists (a única que despacha LLM automaticamente).
    #
    # Kill switch. Default **false**: a esteira sobe ingerindo e materializando
    # jobs sem gastar um centavo, e alguém precisa ligar o gasto de propósito.
    # O modo de falha inverso — subir gastando por engano — é o caro.
    llm_dispatch_enabled: bool = False
    # Teto de chamadas de LLM por rodada do cron. Calibrado sobre o parque
    # medido: ~371 checklists/mês × 3 vistas ≈ 1.113 imagens/mês ≈ 37/dia; com
    # cron de 30 min são 48 rodadas/dia, ~0,8 chamada por rodada. 60 é ~75× a
    # operação normal (não corta nada) e ainda limita o estrago de um loop a
    # ~US$ 0,12 por rodada.
    llm_max_calls_per_run: int = Field(default=60, ge=0)
    # Teto de orçamento do mês corrente, em USD. O custo REAL medido (soma de
    # ``checklist_view_results.cost_usd``) é comparado antes de cada chamada.
    # Custo medido do parque: ≈US$ 2/mês. 25 dá 12× de folga para picos e ainda
    # obriga uma decisão explícita antes de qualquer backfill grande (os 18.338
    # checklists com as 4 vistas custariam ~US$ 110).
    llm_monthly_budget_usd: float = Field(default=25.0, ge=0.0)
    # Quantos jobs `pending` uma rodada tenta processar. Segundo teto, do lado
    # do banco/Dropbox — o teto de chamadas continua sendo o freio de gasto.
    checklist_analysis_max_jobs_per_run: int = Field(default=25, ge=1)

    pipeline_timeout_seconds: int = Field(default=1800, ge=1)
    pipeline_api_key: SecretStr | None = None
    batch_min_images: int = Field(default=30, ge=1)
    emit_quality_score: bool = False
    report_generator: str = "hybrid"
    report_model: str = "claude-haiku-4-5"

    alert_failed_jobs_threshold: int = Field(default=3, ge=1)
    alert_email_to: str | None = None

    session_secret: SecretStr = SecretStr(_DEFAULT_SESSION_SECRET)

    # Acesso inicial do portal — se AMBOS definidos, o boot cria um admin
    # idempotentemente (ver app/main.py::_seed_initial_user). Opcionais: sem
    # eles, o primeiro admin é criado via `python -m app.cli create_user
    # --role admin`. Após o 1º login, troque a senha e remova estas variáveis.
    initial_admin_email: str | None = None
    initial_admin_password: SecretStr | None = None

    # ── Rate limiting de autenticação (ticket usuarios-portal/03) ────────────
    # Contador em memória do processo, NÃO Redis. Medido em docker-compose.yml
    # + Dockerfile: `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port",
    # "8000"]` sem `--workers` — nem em produção (Dockerfile) nem no override de
    # dev (docker-compose.override.yml) — o serviço `api` sobe como processo
    # único. Com 1 worker o contador em memória é exato: não há a fragmentação
    # N-worker que faria o limite efetivo virar N× (cada processo contando
    # separado). Redis (já usado pela fila Arq) resolveria isso, mas ao custo de
    # acoplar a autenticação à disponibilidade do Redis — sem ganho, dado que só
    # existe 1 worker. Reconsiderar SE `docker-compose.yml`/Dockerfile ganharem
    # `--workers`/réplicas do serviço `api` (ver app/core/ratelimit.py).
    login_rate_limit_identity_max_attempts: int = Field(default=5, ge=1)
    login_rate_limit_identity_window_seconds: int = Field(default=900, ge=1)
    login_rate_limit_origin_max_attempts: int = Field(default=20, ge=1)
    login_rate_limit_origin_window_seconds: int = Field(default=900, ge=1)

    # ── Rate limiting de POST /definir-senha (ticket usuarios-portal/02) ─────
    # Mesmo motor do login (app/core/ratelimit.py), par próprio: protege o
    # código de uso único da janela de primeira senha/reset, que é o único
    # segredo durante os 30 min (risco 1 do mapa). Defaults iguais aos do
    # login por simetria — o teto "duro" por código (que não decai com o
    # tempo) é o `MAX_PASSWORD_SETUP_ATTEMPTS` persistido em
    # `users.password_setup_attempts` (app/services/user_management.py); este
    # limitador é a segunda camada, contra tentativa rápida/distribuída.
    password_setup_rate_limit_identity_max_attempts: int = Field(default=5, ge=1)
    password_setup_rate_limit_identity_window_seconds: int = Field(default=900, ge=1)
    password_setup_rate_limit_origin_max_attempts: int = Field(default=20, ge=1)
    password_setup_rate_limit_origin_window_seconds: int = Field(default=900, ge=1)

    event_queue_concurrency: int = Field(default=30, ge=1)
    event_queue_max_retries: int = Field(default=3, ge=0)
    dropbox_avarias_path: str = "/Avarias"
    dropbox_annotated_path: str = "/Avarias/_anotados"

    # ── Ingestão agendada de checklists (ticket mvp-c54-c57/07) ───────────────
    # Cron de 30 min: varre /Sisloc por delta de cursor, filtra por formulário e
    # materializa pipeline_jobs. Ver app/services/checklist_ingestion.py.
    checklist_ingest_enabled: bool = True
    # Raiz no Dropbox; vazio herda `dropbox_root_path` (/Sisloc).
    checklist_ingest_root: str = ""
    # Marco de corte: arquivo com `server_modified` anterior a esta data é
    # ignorado. Sem valor, o corte é o próprio bootstrap do cursor (a ativação).
    checklist_ingest_since: date | None = None
    # Bootstrap: por padrão pega só o cursor "agora" (nenhuma entrada), o que
    # evita a varredura completa de /Sisloc — medida em 67 min, inviável num
    # cron de 30. Ligar só para backfill deliberado.
    checklist_ingest_bootstrap_full: bool = False
    # Teto de checklists avaliados por rodada (protege o SQL Server e o cron).
    checklist_ingest_max_checklists: int = Field(default=500, ge=1)
    # Por quantos dias um checklist incompleto continua sendo reavaliado a cada
    # rodada (fotos chegam em deltas diferentes; a linha do ERP pode atrasar).
    #
    # ⚠️ Desde o ticket 17 esta janela também governa os descartados por
    # `status_nao_concluido` — 14,8% dos F180/F038 estão `A Executar`/`A
    # Conferir`, e o que os traz de volta é a reavaliação, não uma foto nova.
    # Fechar o checklist no Sisloc depende de um humano: se a operação da
    # Tecnogera levar mais de 3 dias para conferir, AUMENTE este valor — o
    # checklist some da esteira em silêncio, e o único sintoma é volume menor
    # que o projetado (≈280/mês). Custo de aumentar: mais ids no SELECT em lote
    # por rodada. Zero custo de LLM.
    checklist_ingest_retry_days: int = Field(default=3, ge=0)

    # ── Backfill sob demanda (ticket mvp-c54-c57/11) ──────────────────────────
    # Teto de checklist_ids aceitos por requisição em POST /checklists/backfill.
    # É guarda-corpo de GASTO, não de performance: cada checklist aceito vira 3–4
    # chamadas de visão no despacho. 20 foi escolhido por caber inteiro numa
    # rodada de análise (`checklist_analysis_max_jobs_per_run`=25), de modo que o
    # operador vê o resultado do lote inteiro num ciclo em vez de a fila
    # transbordar para rodadas seguintes. Reprocessar centenas continua possível
    # — em lotes, cada um uma decisão explícita.
    checklist_backfill_max_ids: int = Field(default=20, ge=1)

    # Cron antigo de /Avarias (fluxo por evento, fora do escopo do MVP fechado).
    # Desligado por padrão desde o ticket 07 — ver o `## Answer` do ticket.
    # Religar com AVARIAS_INGEST_ENABLED=true; o endpoint manual
    # POST /api/v1/events/ingest continua disponível de qualquer forma.
    avarias_ingest_enabled: bool = False

    # ── Sisloc / SQL Server 2017 (somente leitura, atrás da VPN) ──────────────
    # Decisão de driver: docs/exploracao/sql-server-driver.md (ticket 02).
    # Integração opcional: sem host/user/password a esteira degrada, não quebra.
    sisloc_db_host: str | None = None
    sisloc_db_port: int = Field(default=1433, ge=1, le=65535)
    sisloc_db_name: str = "dbsisloc_tecnogera"
    sisloc_db_user: str | None = None
    sisloc_db_password: SecretStr | None = None
    sisloc_db_driver: str = "ODBC Driver 18 for SQL Server"
    # msodbcsql18 tem default Encrypt=yes. "strict" NÃO serve aqui: exige TDS 8.0,
    # que é SQL Server 2022+ — o servidor real é 2017.
    sisloc_db_encrypt: Literal["yes", "no"] = "yes"
    # O certificado do 10.246.0.15 não é confiável pela cadeia padrão (medido no
    # ticket 03: sqlcmd só conectou com -C). Default True enquanto a CA interna
    # não for fornecida pela Tecnogera.
    sisloc_db_trust_server_certificate: bool = True
    sisloc_db_login_timeout: int = Field(default=5, ge=1, le=60)
    sisloc_db_query_timeout: int = Field(default=15, ge=1, le=300)

    @field_validator(
        "sisloc_db_host",
        "sisloc_db_user",
        "sisloc_db_password",
        mode="before",
    )
    @classmethod
    def _sisloc_empty_para_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

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
        "initial_admin_email",
        "initial_admin_password",
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
        if self.llm_provider_efetivo == "fake":
            # Sem chave nenhuma, ``_get_llm_provider`` cai no FakeLLMProvider —
            # que devolve "conforme" para tudo. Na tela do operador esse laudo
            # é indistinguível de um laudo real: é o pior modo de falha do
            # projeto, porque o erro passa como resultado válido. Não existe uso
            # legítimo de resultado fictício em produção, então não há escape
            # hatch: ou há chave de LLM, ou a produção não sobe.
            problemas.append(
                "nenhuma chave de LLM configurada (OPENAI_API_KEY / ANTHROPIC_API_KEY): "
                "o provider cairia no fake e produziria laudo fictício"
            )
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
    def sisloc_configurado(self) -> bool:
        """True quando host, usuário e senha do Sisloc estão presentes."""
        return all((self.sisloc_db_host, self.sisloc_db_user, self.sisloc_db_password))

    @property
    def sisloc_destino(self) -> str:
        """Descrição do alvo SEM credencial — a única forma segura de logar o Sisloc."""
        return f"{self.sisloc_db_host}:{self.sisloc_db_port}/{self.sisloc_db_name}"

    @property
    def sisloc_odbc_connect(self) -> str:
        """Connection string ODBC crua (não é URL — não sofre URL-encoding).

        CONTÉM ``PWD=`` EM CLARO. Nunca logar, nunca colocar em mensagem de
        exceção; para diagnóstico use ``sisloc_destino``.

        PWD vai entre chaves porque senha de ERP costuma ter ';' e '='.
        """
        # Import local: app.core.exceptions -> app.core.logging -> app.core.config.
        from app.core.exceptions import ConfigurationError

        if (
            self.sisloc_db_host is None
            or self.sisloc_db_user is None
            or self.sisloc_db_password is None
        ):
            raise ConfigurationError(
                "credenciais do Sisloc ausentes: defina SISLOC_DB_HOST, "
                "SISLOC_DB_USER e SISLOC_DB_PASSWORD"
            )
        pwd = self.sisloc_db_password.get_secret_value()
        pwd_escapado = pwd.replace("}", "}}")  # '}' literal dobra dentro de {...}
        trust = "yes" if self.sisloc_db_trust_server_certificate else "no"
        return ";".join(
            [
                f"DRIVER={{{self.sisloc_db_driver}}}",
                f"SERVER={self.sisloc_db_host},{self.sisloc_db_port}",
                f"DATABASE={self.sisloc_db_name}",
                f"UID={self.sisloc_db_user}",
                f"PWD={{{pwd_escapado}}}",
                f"Encrypt={self.sisloc_db_encrypt}",
                f"TrustServerCertificate={trust}",
                f"Connection Timeout={self.sisloc_db_login_timeout}",
                "APP=tecnogera-ia-visual",  # aparece no sp_who2 do DBA
            ]
        )

    @property
    def checklist_ingest_root_efetiva(self) -> str:
        """Raiz varrida pelo cron de checklists (default: ``dropbox_root_path``)."""
        return self.checklist_ingest_root.strip() or self.dropbox_root_path

    @property
    def llm_provider_efetivo(self) -> Literal["openai", "anthropic", "fake"]:
        """Qual provider ``_get_llm_provider`` vai instanciar, sem instanciá-lo.

        Espelha exatamente a precedência de ``app/tasks/event_tasks.py``:
        OpenAI se houver chave, Anthropic como plano B, Fake se não houver
        nenhuma. Existe para que o guarda-corpo de produção possa decidir no
        boot, onde importar serviços seria import circular.
        """
        if self.openai_api_key is not None:
            return "openai"
        if self.anthropic_api_key is not None:
            return "anthropic"
        return "fake"

    @property
    def llm_model_efetivo(self) -> str:
        """Modelo do provider efetivo — o que vai para o cálculo de custo."""
        provider = self.llm_provider_efetivo
        if provider == "openai":
            return self.openai_model
        if provider == "anthropic":
            return self.anthropic_model
        return "fake"

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
