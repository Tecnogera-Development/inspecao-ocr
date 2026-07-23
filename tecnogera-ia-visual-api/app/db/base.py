"""Base declarativa e factory de engine SQLAlchemy."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> create_engine:  # type: ignore[valid-type]
    return create_engine(database_url, pool_pre_ping=True)
