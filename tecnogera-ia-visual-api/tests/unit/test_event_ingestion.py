"""Testes de EventIngestionService — IAVS-060."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.event import Event
from app.services.event_ingestion import EventIngestionService


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    yield db
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _make_dropbox_mock(*paths: str) -> MagicMock:
    mock = MagicMock()
    mock.list_avarias_paths.return_value = list(paths)
    return mock


@pytest.mark.unit
def test_ingest_evento_valido_cria_queued(in_memory_db, settings) -> None:
    dropbox = _make_dropbox_mock(
        "/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg"
    )
    svc = EventIngestionService(db=in_memory_db, dropbox=dropbox, settings=settings)
    result = svc.scan_and_ingest()

    assert result.created == 1
    assert result.queued == 1
    assert result.metadata_missing == 0
    assert result.skipped == 0

    event = in_memory_db.query(Event).first()
    assert event is not None
    assert event.status == "queued"
    assert event.asset_code == "FROTA001"
    assert event.moment == "saida"


@pytest.mark.unit
def test_ingest_metadados_ausentes_cria_metadata_missing(in_memory_db, settings) -> None:
    dropbox = _make_dropbox_mock("/Avarias/FROTA001/foto_sem_padrao.jpg")
    svc = EventIngestionService(db=in_memory_db, dropbox=dropbox, settings=settings)
    result = svc.scan_and_ingest()

    assert result.created == 1
    assert result.queued == 0
    assert result.metadata_missing == 1

    event = in_memory_db.query(Event).first()
    assert event.status == "metadata_missing"
    assert event.asset_code == "FROTA001"


@pytest.mark.unit
def test_ingest_dedup_por_source_path(in_memory_db, settings) -> None:
    """Mesmo path ingerido duas vezes → segundo é ignorado."""
    path = "/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg"
    dropbox = _make_dropbox_mock(path)
    svc = EventIngestionService(db=in_memory_db, dropbox=dropbox, settings=settings)

    result1 = svc.scan_and_ingest()
    result2 = svc.scan_and_ingest()

    assert result1.created == 1
    assert result2.created == 0
    assert result2.skipped == 1

    total = in_memory_db.query(Event).count()
    assert total == 1


@pytest.mark.unit
def test_ingest_multiplos_paths(in_memory_db, settings) -> None:
    dropbox = _make_dropbox_mock(
        "/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg",   # válido
        "/Avarias/FROTA001/foto_sem_padrao.jpg",                       # metadata_missing
        "/Avarias/FROTA002/20260601_150000_retorno_traseira_tec01.jpg",  # válido
    )
    svc = EventIngestionService(db=in_memory_db, dropbox=dropbox, settings=settings)
    result = svc.scan_and_ingest()

    assert result.created == 3
    assert result.queued == 2
    assert result.metadata_missing == 1
    assert len(result.queued_ids) == 2


@pytest.mark.unit
def test_ingest_path_invalido_ignorado_sem_erro(in_memory_db, settings) -> None:
    """Path fora de /Avarias → ignorado (ValueError capturado) sem derrubar o lote."""
    dropbox = _make_dropbox_mock(
        "/Sisloc/FROTA001/20260601_143022_saida_frontal_joao.jpg",  # fora da raiz
        "/Avarias/FROTA001/20260601_143022_retorno_traseira_tec01.jpg",  # válido
    )
    svc = EventIngestionService(db=in_memory_db, dropbox=dropbox, settings=settings)
    result = svc.scan_and_ingest()

    assert result.created == 1
    assert result.queued == 1


@pytest.mark.unit
def test_ingest_ignora_pasta_de_sistema(in_memory_db, settings) -> None:
    """Compostos em /Avarias/_anotados não viram eventos (pasta de sistema)."""
    dropbox = _make_dropbox_mock(
        "/Avarias/_anotados/GER-001_2026-06-10.jpg",  # artefato do pipeline
        "/Avarias/GER-001/20260610_083000_saida_frontal_tec01.jpg",  # válido
    )
    svc = EventIngestionService(db=in_memory_db, dropbox=dropbox, settings=settings)
    result = svc.scan_and_ingest()

    assert result.created == 1
    assert result.queued == 1


@pytest.mark.unit
def test_queued_ids_sao_uuids_validos(in_memory_db, settings) -> None:
    dropbox = _make_dropbox_mock(
        "/Avarias/X/20260101_000000_saida_frontal_tec01.jpg"
    )
    svc = EventIngestionService(db=in_memory_db, dropbox=dropbox, settings=settings)
    result = svc.scan_and_ingest()

    assert len(result.queued_ids) == 1
    eid = result.queued_ids[0]
    assert isinstance(eid, uuid.UUID)
