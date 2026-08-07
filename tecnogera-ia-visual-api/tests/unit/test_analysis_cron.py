"""Cron de análise e seu lugar no worker — ticket mvp-c54-c57/08.

O cron de análise é o único da esteira que gasta dinheiro. Estes testes fixam
que ele não sobe sem alguém ligar, que não derruba o worker quando o Dropbox
ou a OpenAI caem, e que não disputa janela com o cron de ingestão.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import AppEnv, Settings
from app.core.exceptions import IntegrationError
from app.services.checklist_analysis import ChecklistAnalysisResult
from app.tasks.analysis_tasks import scheduled_checklist_analysis

pytestmark = pytest.mark.unit


def _cfg(**extra: Any) -> Settings:
    base: dict[str, Any] = {"llm_dispatch_enabled": True, "openai_api_key": "sk-teste"}
    base.update(extra)
    return Settings(_env_file=None, app_env=AppEnv.TEST, **base)


async def _rodar(cfg: Settings, service: MagicMock) -> dict[str, Any]:
    patchers = (
        patch("app.core.config.get_settings", return_value=cfg),
        patch("app.tasks.analysis_tasks.get_settings", return_value=cfg),
        patch("app.db.session.get_session_factory", return_value=MagicMock()),
        patch("app.services.dropbox.DropboxService", return_value=MagicMock()),
        patch("app.services.llm_provider.get_llm_provider", return_value=MagicMock()),
        patch(
            "app.services.checklist_analysis.ChecklistAnalysisService",
            return_value=service,
        ),
    )
    for p in patchers:
        p.start()
    try:
        return await scheduled_checklist_analysis({})
    finally:
        for p in patchers:
            p.stop()


# ── task ──────────────────────────────────────────────────────────────────────


async def test_cron_devolve_o_resumo_da_rodada() -> None:
    service = MagicMock()
    service.dispatch_pending.return_value = ChecklistAnalysisResult(
        jobs_analisados=2, chamadas_llm=6, custo_usd=0.011
    )

    out = await _rodar(_cfg(), service)

    assert out["jobs_analisados"] == 2
    assert out["chamadas_llm"] == 6
    assert out["custo_usd"] == 0.011


async def test_kill_switch_desligado_nem_abre_sessao() -> None:
    """Caso comum: a esteira ingere sem gastar. Não deve custar conexão."""
    service = MagicMock()

    out = await _rodar(_cfg(llm_dispatch_enabled=False), service)

    assert out == {"skipped": "llm_dispatch_disabled"}
    service.dispatch_pending.assert_not_called()


async def test_falha_de_integracao_nao_derruba_o_worker() -> None:
    service = MagicMock()
    service.dispatch_pending.side_effect = IntegrationError("Dropbox fora do ar")

    out = await _rodar(_cfg(), service)

    assert out == {"error": "integration_error"}


async def test_erro_inesperado_tambem_e_contido() -> None:
    service = MagicMock()
    service.dispatch_pending.side_effect = RuntimeError("boom")

    out = await _rodar(_cfg(), service)

    assert out == {"error": "unexpected"}


# ── worker ────────────────────────────────────────────────────────────────────


def _worker_com(cfg: Settings) -> Any:
    import app.worker as worker_mod

    with patch("app.core.config.get_settings", return_value=cfg):
        return importlib.reload(worker_mod)


@pytest.fixture(autouse=True)
def _restaura_worker() -> Any:
    yield
    import app.worker as worker_mod

    importlib.reload(worker_mod)


def test_cron_de_analise_nao_sobe_por_default() -> None:
    """Default fechado: subir gastando por engano é o modo de falha caro."""
    worker = _worker_com(Settings(_env_file=None, app_env=AppEnv.TEST))

    nomes = {c.name for c in worker.WorkerSettings.cron_jobs}
    assert "cron:scheduled_checklist_analysis" not in nomes
    assert any(
        f.__name__ == "scheduled_checklist_analysis" for f in worker.WorkerSettings.functions
    )


def test_cron_de_analise_sobe_deslocado_do_de_ingestao() -> None:
    """Rodar junto disputaria I/O do Dropbox e leria fila em escrita."""
    worker = _worker_com(_cfg())

    crons = {c.name: c for c in worker.WorkerSettings.cron_jobs}
    analise = crons["cron:scheduled_checklist_analysis"]
    ingestao = crons["cron:scheduled_checklist_ingest"]

    assert analise.minute == {10, 40}
    assert ingestao.minute == {0, 30}
    assert not (analise.minute & ingestao.minute)
