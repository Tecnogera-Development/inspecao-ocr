"""Testes para PairingService (IAVS-064)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.event_pair  # noqa: F401 — registra EventPair no metadata
from app.db.base import Base
from app.models.event import Event
from app.models.event_pair import EventPair
from app.services.pairing_service import PairingService, ReconcileResult


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _event(
    asset_code: str = "GER-001",
    moment: str = "saida",
    status: str = "done",
    captured_at: datetime | None = None,
) -> Event:
    ts = captured_at or datetime(2026, 6, 10, 8, 0, 0, tzinfo=UTC)
    date_str = ts.strftime("%Y%m%d_%H%M%S")
    return Event(
        id=uuid.uuid4(),
        asset_code=asset_code,
        canonical_angle="frontal",
        captured_at=ts,
        moment=moment,
        uploaded_by="tech01",
        source_path=f"/Avarias/{asset_code}/{date_str}_{moment}_frontal_tech01.jpg",
        status=status,
    )


def _persisted(db, **kwargs) -> Event:
    ev = _event(**kwargs)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


# ── reconcile_event eligibilidade ────────────────────────────────────────────


def test_evento_nao_done_ignorado(db):
    ev = _persisted(db, status="processing")
    result = PairingService(db).reconcile_event(ev)
    assert result is None
    assert db.query(EventPair).count() == 0


def test_evento_sem_moment_ignorado(db):
    ev = _persisted(db, moment=None, status="done")
    result = PairingService(db).reconcile_event(ev)
    assert result is None


def test_evento_sem_captured_at_ignorado(db):
    ev = _event(moment="saida", status="done")
    ev.captured_at = None
    db.add(ev)
    db.commit()
    result = PairingService(db).reconcile_event(ev)
    assert result is None


# ── reconcile_event criação de par ───────────────────────────────────────────


def test_saida_cria_par_partial(db):
    ev = _persisted(db, moment="saida")
    pair = PairingService(db).reconcile_event(ev)

    assert pair is not None
    assert pair.status == "partial"
    assert pair.saida_event_id == ev.id
    assert pair.retorno_event_id is None
    assert pair.asset_code == ev.asset_code
    assert pair.pair_date == date(2026, 6, 10)


def test_retorno_cria_par_partial(db):
    ev = _persisted(db, moment="retorno")
    pair = PairingService(db).reconcile_event(ev)

    assert pair is not None
    assert pair.status == "partial"
    assert pair.retorno_event_id == ev.id
    assert pair.saida_event_id is None


# ── reconcile_event completar par ────────────────────────────────────────────


def test_retorno_completa_par_existente(db):
    saida = _persisted(db, moment="saida")
    retorno = _persisted(db, moment="retorno")
    svc = PairingService(db)

    pair_after_saida = svc.reconcile_event(saida)
    assert pair_after_saida.status == "partial"

    pair_after_retorno = svc.reconcile_event(retorno)
    assert pair_after_retorno.status == "complete"
    assert pair_after_retorno.saida_event_id == saida.id
    assert pair_after_retorno.retorno_event_id == retorno.id
    assert pair_after_retorno.id == pair_after_saida.id


def test_saida_completa_par_com_retorno_existente(db):
    retorno = _persisted(db, moment="retorno")
    saida = _persisted(db, moment="saida")
    svc = PairingService(db)

    svc.reconcile_event(retorno)
    pair = svc.reconcile_event(saida)

    assert pair.status == "complete"
    assert pair.saida_event_id == saida.id
    assert pair.retorno_event_id == retorno.id


# ── idempotência ─────────────────────────────────────────────────────────────


def test_reconcile_event_idempotente(db):
    ev = _persisted(db, moment="saida")
    svc = PairingService(db)

    svc.reconcile_event(ev)
    svc.reconcile_event(ev)  # segunda chamada não deve criar novo par

    assert db.query(EventPair).count() == 1


def test_par_completo_idempotente(db):
    saida = _persisted(db, moment="saida")
    retorno = _persisted(db, moment="retorno")
    svc = PairingService(db)

    svc.reconcile_event(saida)
    svc.reconcile_event(retorno)
    svc.reconcile_event(saida)   # terceira chamada não deve alterar nada
    svc.reconcile_event(retorno)

    assert db.query(EventPair).count() == 1
    pair = db.query(EventPair).first()
    assert pair.status == "complete"


# ── múltiplos ativos ─────────────────────────────────────────────────────────


def test_dois_ativos_criam_pares_separados(db):
    ev1 = _persisted(db, asset_code="GER-001", moment="saida")
    ev2 = _persisted(db, asset_code="GER-002", moment="saida")
    svc = PairingService(db)

    svc.reconcile_event(ev1)
    svc.reconcile_event(ev2)

    assert db.query(EventPair).count() == 2


def test_mesmo_ativo_dias_diferentes_cria_pares_separados(db):
    ev1 = _persisted(
        db, moment="saida", captured_at=datetime(2026, 6, 10, 8, 0, 0, tzinfo=UTC)
    )
    ev2 = _event(moment="saida", captured_at=datetime(2026, 6, 11, 8, 0, 0, tzinfo=UTC))
    db.add(ev2)
    db.commit()

    svc = PairingService(db)
    svc.reconcile_event(ev1)
    svc.reconcile_event(ev2)

    assert db.query(EventPair).count() == 2


# ── reconcile_all ─────────────────────────────────────────────────────────────


def test_reconcile_all_vazio(db):
    result = PairingService(db).reconcile_all()
    assert isinstance(result, ReconcileResult)
    assert result.pairs_created == 0
    assert result.pairs_completed == 0


def test_reconcile_all_processa_unpaired(db):
    saida = _persisted(db, moment="saida")
    retorno = _persisted(db, moment="retorno")

    result = PairingService(db).reconcile_all()

    assert db.query(EventPair).count() == 1
    pair = db.query(EventPair).first()
    assert pair.status == "complete"
    assert result.pairs_created + result.pairs_completed > 0


def test_reconcile_all_ignora_ja_pareados(db):
    saida = _persisted(db, moment="saida")
    retorno = _persisted(db, moment="retorno")
    svc = PairingService(db)

    svc.reconcile_event(saida)
    svc.reconcile_event(retorno)

    result = svc.reconcile_all()

    assert result.pairs_skipped == 2
    assert db.query(EventPair).count() == 1


def test_reconcile_all_ignora_nao_done(db):
    ev = _persisted(db, status="processing", moment="saida")
    result = PairingService(db).reconcile_all()
    assert result.pairs_created == 0
    assert db.query(EventPair).count() == 0


# ── pair_date ──────────────────────────────────────────────────────────────


def test_pair_date_extraido_de_captured_at(db):
    ev = _persisted(db, captured_at=datetime(2026, 3, 15, 12, 30, 0, tzinfo=UTC))
    pair = PairingService(db).reconcile_event(ev)
    assert pair.pair_date == date(2026, 3, 15)
