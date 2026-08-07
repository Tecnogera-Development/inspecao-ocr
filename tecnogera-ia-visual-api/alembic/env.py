"""Alembic env — usa database_url das Settings da aplicação."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Importar Base para suporte a autogenerate
from app.db.base import Base  # noqa: E402
import app.models.pipeline  # noqa: E402, F401  — garante que os modelos estão registrados
import app.models.event  # noqa: E402, F401
import app.models.event_pair  # noqa: E402, F401
import app.models.ingest  # noqa: E402, F401
import app.models.checklist_analysis  # noqa: E402, F401

target_metadata = Base.metadata


def _get_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app.core.config import get_settings
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    if not cfg.get("sqlalchemy.url"):
        cfg["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
