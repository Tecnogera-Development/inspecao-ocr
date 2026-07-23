"""Testes da rota POST /pipeline/run com mode=batch — IAVS-042."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

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
def pipeline_client(settings, sqlite_session: Session) -> TestClient:
    from app.core.config import get_settings

    def _override_db():
        yield sqlite_session

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_mode_batch_abaixo_threshold_retorna_422(pipeline_client: TestClient) -> None:
    """422 quando mode=batch e checklist tem menos imagens que o threshold."""
    with patch("app.routers.pipeline.DropboxService") as mock_cls:
        mock_cls.return_value.list_checklist_images.return_value = [MagicMock()] * 5
        resp = pipeline_client.post(
            "/api/v1/pipeline/run?mode=batch",
            json={"checklist_id": "276800"},
        )
    assert resp.status_code == 422
    assert "mode=sync" in resp.json()["detail"]


@pytest.mark.unit
def test_mode_batch_acima_threshold_retorna_202(pipeline_client: TestClient) -> None:
    """202 quando mode=batch e checklist tem imagens suficientes (≥ threshold)."""
    with patch("app.routers.pipeline.DropboxService") as mock_cls:
        mock_cls.return_value.list_checklist_images.return_value = [MagicMock()] * 35
        with patch("app.routers.pipeline.BackgroundTasks.add_task"):
            resp = pipeline_client.post(
                "/api/v1/pipeline/run?mode=batch",
                json={"checklist_id": "276800"},
            )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert "job_id" in body
