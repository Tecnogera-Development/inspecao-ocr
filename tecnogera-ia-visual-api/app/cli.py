"""CLI de operação — IAVS-030.

Uso:
    python -m app.cli create_user --email <email> --password <senha>
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

import bcrypt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User

if TYPE_CHECKING:
    pass


def create_user_in_db(db: Session, email: str, password: str) -> None:
    """Cria um usuário no banco, hash de senha via bcrypt. Levanta IntegrityError se duplicado."""
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    db.commit()


def _make_db_session() -> Session:  # pragma: no cover
    """Cria uma sessão de banco padrão via settings de produção."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import get_settings

    cfg = get_settings()
    engine = create_engine(cfg.database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return factory()


def _run_create_user(
    email: str,
    password: str,
    *,
    db_factory: Callable[[], Session] | None = None,
) -> None:
    db = (db_factory or _make_db_session)()
    try:
        create_user_in_db(db, email, password)
        print(f"Usuário criado: {email}")  # noqa: T201
    except IntegrityError:
        db.rollback()
        print(f"Erro: email '{email}' já existe.", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description="CLI de operação Tecnogera IA")
    sub = parser.add_subparsers(dest="command", required=True)

    create_user_parser = sub.add_parser("create_user", help="Cria um usuário do portal")
    create_user_parser.add_argument("--email", required=True, help="Email do usuário")
    create_user_parser.add_argument("--password", required=True, help="Senha do usuário")

    args = parser.parse_args(argv)

    if args.command == "create_user":
        _run_create_user(args.email, args.password)


if __name__ == "__main__":  # pragma: no cover
    main()
