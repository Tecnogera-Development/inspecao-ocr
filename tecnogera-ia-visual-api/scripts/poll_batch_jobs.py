#!/usr/bin/env python3
"""poll_batch_jobs.py — itera jobs pending_batch e retoma o pipeline quando prontos.

Uso (dentro do container ou localmente com .env):
    python scripts/poll_batch_jobs.py

Crontab VPS (*/10 * * * *):
    */10 * * * * docker exec ia-visual-api python scripts/poll_batch_jobs.py >> /var/log/tecnogera/batch_poller.log 2>&1

State machine: pending → running (prewarm) → pending_batch → running (resolved) → done
"""

from __future__ import annotations

import sys
from pathlib import Path

# Garante que o root do projeto está no path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.services.batch_poller import BatchPoller
from app.services.dropbox import DropboxService
from app.services.llm_provider import AnthropicProvider

_log = get_logger("poll_batch_jobs")


def main() -> int:
    settings = get_settings()

    if settings.anthropic_api_key is None:
        _log.error("poll_batch_jobs_no_api_key")
        return 1

    provider = AnthropicProvider(
        api_key=settings.anthropic_api_key.get_secret_value(),
        model=settings.anthropic_model,
    )
    dropbox = DropboxService(settings)

    db_gen = get_db()
    db = next(db_gen)
    try:
        poller = BatchPoller(db=db, dropbox=dropbox, provider=provider, settings=settings)
        stats = poller.poll_once()
        _log.info("poll_batch_jobs_done", **stats)
        return 0
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


if __name__ == "__main__":
    sys.exit(main())
