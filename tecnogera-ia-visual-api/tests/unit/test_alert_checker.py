"""Testes de alert_checker — IAVS-052 (E6 alerta jobs failed)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.pipeline import PipelineJob


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(engine) -> Session:
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _job(
    db: Session,
    *,
    status: str = "done",
    created_at: datetime | None = None,
) -> PipelineJob:
    now = datetime.now(UTC)
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id="111111",
        status=status,
        created_at=created_at or now,
        updated_at=created_at or now,
    )
    db.add(job)
    db.flush()
    return job


# ── count_failed_jobs ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_count_failed_jobs_retorna_zero_sem_jobs_failed(db: Session) -> None:
    from app.services.alert_checker import count_failed_jobs

    _job(db, status="done")
    db.commit()

    assert count_failed_jobs(db) == 0


@pytest.mark.unit
def test_count_failed_jobs_conta_apenas_failed_recentes(db: Session) -> None:
    from app.services.alert_checker import count_failed_jobs

    now = datetime.now(UTC)
    _job(db, status="failed", created_at=now)
    _job(db, status="failed", created_at=now - timedelta(minutes=30))
    _job(db, status="done", created_at=now)
    db.commit()

    assert count_failed_jobs(db, hours=1) == 2


@pytest.mark.unit
def test_count_failed_jobs_ignora_failed_antigos(db: Session) -> None:
    from app.services.alert_checker import count_failed_jobs

    now = datetime.now(UTC)
    _job(db, status="failed", created_at=now)
    _job(db, status="failed", created_at=now - timedelta(hours=2))  # antigo
    db.commit()

    assert count_failed_jobs(db, hours=1) == 1


# ── check_and_alert ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_check_and_alert_nao_alerta_quando_abaixo_threshold(
    db: Session, tmp_path: Path
) -> None:
    from app.services.alert_checker import check_and_alert

    now = datetime.now(UTC)
    _job(db, status="failed", created_at=now)
    _job(db, status="failed", created_at=now)
    db.commit()

    log_path = tmp_path / "alerts.log"
    result = check_and_alert(db, threshold=3, log_path=log_path)

    assert result["alerted"] is False
    assert result["count"] == 2
    assert result["threshold"] == 3
    assert not log_path.exists()


@pytest.mark.unit
def test_check_and_alert_alerta_quando_acima_threshold(
    db: Session, tmp_path: Path
) -> None:
    from app.services.alert_checker import check_and_alert

    now = datetime.now(UTC)
    for _ in range(4):
        _job(db, status="failed", created_at=now)
    db.commit()

    log_path = tmp_path / "alerts.log"
    result = check_and_alert(db, threshold=3, log_path=log_path)

    assert result["alerted"] is True
    assert result["count"] == 4


@pytest.mark.unit
def test_check_and_alert_escreve_json_no_log(db: Session, tmp_path: Path) -> None:
    from app.services.alert_checker import check_and_alert

    now = datetime.now(UTC)
    for _ in range(4):
        _job(db, status="failed", created_at=now)
    db.commit()

    log_path = tmp_path / "alerts.log"
    check_and_alert(db, threshold=3, log_path=log_path)

    assert log_path.exists()
    line = log_path.read_text().strip()
    data = json.loads(line)
    assert data["count"] == 4
    assert data["threshold"] == 3
    assert "ts" in data


@pytest.mark.unit
def test_check_and_alert_json_tem_campos_obrigatorios(
    db: Session, tmp_path: Path
) -> None:
    from app.services.alert_checker import check_and_alert

    now = datetime.now(UTC)
    for _ in range(4):
        _job(db, status="failed", created_at=now)
    db.commit()

    log_path = tmp_path / "alerts.log"
    check_and_alert(db, threshold=3, log_path=log_path)

    data = json.loads(log_path.read_text().strip())
    assert set(data.keys()) >= {"ts", "count", "threshold"}


@pytest.mark.unit
def test_check_and_alert_nao_envia_email_sem_email_to(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import alert_checker

    calls: list = []
    monkeypatch.setattr(alert_checker, "_send_ssmtp_email", lambda *a, **kw: calls.append(1))

    now = datetime.now(UTC)
    for _ in range(4):
        _job(db, status="failed", created_at=now)
    db.commit()

    alert_checker.check_and_alert(db, threshold=3, log_path=tmp_path / "alerts.log")
    assert len(calls) == 0


@pytest.mark.unit
def test_check_and_alert_envia_email_quando_email_to_configurado(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import alert_checker

    calls: list = []
    monkeypatch.setattr(alert_checker, "_send_ssmtp_email", lambda *a, **kw: calls.append((a, kw)))

    now = datetime.now(UTC)
    for _ in range(4):
        _job(db, status="failed", created_at=now)
    db.commit()

    alert_checker.check_and_alert(
        db, threshold=3, log_path=tmp_path / "alerts.log", email_to="ops@tecnogera.com"
    )
    assert len(calls) == 1


# ── settings ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_settings_alert_threshold_default() -> None:
    from app.core.config import Settings

    cfg = Settings(_env_file=None)
    assert cfg.alert_failed_jobs_threshold == 3


@pytest.mark.unit
def test_settings_alert_email_to_default_none() -> None:
    from app.core.config import Settings

    cfg = Settings(_env_file=None)
    assert cfg.alert_email_to is None
