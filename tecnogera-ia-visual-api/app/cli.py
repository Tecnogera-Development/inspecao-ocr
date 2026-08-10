"""CLI de operação — IAVS-030.

Uso:
    python -m app.cli create_user --email <email> --password <senha> [--role admin|operador]
    python -m app.cli sisloc_ping [--verbose]
    python -m app.cli analyze_pending [--max-calls N] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

import bcrypt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import ROLE_ADMIN, ROLE_OPERADOR, ROLES, User

if TYPE_CHECKING:
    pass


class PapelInvalidoError(ValueError):
    """Papel informado não é um dos papéis válidos do portal."""


def create_user_in_db(db: Session, email: str, password: str, role: str = ROLE_OPERADOR) -> None:
    """Cria um usuário no banco, hash de senha via bcrypt.

    Levanta ``IntegrityError`` se o e-mail já existir e ``PapelInvalidoError``
    se ``role`` não for um dos papéis válidos (``admin``/``operador``) — é o
    bootstrap do primeiro admin, então o valor errado não pode entrar em
    silêncio.
    """
    if role not in ROLES:
        raise PapelInvalidoError(
            f"papel inválido: {role!r} — use um de {sorted(ROLES)}"
        )
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=password_hash, role=role)
    db.add(user)
    db.commit()


def ensure_initial_user(
    db: Session, email: str, password: str, role: str = ROLE_ADMIN
) -> bool:
    """Cria o usuário inicial se ainda não existir (idempotente).

    Retorna ``True`` se criou agora, ``False`` se o e-mail já existia. Usado no
    boot para provisionar o acesso inicial via INITIAL_ADMIN_EMAIL/PASSWORD.
    Nasce com papel ``admin`` (default) para poder gerenciar usuários no portal
    — omitir o papel faria nascer ``operador`` e a tela de Usuários daria 403.
    """
    if db.query(User).filter(User.email == email).first() is not None:
        return False
    create_user_in_db(db, email, password, role=role)
    return True


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
    role: str = ROLE_OPERADOR,
    *,
    db_factory: Callable[[], Session] | None = None,
) -> None:
    db = (db_factory or _make_db_session)()
    try:
        create_user_in_db(db, email, password, role)
        print(f"Usuário criado: {email} (papel: {role})")  # noqa: T201
    except IntegrityError:
        db.rollback()
        print(f"Erro: email '{email}' já existe.", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    except PapelInvalidoError as exc:
        db.rollback()
        print(f"Erro: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    finally:
        db.close()


def _run_sisloc_ping(*, verbose: bool = False) -> None:
    """Verifica o acesso vivo ao SQL Server do Sisloc (ticket mvp-c54-c57/03).

    Somente leitura: roda um ``SELECT TOP 1`` em ``[dbo].[checklist produto]``.
    Sai com código 1 em qualquer falha, para servir de check em script de deploy.
    """
    from app.core.exceptions import AppError
    from app.services.sisloc import SislocService

    service = SislocService()
    print(f"Sisloc: {service.destino}")  # noqa: T201
    try:
        resultado = service.ping()
    except AppError as exc:
        print(f"FALHA [{exc.error_code}]: {exc.message}", file=sys.stderr)  # noqa: T201
        print(  # noqa: T201
            "Dica: 'Login timeout expired' (HYT00) quase sempre é VPN caída, "
            "não credencial errada. Ver docs/operations/sql-server.md.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"OK — conexão estabelecida, {resultado.total_colunas} colunas")  # noqa: T201
    if not resultado.linha:
        print("Atenção: a tabela respondeu, mas sem linhas.")  # noqa: T201
        return
    campos = resultado.colunas if verbose else resultado.colunas[:12]
    for nome in campos:
        print(f"  {nome} = {resultado.linha.get(nome)!r}")  # noqa: T201
    restantes = len(resultado.colunas) - len(campos)
    if restantes > 0:
        print(f"  ... (+{restantes} colunas; use --verbose para todas)")  # noqa: T201


def _run_analyze_pending(*, max_calls: int | None = None, dry_run: bool = False) -> None:
    """Despacha uma rodada de análise à mão (ticket mvp-c54-c57/08).

    Existe para o operador poder rodar a esteira sob supervisão sem ligar o
    cron — e ``--max-calls`` é o freio explícito para uma primeira validação
    controlada, sobrepondo ``LLM_MAX_CALLS_PER_RUN`` só nesta execução.
    ``--dry-run`` só mostra a fila e o gasto do mês, sem chamar nada.
    """
    from app.core.config import get_settings
    from app.services.checklist_analysis import ChecklistAnalysisService
    from app.services.dropbox import DropboxService
    from app.services.llm_budget import LLMBudgetGuard
    from app.services.llm_provider import get_llm_provider

    cfg = get_settings()
    overrides: dict[str, object] = {}
    if max_calls is not None:
        overrides["llm_max_calls_per_run"] = max_calls
    if dry_run:
        overrides["llm_dispatch_enabled"] = False
    if overrides:
        cfg = cfg.model_copy(update=overrides)

    db = _make_db_session()
    try:
        guard = LLMBudgetGuard(db, cfg)
        print(f"Provider: {cfg.llm_provider_efetivo} ({cfg.llm_model_efetivo})")  # noqa: T201
        print(  # noqa: T201
            f"Gasto no mês: US$ {guard.gasto_persistido_no_mes():.4f} "
            f"de US$ {cfg.llm_monthly_budget_usd:.2f}"
        )
        service = ChecklistAnalysisService(
            db=db,
            dropbox=DropboxService(cfg),
            provider=get_llm_provider(cfg),
            settings=cfg,
            guard=guard,
        )
        resultado = service.dispatch_pending()
    finally:
        db.close()
    for chave, valor in resultado.como_log().items():
        print(f"  {chave} = {valor}")  # noqa: T201


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description="CLI de operação Tecnogera IA")
    sub = parser.add_subparsers(dest="command", required=True)

    create_user_parser = sub.add_parser("create_user", help="Cria um usuário do portal")
    create_user_parser.add_argument("--email", required=True, help="Email do usuário")
    create_user_parser.add_argument("--password", required=True, help="Senha do usuário")
    create_user_parser.add_argument(
        "--role",
        default=ROLE_OPERADOR,
        help=f"Papel do usuário — um de {sorted(ROLES)} (padrão: {ROLE_OPERADOR})",
    )

    sisloc_parser = sub.add_parser(
        "sisloc_ping",
        help="Testa o acesso somente-leitura ao SQL Server do Sisloc (exige VPN)",
    )
    sisloc_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Imprime todas as colunas da linha lida",
    )

    analyze_parser = sub.add_parser(
        "analyze_pending",
        help="Despacha uma rodada de análise dos checklists pendentes (GASTA LLM)",
    )
    analyze_parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="Teto de chamadas SÓ nesta execução (sobrepõe LLM_MAX_CALLS_PER_RUN)",
    )
    analyze_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só mostra fila e gasto do mês; não chama LLM nenhuma",
    )

    args = parser.parse_args(argv)

    if args.command == "create_user":
        _run_create_user(args.email, args.password, args.role)
    elif args.command == "sisloc_ping":
        _run_sisloc_ping(verbose=args.verbose)
    elif args.command == "analyze_pending":
        _run_analyze_pending(max_calls=args.max_calls, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    main()
