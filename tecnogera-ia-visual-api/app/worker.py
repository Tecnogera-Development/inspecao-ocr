"""Worker Arq para processamento de eventos de avaria.

Executar no docker-compose com:
    arq app.worker.WorkerSettings

Ou localmente:
    arq app.worker.WorkerSettings

Configuração via variáveis de ambiente:
    EVENT_QUEUE_CONCURRENCY (default 30)
    EVENT_QUEUE_MAX_RETRIES (default 3)
    REDIS_HOST / REDIS_PORT
    CHECKLIST_INGEST_ENABLED (default true)  — cron de checklists, 30 min
    LLM_DISPATCH_ENABLED     (default false) — cron de ANÁLISE, 30 min (gasta $)
    AVARIAS_INGEST_ENABLED   (default false) — cron antigo de /Avarias, 5 min
"""

from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.tasks.analysis_tasks import scheduled_checklist_analysis
from app.tasks.checklist_tasks import scheduled_checklist_ingest
from app.tasks.event_tasks import process_event, scheduled_ingest


def _redis_settings() -> RedisSettings:
    cfg = get_settings()
    return RedisSettings(host=cfg.redis_host, port=cfg.redis_port)


def _cron_jobs() -> list:  # type: ignore[type-arg]
    """Monta a lista de crons conforme a configuração.

    **Decisão do ticket mvp-c54-c57/07 — os dois crons não coexistem ligados.**

    O cron antigo (``scheduled_ingest``, 5 min, raiz ``/Avarias``) pertence ao
    fluxo por evento com pareamento saída×retorno, que o escopo fechado do MVP
    tirou de cena (fora do escopo do MVP). Mantê-lo ligado
    significava varrer duas raízes do Dropbox em cadências diferentes e — pior —
    disparar classificação de imagem por LLM sem ninguém pedindo, gastando
    token num fluxo fora do escopo.

    Ele fica **desligado por default** (``AVARIAS_INGEST_ENABLED=false``), não
    apagado: a função segue registrada, o endpoint ``POST /api/v1/events/ingest``
    continua disponível e uma variável de ambiente religa o cron se a Tecnogera
    quiser o fluxo de avarias de volta.
    """
    cfg = get_settings()
    jobs = []
    if cfg.checklist_ingest_enabled:
        # Ingestão de checklists do Sisloc a cada 30 min (ticket 07).
        jobs.append(
            cron(
                scheduled_checklist_ingest,
                minute={0, 30},
                run_at_startup=False,
            )
        )
    if cfg.llm_dispatch_enabled:
        # Análise (ticket 08) — o ÚNICO cron que gasta dinheiro, e por isso o
        # único com kill switch próprio. Deslocado 10 min da ingestão: rodar
        # junto disputaria a mesma janela de I/O do Dropbox e leria uma fila
        # que o outro cron ainda está escrevendo.
        jobs.append(
            cron(
                scheduled_checklist_analysis,
                minute={10, 40},
                run_at_startup=False,
            )
        )
    if cfg.avarias_ingest_enabled:
        jobs.append(cron(scheduled_ingest, minute=set(range(0, 60, 5)), run_at_startup=False))
    return jobs


class WorkerSettings:
    """Configuração do worker Arq.

    Referenciada pelo CLI: ``arq app.worker.WorkerSettings``.
    """

    functions = [
        process_event,
        scheduled_ingest,
        scheduled_checklist_ingest,
        scheduled_checklist_analysis,
    ]
    cron_jobs = _cron_jobs()
    redis_settings = _redis_settings()
    max_jobs: int = get_settings().event_queue_concurrency
    job_timeout: int = 600
    retry_jobs: bool = True
    max_tries: int = get_settings().event_queue_max_retries
    keep_result: int = 3600  # manter resultado 1h para debug
