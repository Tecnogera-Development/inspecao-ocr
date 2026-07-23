"""Testes do recovery hook — pending_batch deve ser preservado — IAVS-041."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _run_recovery_hook() -> list[str]:
    """Executa _recovery_hook com mocks e retorna as queries SQL executadas."""
    from importlib import reload

    import app.main as main_module

    executed_queries: list[str] = []

    mock_db = MagicMock()
    mock_db.execute.side_effect = lambda stmt: executed_queries.append(str(stmt))

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_db)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_session_factory = MagicMock(return_value=mock_session)
    mock_engine = MagicMock()

    with (
        patch("sqlalchemy.create_engine", return_value=mock_engine),
        patch("sqlalchemy.orm.sessionmaker", return_value=mock_session_factory),
    ):
        from app.core.config import Settings

        cfg = Settings(postgres_password="test")  # type: ignore[arg-type]
        main_module._recovery_hook(cfg)

    return executed_queries


@pytest.mark.unit
def test_recovery_hook_preserva_pending_batch() -> None:
    """Jobs em pending_batch NÃO viram failed no recovery hook ao subir a API."""
    queries = _run_recovery_hook()
    assert queries, "nenhuma query foi executada"
    query_sql = queries[0]
    assert "pending_batch" not in query_sql, (
        f"pending_batch não deve aparecer no UPDATE de recovery: {query_sql}"
    )


@pytest.mark.unit
def test_recovery_hook_marca_running_como_failed() -> None:
    """Jobs em running continuam virando failed no recovery hook."""
    queries = _run_recovery_hook()
    query_sql = queries[0]
    assert "running" in query_sql
    assert "failed" in query_sql
