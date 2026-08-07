"""Engine SQLAlchemy dedicado ao SQL Server do Sisloc (somente leitura).

Separado do Postgres de propósito: outro dialeto, outro ciclo de vida e
opcional — se a credencial não existir, a esteira degrada em vez de quebrar.

**Nunca é alvo de migration**: este Engine não entra no ``MetaData`` do Alembic
e nenhum modelo ORM é mapeado sobre a base do ERP. A credencial
``maisacesso_read`` é read-only do lado do servidor; o código também é.

Decisão de driver: ``docs/exploracao/sql-server-driver.md`` (ticket 02).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app.core.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from app.core.config import Settings

# Pool deliberadamente pequeno: o gargalo do checklist é o LLM, não o Sisloc.
# Abrir dezenas de sessões num ERP de produção é a maneira mais rápida de o DBA
# da Tecnogera revogar a credencial.
POOL_SIZE = 2
MAX_OVERFLOW = 3
# Firewall/NAT corporativo mata sessão TCP ociosa em silêncio (08S01).
POOL_RECYCLE_SECONDS = 1800

_engine: Engine | None = None


def build_sisloc_engine(cfg: Settings) -> Engine:
    """Cria um Engine novo para o Sisloc. Não faz cache — ver ``get_sisloc_engine``.

    Levanta ``ConfigurationError`` se as credenciais não estiverem presentes.
    A connection string contém ``PWD=``; ela fica confinada ao ``URL.create``
    abaixo e nunca é logada nem embutida em mensagem de exceção.
    """
    url = URL.create("mssql+pyodbc", query={"odbc_connect": cfg.sisloc_odbc_connect})
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_recycle=POOL_RECYCLE_SECONDS,
        # timeout de QUERY (cursor.timeout do pyodbc); o de login vai na
        # connection string como "Connection Timeout".
        connect_args={"timeout": cfg.sisloc_db_query_timeout},
        # Leitura read-only em ERP não pode deixar transação aberta segurando lock.
    ).execution_options(isolation_level="AUTOCOMMIT")


def get_sisloc_engine() -> Engine:
    """Singleton preguiçoso de módulo, no mesmo padrão de ``app/db/session.py``."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = build_sisloc_engine(get_settings())
    return _engine


def dispose_sisloc_engine() -> None:
    """Fecha o pool (shutdown do app / testes)."""
    global _engine  # noqa: PLW0603
    if _engine is not None:
        _engine.dispose()
        _engine = None
