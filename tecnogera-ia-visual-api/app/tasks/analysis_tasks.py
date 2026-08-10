"""Arq cron: despacho da análise de checklists — ticket ``mvp-c54-c57/08``.

O ticket 07 materializa ``pipeline_jobs`` ``pending`` e para ali. Esta tarefa é
quem os consome — e é **o único ponto da esteira que gasta dinheiro**. Por isso
ela é a única com kill switch próprio (``LLM_DISPATCH_ENABLED``, default
``false``): a ingestão pode rodar sozinha por dias, acumulando fila, sem
consumir um token.

Cadência 30 min, deslocada 10 min do cron de ingestão (``:10`` e ``:40`` contra
``:00`` e ``:30``). O deslocamento não é estético: rodar junto faria a análise
disputar a mesma janela de I/O do Dropbox que a ingestão, e a fila que ela lê
seria justamente a que o outro cron ainda está escrevendo.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger(__name__)


async def scheduled_checklist_analysis(ctx: dict[str, Any]) -> dict[str, Any]:
    """Uma rodada de despacho: jobs ``pending`` → laudo por vista + rollup.

    Nenhuma falha de integração escapa daqui. Um Dropbox intermitente ou uma
    chave revogada viram log e a rodada seguinte tenta de novo; derrubar o
    worker deixaria a esteira parada até alguém notar.
    """
    import asyncio  # noqa: PLC0415

    from app.core.exceptions import AppError  # noqa: PLC0415
    from app.db.session import get_session_factory  # noqa: PLC0415
    from app.services.checklist_analysis import ChecklistAnalysisService  # noqa: PLC0415
    from app.services.dropbox import DropboxService  # noqa: PLC0415
    from app.services.llm_provider import get_llm_provider  # noqa: PLC0415

    settings = get_settings()
    if not settings.llm_dispatch_enabled:
        # Barato e explícito: nem abre sessão de banco. O guarda também recusa,
        # mas a esteira desligada é o caso comum e não precisa custar conexão.
        return {"skipped": "llm_dispatch_disabled"}

    db = get_session_factory()()
    try:
        service = ChecklistAnalysisService(
            db=db,
            dropbox=DropboxService(settings),
            provider=get_llm_provider(settings),
            settings=settings,
        )
        # Dropbox e SDKs de LLM são I/O de rede bloqueante e o pyodbc é síncrono:
        # fora da thread do event loop, senão o worker inteiro trava.
        resultado = await asyncio.to_thread(service.dispatch_pending)
    except AppError as exc:
        db.rollback()
        _log.warning(
            "checklist_analysis_falhou",
            error_code=exc.error_code,
            error=exc.message,
        )
        return {"error": exc.error_code}
    except Exception as exc:  # noqa: BLE001 — cron não pode derrubar o worker
        db.rollback()
        _log.error("checklist_analysis_erro_inesperado", error=str(exc))
        return {"error": "unexpected"}
    else:
        _log.info("checklist_analysis", **resultado.como_log())
        return resultado.como_log()
    finally:
        db.close()
