"""Testes de POST /api/v1/portal/run — IAVS-034."""

from __future__ import annotations

from collections.abc import Generator

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.pipeline import PipelineJob
from app.models.user import User


@pytest.fixture
def portal_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        session_secret="test-secret-key-32-chars-minimum!",
    )


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
def db(sqlite_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def portal_client(portal_settings: Settings, db: Session) -> TestClient:
    def _override_db():
        yield db

    app = create_app(portal_settings)
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: portal_settings
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


def _make_user(db: Session, email: str = "celio@tecnogera.com", password: str = "s3cr3t") -> User:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=hashed, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_and_get_csrf(client: TestClient) -> str:
    """Faz login e retorna o CSRF token da sessão."""
    client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )
    return client.get("/api/v1/portal/csrf").json()["token"]


# ── autenticação ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_portal_run_sem_sessao_retorna_401(portal_client: TestClient) -> None:
    resp = portal_client.post("/api/v1/portal/run", json={"checklist_id": "276800"})
    assert resp.status_code == 401


@pytest.mark.unit
def test_portal_run_sem_csrf_retorna_403(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    portal_client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )
    resp = portal_client.post("/api/v1/portal/run", json={"checklist_id": "276800"})
    assert resp.status_code == 403


# ── execução ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_portal_run_sucesso_retorna_202_e_cria_job(
    portal_client: TestClient, db: Session
) -> None:
    from unittest.mock import patch

    _make_user(db)
    csrf = _login_and_get_csrf(portal_client)

    with patch("app.routers.portal.BackgroundTasks.add_task"):
        resp = portal_client.post(
            "/api/v1/portal/run",
            json={"checklist_id": "276800"},
            headers={"X-CSRF-Token": csrf},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "pending"

    job = db.query(PipelineJob).filter_by(checklist_id="276800").first()
    assert job is not None
    assert job.status == "pending"


@pytest.mark.unit
def test_portal_run_dispara_background_task_com_assinatura_correta(
    portal_client: TestClient, db: Session
) -> None:
    """Regressão IAVS-067: o call site NÃO pode passar a Session (db) para a task.

    Diferente do teste acima, NÃO mocka BackgroundTasks.add_task — deixa a task
    real ser agendada/executada, capturando os argumentos. Se o call site voltar
    a passar `db` (4 args para uma função de 3), o spy nunca é chamado e o assert
    falha. Foi esse mock de add_task que escondeu o crash na auditoria.
    """
    from unittest.mock import patch

    from app.core.config import Settings as _Settings

    _make_user(db)
    csrf = _login_and_get_csrf(portal_client)

    captured: dict[str, object] = {}

    async def _spy(job_id: object, checklist_id: str, settings: object) -> None:
        captured["args"] = (job_id, checklist_id, settings)

    with patch("app.routers.portal._run_pipeline_async", _spy):
        resp = portal_client.post(
            "/api/v1/portal/run",
            json={"checklist_id": "276800"},
            headers={"X-CSRF-Token": csrf},
        )

    assert resp.status_code == 202
    assert "args" in captured, "background task não foi chamada com a assinatura esperada (3 args)"
    _job_id, checklist_id, settings = captured["args"]
    assert checklist_id == "276800"
    assert isinstance(settings, _Settings)  # 3º arg é Settings, nunca a Session


@pytest.mark.unit
def test_portal_run_checklist_id_invalido_retorna_422(
    portal_client: TestClient, db: Session
) -> None:
    _make_user(db)
    csrf = _login_and_get_csrf(portal_client)

    resp = portal_client.post(
        "/api/v1/portal/run",
        json={"checklist_id": "abc-invalid"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422
