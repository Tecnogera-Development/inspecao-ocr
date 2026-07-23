"""alert_checker — conta jobs failed e dispara alerta se acima do threshold.

Interface pública:
    count_failed_jobs(db, hours=1) -> int
    check_and_alert(db, threshold, log_path, email_to=None) -> dict
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineJob


def count_failed_jobs(db: Session, hours: int = 1) -> int:
    """Retorna total de jobs com status='failed' criados nas últimas `hours` horas."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    result = db.execute(
        select(func.count()).where(
            PipelineJob.status == "failed",
            PipelineJob.created_at >= cutoff,
        )
    )
    return result.scalar_one()


def check_and_alert(
    db: Session,
    threshold: int,
    log_path: Path | str,
    email_to: str | None = None,
) -> dict[str, Any]:
    """Verifica jobs failed. Se count > threshold, escreve JSON em log_path.

    Retorna {"alerted": bool, "count": int, "threshold": int}.
    """
    count = count_failed_jobs(db)
    alerted = count > threshold

    if alerted:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "count": count,
            "threshold": threshold,
        }
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        if email_to:
            _send_ssmtp_email(email_to, count, threshold)

    return {"alerted": alerted, "count": count, "threshold": threshold}


def _send_ssmtp_email(email_to: str, count: int, threshold: int) -> None:
    """Envia email de alerta via ssmtp (best-effort, falha silenciosamente)."""
    subject = f"[ALERTA] {count} jobs failed na última hora (threshold: {threshold})"
    body = (
        f"To: {email_to}\n"
        f"Subject: {subject}\n"
        f"\n"
        f"{count} jobs falharam na última hora. Threshold configurado: {threshold}.\n"
        f"Verifique os logs em /var/log/tecnogera/alerts.log\n"
    )
    try:
        subprocess.run(
            ["ssmtp", email_to],
            input=body,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
