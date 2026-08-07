"""Cron de 30 min e a decisão sobre o cron antigo — ticket mvp-c54-c57/07."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import AppEnv, Settings
from app.core.exceptions import IntegrationError
from app.services.checklist_ingestion import ChecklistIngestResult
from app.tasks.checklist_tasks import scheduled_checklist_ingest

pytestmark = pytest.mark.unit


def _cfg(**extra: Any) -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST, **extra)


def _patches(cfg: Settings, service: MagicMock):
    return (
        patch("app.core.config.get_settings", return_value=cfg),
        patch("app.tasks.checklist_tasks.get_settings", return_value=cfg),
        patch("app.db.session.get_session_factory", return_value=MagicMock()),
        patch("app.services.dropbox.DropboxService", return_value=MagicMock()),
        patch("app.services.sisloc.SislocService", return_value=MagicMock()),
        patch(
            "app.services.checklist_ingestion.ChecklistIngestionService",
            return_value=service,
        ),
    )


async def _rodar(cfg: Settings, service: MagicMock) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    patchers = _patches(cfg, service)
    for p in patchers:
        p.start()
    try:
        return await scheduled_checklist_ingest(ctx)
    finally:
        for p in patchers:
            p.stop()


# ── task ──────────────────────────────────────────────────────────────────────


async def test_cron_devolve_o_resumo_da_rodada() -> None:
    service = MagicMock()
    service.scan_and_ingest.return_value = ChecklistIngestResult(jobs_criados=3, imagens=42)

    out = await _rodar(_cfg(), service)

    assert out["jobs_criados"] == 3
    assert out["imagens"] == 42


async def test_cron_desligado_nao_toca_em_nada() -> None:
    service = MagicMock()
    out = await _rodar(_cfg(checklist_ingest_enabled=False), service)

    assert out == {"skipped": "disabled"}
    service.scan_and_ingest.assert_not_called()


async def test_falha_de_integracao_nao_derruba_o_worker() -> None:
    """VPN caída / Dropbox fora: loga e tenta na próxima rodada."""
    service = MagicMock()
    service.scan_and_ingest.side_effect = IntegrationError("HYT00 Login timeout expired")

    out = await _rodar(_cfg(), service)

    assert out == {"error": "integration_error"}


async def test_erro_inesperado_tambem_e_contido() -> None:
    service = MagicMock()
    service.scan_and_ingest.side_effect = RuntimeError("boom")

    out = await _rodar(_cfg(), service)

    assert out == {"error": "unexpected"}


# ── worker: cadência e a decisão sobre o cron antigo ──────────────────────────


def _worker_com(cfg: Settings) -> Any:
    import app.worker as worker_mod

    with patch("app.core.config.get_settings", return_value=cfg):
        return importlib.reload(worker_mod)


@pytest.fixture(autouse=True)
def _restaura_worker() -> Any:
    yield
    import app.worker as worker_mod

    importlib.reload(worker_mod)


def test_cron_de_checklists_roda_a_cada_30_min() -> None:
    worker = _worker_com(_cfg())
    crons = worker.WorkerSettings.cron_jobs
    nomes = {c.name for c in crons}

    assert "cron:scheduled_checklist_ingest" in nomes
    checklist = next(c for c in crons if c.name.endswith("scheduled_checklist_ingest"))
    assert checklist.minute == {0, 30}


def test_cron_antigo_de_avarias_vem_desligado() -> None:
    """Decisão do ticket 07: o fluxo por evento saiu do escopo do MVP.

    Não é remoção — a função segue registrada e AVARIAS_INGEST_ENABLED religa.
    """
    worker = _worker_com(_cfg())

    assert "cron:scheduled_ingest" not in {c.name for c in worker.WorkerSettings.cron_jobs}
    assert any(f.__name__ == "scheduled_ingest" for f in worker.WorkerSettings.functions)


def test_cron_antigo_pode_ser_religado_por_env() -> None:
    worker = _worker_com(_cfg(avarias_ingest_enabled=True))
    assert "cron:scheduled_ingest" in {c.name for c in worker.WorkerSettings.cron_jobs}


def test_desligar_os_dois_deixa_o_worker_sem_cron() -> None:
    worker = _worker_com(_cfg(checklist_ingest_enabled=False))
    assert worker.WorkerSettings.cron_jobs == []
