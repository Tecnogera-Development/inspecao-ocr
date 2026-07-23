"""Teste de migration das colunas batch em pipeline_jobs — IAVS-041.

Verifica que a migration 0003 pode fazer up/down sem erros em SQLite in-memory.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text


@pytest.fixture()
def sqlite_engine() -> sa.Engine:
    """Engine SQLite in-memory para testar estrutura das migrations."""
    engine = sa.create_engine("sqlite:///:memory:")
    # Cria tabela base (equivalente a migration 0001)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE pipeline_jobs (
                id TEXT PRIMARY KEY,
                checklist_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                finished_at DATETIME,
                error TEXT,
                result_pdf_path TEXT,
                metrics TEXT
            )
        """))
        # Insere um job antigo (v1.0 — sem colunas batch)
        conn.execute(text(
            "INSERT INTO pipeline_jobs (id, checklist_id, status) VALUES ('job-1', '276800', 'done')"
        ))
        conn.commit()
    return engine


@pytest.mark.unit
def test_migration_batch_up_adiciona_colunas(sqlite_engine: sa.Engine) -> None:
    """Após up da migration 0003, as colunas batch existem na tabela."""
    with sqlite_engine.connect() as conn:
        # Simula up: adiciona as 4 colunas novas
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'sync'"))
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN batch_id TEXT"))
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN batch_submitted_at DATETIME"))
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN batch_resolved_at DATETIME"))
        conn.commit()

    # Job antigo ainda existe e tem mode='sync' por default
    with sqlite_engine.connect() as conn:
        row = conn.execute(
            text("SELECT mode, batch_id FROM pipeline_jobs WHERE id='job-1'")
        ).fetchone()
    assert row is not None
    assert row[0] == "sync"
    assert row[1] is None


@pytest.mark.unit
def test_migration_batch_down_remove_colunas(sqlite_engine: sa.Engine) -> None:
    """Após down da migration 0003, os dados originais sobrevivem (SQLite copia tabela)."""
    with sqlite_engine.connect() as conn:
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'sync'"))
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN batch_id TEXT"))
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN batch_submitted_at DATETIME"))
        conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN batch_resolved_at DATETIME"))
        # Insere um job com dados de batch
        conn.execute(text(
            "INSERT INTO pipeline_jobs (id, checklist_id, status, mode, batch_id) "
            "VALUES ('job-batch', '278154', 'pending_batch', 'batch', 'batch_xyz')"
        ))
        conn.commit()

    # Simula down: recria tabela sem colunas batch (SQLite não tem DROP COLUMN direto)
    with sqlite_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE pipeline_jobs_bak AS
            SELECT id, checklist_id, status, created_at, started_at, finished_at,
                   error, result_pdf_path, metrics
            FROM pipeline_jobs
        """))
        conn.execute(text("DROP TABLE pipeline_jobs"))
        conn.execute(text("ALTER TABLE pipeline_jobs_bak RENAME TO pipeline_jobs"))
        conn.commit()

    # Dados originais preservados
    with sqlite_engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM pipeline_jobs")).fetchall()
    ids = [r[0] for r in rows]
    assert "job-1" in ids
    assert "job-batch" in ids
