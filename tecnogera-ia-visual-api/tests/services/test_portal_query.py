"""Testes de portal_query.list_jobs, compute_stats e get_job_result — IAVS-032/033/035."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.pipeline import PipelineJob
from app.services.portal_query import JobFilters, compute_stats, get_job_result, list_jobs


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
    checklist_id: str = "111111",
    status: str = "done",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> PipelineJob:
    now = datetime.now(UTC)
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status=status,
        created_at=created_at or now,
        updated_at=updated_at or created_at or now,
    )
    db.add(job)
    db.flush()
    return job


# ── list_jobs básico ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_list_jobs_retorna_todos_sem_filtro(db: Session) -> None:
    _job(db, checklist_id="111111")
    _job(db, checklist_id="222222")
    db.commit()

    jobs, _etag = list_jobs(db, JobFilters())
    assert len(jobs) == 2


@pytest.mark.unit
def test_list_jobs_filtra_por_status_done(db: Session) -> None:
    _job(db, checklist_id="111111", status="done")
    _job(db, checklist_id="222222", status="running")
    db.commit()

    jobs, _ = list_jobs(db, JobFilters(status_filter=["done"]))
    assert len(jobs) == 1
    assert jobs[0].status == "done"


@pytest.mark.unit
def test_list_jobs_filtra_por_multiplos_status(db: Session) -> None:
    _job(db, checklist_id="111111", status="done")
    _job(db, checklist_id="222222", status="running")
    _job(db, checklist_id="333333", status="failed")
    db.commit()

    jobs, _ = list_jobs(db, JobFilters(status_filter=["done", "running"]))
    assert len(jobs) == 2


@pytest.mark.unit
def test_list_jobs_filtra_por_date_from(db: Session) -> None:
    old = datetime(2026, 5, 1, tzinfo=UTC)
    new = datetime(2026, 5, 20, tzinfo=UTC)
    _job(db, checklist_id="111111", created_at=old)
    _job(db, checklist_id="222222", created_at=new)
    db.commit()

    cutoff = datetime(2026, 5, 10, tzinfo=UTC)
    jobs, _ = list_jobs(db, JobFilters(date_from=cutoff))
    assert len(jobs) == 1
    assert jobs[0].checklist_id == "222222"


@pytest.mark.unit
def test_list_jobs_filtra_por_date_to(db: Session) -> None:
    old = datetime(2026, 5, 1, tzinfo=UTC)
    new = datetime(2026, 5, 20, tzinfo=UTC)
    _job(db, checklist_id="111111", created_at=old)
    _job(db, checklist_id="222222", created_at=new)
    db.commit()

    cutoff = datetime(2026, 5, 10, tzinfo=UTC)
    jobs, _ = list_jobs(db, JobFilters(date_to=cutoff))
    assert len(jobs) == 1
    assert jobs[0].checklist_id == "111111"


@pytest.mark.unit
def test_list_jobs_paginacao_limit_offset(db: Session) -> None:
    for i in range(5):
        _job(db, checklist_id=str(100000 + i))
    db.commit()

    jobs_page1, _ = list_jobs(db, JobFilters(limit=2, offset=0))
    jobs_page2, _ = list_jobs(db, JobFilters(limit=2, offset=2))
    assert len(jobs_page1) == 2
    assert len(jobs_page2) == 2
    ids_page1 = {j.checklist_id for j in jobs_page1}
    ids_page2 = {j.checklist_id for j in jobs_page2}
    assert ids_page1.isdisjoint(ids_page2)


# ── ETag ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_etag_estavel_para_mesmo_resultset(db: Session) -> None:
    _job(db, checklist_id="111111")
    db.commit()

    _, etag1 = list_jobs(db, JobFilters())
    _, etag2 = list_jobs(db, JobFilters())
    assert etag1 == etag2


@pytest.mark.unit
def test_etag_muda_quando_novo_job_inserido(db: Session) -> None:
    _job(db, checklist_id="111111")
    db.commit()

    _, etag_before = list_jobs(db, JobFilters())

    _job(db, checklist_id="222222")
    db.commit()

    _, etag_after = list_jobs(db, JobFilters())
    assert etag_before != etag_after


@pytest.mark.unit
def test_etag_lista_vazia(db: Session) -> None:
    _, etag = list_jobs(db, JobFilters())
    assert isinstance(etag, str)
    assert len(etag) == 32  # md5 hex


# ── compute_stats ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_compute_stats_sem_jobs_retorna_zeros(db: Session) -> None:
    stats = compute_stats(db, "2026-05")
    assert stats.total_done == 0
    assert stats.in_progress == 0
    assert stats.failed == 0
    assert stats.total_cost_usd == 0.0
    assert stats.accuracy_last_week is None


@pytest.mark.unit
def test_compute_stats_contadores_por_status(db: Session) -> None:
    may = datetime(2026, 5, 15, tzinfo=UTC)
    _job(db, status="done", created_at=may)
    _job(db, status="done", created_at=may)
    _job(db, status="running", created_at=may)
    _job(db, status="pending", created_at=may)
    _job(db, status="pending_batch", created_at=may)
    _job(db, status="failed", created_at=may)
    db.commit()

    stats = compute_stats(db, "2026-05")
    assert stats.total_done == 2
    assert stats.in_progress == 3
    assert stats.failed == 1


@pytest.mark.unit
def test_compute_stats_custo_total_soma_done_do_mes(db: Session) -> None:
    may = datetime(2026, 5, 15, tzinfo=UTC)
    june = datetime(2026, 6, 1, tzinfo=UTC)

    j1 = _job(db, status="done", created_at=may)
    j2 = _job(db, status="done", created_at=may)
    j3 = _job(db, status="done", created_at=june)   # fora do mês
    j4 = _job(db, status="failed", created_at=may)  # status errado
    db.flush()

    j1.metrics = {"estimated_cost_usd": 0.30}
    j2.metrics = {"estimated_cost_usd": 0.15}
    j3.metrics = {"estimated_cost_usd": 0.50}
    j4.metrics = {"estimated_cost_usd": 0.99}
    db.commit()

    stats = compute_stats(db, "2026-05")
    assert abs(stats.total_cost_usd - 0.45) < 1e-9


@pytest.mark.unit
def test_compute_stats_accuracy_null_sem_eval(db: Session) -> None:
    now = datetime.now(UTC)
    j = _job(db, status="done", created_at=now)
    db.flush()
    j.finished_at = now
    j.metrics = {"estimated_cost_usd": 0.10}  # sem chave "eval"
    db.commit()

    stats = compute_stats(db, now.strftime("%Y-%m"))
    assert stats.accuracy_last_week is None


@pytest.mark.unit
def test_compute_stats_accuracy_media_dos_ultimos_7_dias(db: Session) -> None:
    now = datetime.now(UTC)
    old_finished = now - timedelta(days=8)  # fora da janela de 7 dias

    j1 = _job(db, status="done", created_at=now)
    j2 = _job(db, status="done", created_at=now)
    j_old = _job(db, status="done", created_at=old_finished)
    db.flush()

    j1.finished_at = now
    j1.metrics = {"eval": {"accuracy_global": 0.9}}
    j2.finished_at = now
    j2.metrics = {"eval": {"accuracy_global": 0.8}}
    j_old.finished_at = old_finished
    j_old.metrics = {"eval": {"accuracy_global": 0.5}}  # deve ser excluído
    db.commit()

    stats = compute_stats(db, now.strftime("%Y-%m"))
    assert stats.accuracy_last_week == pytest.approx(0.85)


# ── get_job_result ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_job_result_retorna_none_para_job_inexistente(db: Session) -> None:
    result = get_job_result(db, uuid.uuid4())
    assert result is None


@pytest.mark.unit
def test_get_job_result_job_sem_classifications_retorna_listas_vazias(db: Session) -> None:
    job = _job(db, status="done")
    db.commit()

    result = get_job_result(db, job.id)
    assert result is not None
    assert result.classifications == []
    assert result.inconclusivas == []


@pytest.mark.unit
def test_get_job_result_mapeia_classifications_do_metrics(db: Session) -> None:
    job = _job(db, status="done")
    db.flush()
    job.metrics = {
        "estimated_cost_usd": 0.45,
        "classifications": [
            {
                "image_filename": "foto_c0.jpg",
                "field_name": "c0",
                "confidence": 0.92,
                "is_valid": True,
                "requires_human_review": False,
                "second_best_field": None,
                "second_best_confidence": None,
            },
            {
                "image_filename": "foto_c3.jpg",
                "field_name": "c3",
                "confidence": 0.55,
                "is_valid": False,
                "requires_human_review": True,
                "second_best_field": "c4",
                "second_best_confidence": 0.42,
            },
            {
                "image_filename": "foto_c6.jpg",
                "field_name": "c6",
                "confidence": 0.20,
                "is_valid": False,
                "requires_human_review": False,
                "second_best_field": None,
                "second_best_confidence": None,
            },
        ],
    }
    db.commit()

    result = get_job_result(db, job.id)
    assert result is not None
    assert len(result.classifications) == 3
    assert result.classifications[0].photo_id == "foto_c0.jpg"
    assert result.classifications[0].status == "valid"
    assert result.classifications[1].status == "inconclusive"
    assert result.classifications[1].second_best_field == "c4"
    assert result.classifications[2].status == "excluded"
    assert result.estimated_cost_usd == 0.45


@pytest.mark.unit
def test_get_job_result_inconclusivas_sao_subset_de_classifications(db: Session) -> None:
    job = _job(db, status="done")
    db.flush()
    job.metrics = {
        "classifications": [
            {"image_filename": "f1.jpg", "field_name": "c0", "confidence": 0.95, "is_valid": True, "requires_human_review": False, "second_best_field": None, "second_best_confidence": None},
            {"image_filename": "f2.jpg", "field_name": "c3", "confidence": 0.60, "is_valid": False, "requires_human_review": True, "second_best_field": None, "second_best_confidence": None},
            {"image_filename": "f3.jpg", "field_name": "c4", "confidence": 0.65, "is_valid": False, "requires_human_review": True, "second_best_field": "c3", "second_best_confidence": 0.55},
        ]
    }
    db.commit()

    result = get_job_result(db, job.id)
    assert result is not None
    assert len(result.inconclusivas) == 2
    assert all(i.status == "inconclusive" for i in result.inconclusivas)


@pytest.mark.unit
def test_get_job_result_job_failed_retorna_error_preenchido(db: Session) -> None:
    job = _job(db, status="failed")
    db.flush()
    job.error = "Dropbox connection timeout"
    db.commit()

    result = get_job_result(db, job.id)
    assert result is not None
    assert result.status == "failed"
    assert result.error == "Dropbox connection timeout"
    assert result.classifications == []


@pytest.mark.unit
def test_get_job_result_etag_estavel_para_mesmo_estado(db: Session) -> None:
    job = _job(db, status="done")
    db.commit()

    r1 = get_job_result(db, job.id)
    r2 = get_job_result(db, job.id)
    assert r1 is not None and r2 is not None
    assert r1.etag == r2.etag
    assert len(r1.etag) == 32
