"""Testes de GET /api/v1/portal/photos/{photo_id}/thumb — IAVS-036."""

from __future__ import annotations

import io
from pathlib import Path

import bcrypt
import pytest
from fastapi.testclient import TestClient
from PIL import Image as PilImage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
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
def db(sqlite_engine) -> Session:
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def tmp_work_dir(tmp_path: Path) -> Path:
    return tmp_path / "checklists"


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "thumbs"


@pytest.fixture
def portal_client(portal_settings: Settings, db: Session, tmp_work_dir: Path, tmp_cache_dir: Path) -> TestClient:
    def _override_db():
        yield db

    app = create_app(portal_settings)
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: portal_settings
    app.dependency_overrides[get_db] = _override_db

    from app.routers import portal as portal_module
    app.dependency_overrides[portal_module._get_thumb_dirs] = lambda: (tmp_work_dir, tmp_cache_dir)

    return TestClient(app, raise_server_exceptions=False)


def _make_user(db: Session, email: str = "celio@tecnogera.com", password: str = "s3cr3t") -> User:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=hashed, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient) -> None:
    client.post(
        "/api/v1/portal/login",
        json={"email": "celio@tecnogera.com", "password": "s3cr3t"},
    )


def _make_jpeg(path: Path, width: int = 400, height: int = 300) -> None:
    img = PilImage.new("RGB", (width, height), color=(100, 150, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="JPEG")


PHOTO_ID = "153074915_checklist_276800_c145_0_09_04_2026 18_03_00.jpeg"


# --------------------------------------------------------------------------- #
# Cycle 7 — 401 sem sessão                                                    #
# --------------------------------------------------------------------------- #


def test_thumb_sem_sessao_retorna_401(portal_client: TestClient) -> None:
    resp = portal_client.get(f"/api/v1/portal/photos/{PHOTO_ID}/thumb?w=240")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Cycle 8 — 422 para w inválido                                               #
# --------------------------------------------------------------------------- #


def test_thumb_w_invalido_retorna_422(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)

    resp = portal_client.get(f"/api/v1/portal/photos/{PHOTO_ID}/thumb?w=999")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Cycle 9 — 404 se foto não existe                                            #
# --------------------------------------------------------------------------- #


def test_thumb_foto_ausente_retorna_404(portal_client: TestClient, db: Session) -> None:
    _make_user(db)
    _login(portal_client)

    resp = portal_client.get(f"/api/v1/portal/photos/{PHOTO_ID}/thumb?w=240")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Cycle 10 — 200 retorna JPEG com Cache-Control e ETag                        #
# --------------------------------------------------------------------------- #


def test_thumb_retorna_200_com_jpeg_e_headers(
    portal_client: TestClient, db: Session, tmp_work_dir: Path
) -> None:
    _make_user(db)
    _login(portal_client)
    _make_jpeg(tmp_work_dir / "276800" / PHOTO_ID)

    resp = portal_client.get(f"/api/v1/portal/photos/{PHOTO_ID}/thumb?w=240")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert "private" in resp.headers["cache-control"]
    assert "max-age=86400" in resp.headers["cache-control"]
    assert "ETag" in resp.headers or "etag" in resp.headers

    img = PilImage.open(io.BytesIO(resp.content))
    assert img.format == "JPEG"
