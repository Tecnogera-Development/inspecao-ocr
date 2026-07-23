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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory — facilita teste isolado e reuso em workers."""
    cfg = settings or get_settings()
    configure_logging(cfg)
    log = get_logger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        _recovery_hook(cfg)
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_allow_origins,
        allow_credentials=True,
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
