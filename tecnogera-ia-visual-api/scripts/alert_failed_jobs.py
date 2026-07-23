#!/usr/bin/env python3
"""alert_failed_jobs.py — verifica jobs failed na última hora e dispara alerta.

Uso (dentro do container ou localmente com .env):
    python scripts/alert_failed_jobs.py

Crontab VPS (*/15 * * * *):
    */15 * * * * docker exec ia-visual-api python scripts/alert_failed_jobs.py >> /var/log/tecnogera/alert_failed_jobs.log 2>&1
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_db
from app.services.alert_checker import check_and_alert

_log = get_logger("alert_failed_jobs")

_ALERTS_LOG = Path("/var/log/tecnogera/alerts.log")


def main() -> int:
    settings = get_settings()
    configure_logging(settings)

    db_gen = get_db()
    db = next(db_gen)
    try:
        result = check_and_alert(
            db,
            threshold=settings.alert_failed_jobs_threshold,
            log_path=_ALERTS_LOG,
            email_to=settings.alert_email_to,
        )
        if result["alerted"]:
            _log.warning(
                "alert_failed_jobs_triggered",
                count=result["count"],
                threshold=result["threshold"],
            )
        else:
            _log.info(
                "alert_failed_jobs_ok",
                count=result["count"],
                threshold=result["threshold"],
            )
        return 0
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


if __name__ == "__main__":
    sys.exit(main())
