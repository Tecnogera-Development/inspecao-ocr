"""Worker Arq para processamento de eventos de avaria.

Executar no docker-compose com:
    arq app.worker.WorkerSettings

Ou localmente:
    arq app.worker.WorkerSettings

Configuração via variáveis de ambiente:
    EVENT_QUEUE_CONCURRENCY (default 30)
    EVENT_QUEUE_MAX_RETRIES (default 3)
    REDIS_HOST / REDIS_PORT
"""

from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.tasks.event_tasks import process_event, scheduled_ingest


def _redis_settings() -> RedisSettings:
    cfg = get_settings()
    return RedisSettings(host=cfg.redis_host, port=cfg.redis_port)


class WorkerSettings:
    """Configuração do worker Arq.

    Referenciada pelo CLI: ``arq app.worker.WorkerSettings``.
    """

    functions = [process_event, scheduled_ingest]
    # Ingestão automática a cada 5 min — o operador não dispara nada na mão.
    cron_jobs = [cron(scheduled_ingest, minute=set(range(0, 60, 5)), run_at_startup=False)]
    redis_settings = _redis_settings()
    max_jobs: int = get_settings().event_queue_concurrency
    job_timeout: int = 600
    retry_jobs: bool = True
    max_tries: int = get_settings().event_queue_max_retries
    keep_result: int = 3600  # manter resultado 1h para debug
