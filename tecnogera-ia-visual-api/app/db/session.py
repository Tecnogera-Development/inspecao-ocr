"""Dependency FastAPI para sessão de banco de dados."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Generator

_engine = None
_SessionLocal = None


def _get_session_factory() -> sessionmaker[Session]:
    global _engine, _SessionLocal  # noqa: PLW0603
    if _SessionLocal is None:
        cfg = get_settings()
        _engine = create_engine(cfg.database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    factory = _get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_session_factory() -> sessionmaker[Session]:
    """Retorna a factory de Session para uso fora do DI (workers, background tasks).

    Uso correto:
        db = get_session_factory()()
        try:
            ...
        finally:
            db.close()
    """
    return _get_session_factory()
