"""Arq cron: ingestão agendada de checklists do Sisloc — ticket ``mvp-c54-c57/07``.

Roda a cada 30 min. É o que torna a esteira automática: até aqui o pipeline só
existia via ``POST /pipeline/run`` com ``checklist_id`` digitado à mão.

A tarefa **não chama LLM** e **não despacha** os jobs que cria — eles nascem
``pending`` e a execução (com o custo de token junto) é do ticket 08. Também
não escreve nada no Dropbox: só ``files_list_folder*``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger(__name__)


async def scheduled_checklist_ingest(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron de 30 min: /Sisloc → filtro por formulário → ``pipeline_jobs``.

    Nenhuma falha de integração escapa daqui: Dropbox fora do ar ou VPN caída
    viram log e a rodada seguinte tenta de novo. Derrubar o worker por causa de
    uma dependência intermitente deixaria a esteira parada até alguém notar.
    """
    from app.core.exceptions import AppError
    from app.db.session import get_session_factory
    from app.services.checklist_ingestion import ChecklistIngestionService
    from app.services.dropbox import DropboxService
    from app.services.sisloc import SislocService

    settings = get_settings()
    if not settings.checklist_ingest_enabled:
        return {"skipped": "disabled"}

    db = get_session_factory()()
    try:
        service = ChecklistIngestionService(
            db=db,
            dropbox=DropboxService(settings),
            sisloc=SislocService(settings),
            settings=settings,
        )
        # O Sisloc é síncrono (pyodbc) e o Dropbox faz I/O de rede bloqueante:
        # fora da thread do event loop, senão o worker inteiro trava na VPN.
        resultado = await asyncio.to_thread(service.scan_and_ingest)
    except AppError as exc:
        db.rollback()
        _log.warning(
            "checklist_ingest_falhou",
            error_code=exc.error_code,
            error=exc.message,
        )
        return {"error": exc.error_code}
    except Exception as exc:  # noqa: BLE001 — cron não pode derrubar o worker
        db.rollback()
        _log.error("checklist_ingest_erro_inesperado", error=str(exc))
        return {"error": "unexpected"}
    else:
        _log.info("checklist_ingest", **resultado.como_log())
        return resultado.como_log()
    finally:
        db.close()
