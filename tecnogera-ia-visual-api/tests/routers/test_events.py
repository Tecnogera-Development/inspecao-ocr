"""Testes do endpoint POST /api/v1/events/ingest — IAVS-060."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def sqlite_session(sqlite_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def events_client(settings, sqlite_session: Session) -> TestClient:
    from app.core.config import get_settings

    def _override_db():
        yield sqlite_session

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _override_db
    # Sem pool arq em testes
    app.state.arq_pool = None
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_ingest_retorna_200_com_stats(events_client: TestClient) -> None:
    with patch("app.routers.events.DropboxService") as MockDpx:
        MockDpx.return_value.list_avarias_paths.return_value = [
            "/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg",
            "/Avarias/FROTA001/foto_sem_padrao.jpg",
        ]
        resp = events_client.post("/api/v1/events/ingest")

    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 2
    assert body["queued"] == 1
    assert body["metadata_missing"] == 1
    assert body["skipped"] == 0


@pytest.mark.unit
def test_ingest_retorna_200_sem_novos_arquivos(events_client: TestClient) -> None:
    with patch("app.routers.events.DropboxService") as MockDpx:
        MockDpx.return_value.list_avarias_paths.return_value = []
        resp = events_client.post("/api/v1/events/ingest")

    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 0
    assert body["queued"] == 0


@pytest.mark.unit
def test_ingest_segunda_chamada_skipa_existentes(
    events_client: TestClient,
) -> None:
    path = "/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg"
    with patch("app.routers.events.DropboxService") as MockDpx:
        MockDpx.return_value.list_avarias_paths.return_value = [path]
        resp1 = events_client.post("/api/v1/events/ingest")
        resp2 = events_client.post("/api/v1/events/ingest")

    assert resp1.json()["created"] == 1
    assert resp2.json()["skipped"] == 1
    assert resp2.json()["created"] == 0
