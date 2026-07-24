"""Entrypoint da aplicação FastAPI — IAVS-002.

Monta a aplicação com configuração externalizada (Pydantic Settings),
logging estruturado em JSON, CORS configurável, handlers globais de
exceção e roteamento modular.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.routers import events, meta, pipeline, portal

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def _recovery_hook(cfg: Settings) -> None:
    """Marca jobs orphaned como failed ao reiniciar a API."""
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(cfg.database_url, pool_pre_ping=True)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            db.execute(
                text(
                    "UPDATE pipeline_jobs SET status='failed', error='api_restart'"
                    " WHERE status IN ('pending','running')"
                )
            )
            db.commit()
        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        get_logger(__name__).warning("recovery_hook_failed", reason=str(exc))


def _seed_initial_user(cfg: Settings) -> None:
    """Cria o usuário inicial do portal a partir das variáveis de ambiente.

    Idempotente e best-effort: se ``INITIAL_ADMIN_EMAIL`` /
    ``INITIAL_ADMIN_PASSWORD`` não estão definidos, ou o usuário já existe,
    nada acontece. Falhas (ex.: banco indisponível) apenas logam — não
    derrubam o boot da API.
    """
    if cfg.initial_admin_email is None or cfg.initial_admin_password is None:
        return
    log = get_logger(__name__)
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.cli import ensure_initial_user

        engine = create_engine(cfg.database_url, pool_pre_ping=True)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            created = ensure_initial_user(
                db,
                cfg.initial_admin_email,
                cfg.initial_admin_password.get_secret_value(),
            )
        engine.dispose()
        if created:
            log.warning(
                "initial_admin_criado",
                email=cfg.initial_admin_email,
                aviso=(
                    "troque a senha e remova INITIAL_ADMIN_EMAIL/PASSWORD do "
                    "ambiente apos o primeiro login"
                ),
            )
        else:
            log.info("initial_admin_ja_existe", email=cfg.initial_admin_email)
    except Exception as exc:  # noqa: BLE001
        log.warning("seed_initial_user_failed", reason=str(exc))


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory — facilita teste isolado e reuso em workers."""
    cfg = settings or get_settings()
    configure_logging(cfg)
    log = get_logger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        _recovery_hook(cfg)
        _seed_initial_user(cfg)
        # Arq pool: opcional — não falha o boot se Redis estiver indisponível
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            app.state.arq_pool = await create_pool(
                RedisSettings(host=cfg.redis_host, port=cfg.redis_port)
            )
        except Exception as exc:  # noqa: BLE001
            app.state.arq_pool = None
            get_logger(__name__).warning("arq_pool_unavailable", reason=str(exc))
        yield
        pool = getattr(app.state, "arq_pool", None)
        if pool is not None:
            await pool.aclose()

    app = FastAPI(
        title="Tecnogera IA Visual API",
        version=cfg.app_version,
        description="Sistema de inspeção visual automatizada por IA.",
        lifespan=lifespan,
    )

    # CORS: nunca combinar wildcard com credenciais (combinação insegura e
    # rejeitada pelos browsers). Com origem '*' as credenciais são desligadas;
    # em produção o boot já exige origens explícitas (ver Settings._validar_producao).
    _cors_allow_all = cfg.cors_allow_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_allow_origins,
        allow_credentials=not _cors_allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.session_secret.get_secret_value(),
        session_cookie="session",
        max_age=8 * 3600,  # 8h TTL
        same_site="lax",
        https_only=cfg.is_production,
    )

    register_exception_handlers(app)

    # Rate limiter do login (brute-force). Instância por app → sem estado
    # global compartilhado entre testes.
    from app.services.rate_limit import RateLimiter

    app.state.login_rate_limiter = RateLimiter(
        max_attempts=cfg.login_max_attempts,
        window_seconds=cfg.login_window_seconds,
    )

    app.include_router(meta.router)
    app.include_router(pipeline.router)
    app.include_router(portal.router)
    app.include_router(events.router)

    log.info(
        "app_started",
        app_name=cfg.app_name,
        app_version=cfg.app_version,
        env=cfg.app_env.value,
    )
    return app


app = create_app()


__all__ = ["app", "create_app", "__version__"]
