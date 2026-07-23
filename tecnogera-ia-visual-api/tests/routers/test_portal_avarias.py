"""Testes dos endpoints /api/v1/portal/avarias/* — IAVS-068."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.event_pair  # noqa: F401
from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.event import Event
from app.models.event_pair import EventPair
from app.models.user import User


# ── fixtures ─────────────────────────────────────────────────────────────────


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
def portal_client(portal_settings: Settings, db: Session) -> TestClient:
    def _override_db():
        yield db

    app = create_app(portal_settings)
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: portal_settings
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_user(db: Session) -> User:
    hashed = bcrypt.hashpw(b"s3cr3t", bcrypt.gensalt()).decode()
    user = User(email="test@tecnogera.com", password_hash=hashed, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient) -> None:
    client.post(
        "/api/v1/portal/login",
        json={"email": "test@tecnogera.com", "password": "s3cr3t"},
    )


def _make_event(
    db: Session,
    asset_code: str = "GER-001",
    moment: str = "saida",
    damage_class: str | None = None,
    damage_severity: str | None = None,
    result_json: dict | None = None,
    captured_at: datetime | None = None,
    checklist_id: str | None = None,
) -> Event:
    ev = Event(
        id=uuid.uuid4(),
        asset_code=asset_code,
        canonical_angle="frontal",
        captured_at=captured_at or datetime(2026, 6, 10, 8, 0, 0, tzinfo=UTC),
        moment=moment,
        uploaded_by="tech01",
        checklist_id=checklist_id,
        source_path=f"/Avarias/{asset_code}/{uuid.uuid4()}.jpg",
        status="done",
        damage_class=damage_class,
        damage_severity=damage_severity,
        result_json=result_json,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def _make_pair(
    db: Session,
    saida: Event | None = None,
    retorno: Event | None = None,
    status: str = "complete",
    asset_code: str = "GER-001",
    pair_date: date = date(2026, 6, 10),
    annotated_image_path: str | None = None,
    created_at: datetime | None = None,
) -> EventPair:
    pair = EventPair(
        id=uuid.uuid4(),
        asset_code=asset_code,
        pair_date=pair_date,
        saida_event_id=saida.id if saida else None,
        retorno_event_id=retorno.id if retorno else None,
        status=status,
        annotated_image_path=annotated_image_path,
        **({"created_at": created_at} if created_at else {}),
    )
    db.add(pair)
    db.commit()
    db.refresh(pair)
    return pair


# ── GET /avarias/pairs ────────────────────────────────────────────────────────


def test_pairs_lista_vazia(portal_client, db):
    _make_user(db)
    _login(portal_client)
    r = portal_client.get("/api/v1/portal/avarias/pairs")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_pairs_requer_autenticacao(portal_client):
    r = portal_client.get("/api/v1/portal/avarias/pairs")
    assert r.status_code == 401


def test_pairs_lista_par_completo(portal_client, db):
    _make_user(db)
    saida = _make_event(
        db, moment="saida", damage_class="dano_visivel", damage_severity="alta",
        checklist_id="276800",
    )
    retorno = _make_event(db, moment="retorno")
    _make_pair(db, saida=saida, retorno=retorno, annotated_image_path="/Avarias/_anotados/GER-001_2026-06-10.jpg")

    _login(portal_client)
    r = portal_client.get("/api/v1/portal/avarias/pairs")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["asset_code"] == "GER-001"
    assert item["status"] == "complete"
    assert item["saida_damage_class"] == "dano_visivel"
    assert item["saida_damage_severity"] == "alta"
    assert item["retorno_damage_class"] is None
    assert item["has_non_conformity"] is True
    assert item["checklist_id"] == "276800"
    assert item["annotated_image_path"] == "/Avarias/_anotados/GER-001_2026-06-10.jpg"


def test_pairs_conforme_has_non_conformity_false(portal_client, db):
    _make_user(db)
    saida = _make_event(db, moment="saida", damage_class=None)
    retorno = _make_event(db, moment="retorno", damage_class=None)
    _make_pair(db, saida=saida, retorno=retorno)

    _login(portal_client)
    r = portal_client.get("/api/v1/portal/avarias/pairs")
    assert r.status_code == 200
    assert r.json()["items"][0]["has_non_conformity"] is False


def test_pairs_filtro_por_status(portal_client, db):
    _make_user(db)
    saida = _make_event(db, moment="saida")
    _make_pair(db, saida=saida, status="partial")
    _make_pair(db, asset_code="GER-002", pair_date=date(2026, 6, 11))

    _login(portal_client)
    r = portal_client.get("/api/v1/portal/avarias/pairs?status=partial")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "partial"


def test_pairs_filtro_por_asset_code(portal_client, db):
    _make_user(db)
    _make_pair(db, asset_code="GER-001")
    _make_pair(db, asset_code="GER-002", pair_date=date(2026, 6, 11))

    _login(portal_client)
    r = portal_client.get("/api/v1/portal/avarias/pairs?asset_code=GER-002")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["asset_code"] == "GER-002"


def test_pairs_paginacao(portal_client, db):
    _make_user(db)
    for i in range(3):
        _make_pair(db, asset_code=f"GER-{i:03d}", pair_date=date(2026, 6, 10 + i))

    _login(portal_client)
    r = portal_client.get("/api/v1/portal/avarias/pairs?limit=2&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2


def test_pairs_ordenacao_processamento_desc(portal_client, db):
    # Ordena por data de PROCESSAMENTO (created_at) desc — mais recente primeiro.
    _make_user(db)
    # GER-001: captura mais antiga, porém processado por ÚLTIMO
    _make_pair(
        db, asset_code="GER-001", pair_date=date(2026, 6, 10),
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )
    # GER-002: captura mais nova, processado ANTES
    _make_pair(
        db, asset_code="GER-002", pair_date=date(2026, 6, 12),
        created_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
    )

    _login(portal_client)
    r = portal_client.get("/api/v1/portal/avarias/pairs")
    assert r.status_code == 200
    items = r.json()["items"]
    # processado por último aparece primeiro, mesmo com pair_date mais antigo
    assert items[0]["asset_code"] == "GER-001"
    assert items[1]["asset_code"] == "GER-002"


# ── GET /avarias/pairs/{pair_id} ──────────────────────────────────────────────


def test_pair_detail_completo(portal_client, db):
    _make_user(db)
    saida = _make_event(
        db, moment="saida",
        damage_class="dano_visivel",
        result_json={"no_conformity": True, "canonical_angle": "frontal"},
    )
    retorno = _make_event(db, moment="retorno")
    pair = _make_pair(db, saida=saida, retorno=retorno)

    _login(portal_client)
    r = portal_client.get(f"/api/v1/portal/avarias/pairs/{pair.id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(pair.id)
    assert data["status"] == "complete"
    assert data["saida"]["moment"] == "saida"
    assert data["saida"]["damage_class"] == "dano_visivel"
    assert data["saida"]["result_json"]["no_conformity"] is True
    assert data["retorno"]["moment"] == "retorno"


def test_pair_detail_partial(portal_client, db):
    _make_user(db)
    saida = _make_event(db, moment="saida")
    pair = _make_pair(db, saida=saida, retorno=None, status="partial")

    _login(portal_client)
    r = portal_client.get(f"/api/v1/portal/avarias/pairs/{pair.id}")
    assert r.status_code == 200
    data = r.json()
    assert data["retorno"] is None
    assert data["saida"]["moment"] == "saida"


def test_pair_detail_nao_encontrado(portal_client, db):
    _make_user(db)
    _login(portal_client)
    r = portal_client.get(f"/api/v1/portal/avarias/pairs/{uuid.uuid4()}")
    assert r.status_code == 404


def test_pair_detail_requer_auth(portal_client, db):
    _make_pair(db)
    r = portal_client.get(f"/api/v1/portal/avarias/pairs/{uuid.uuid4()}")
    assert r.status_code == 401


# ── GET /avarias/image ────────────────────────────────────────────────────────


def test_image_proxy_retorna_jpeg(portal_client, db):
    _make_user(db)
    _login(portal_client)

    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (100, 80), (128, 128, 128)).save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    with patch("app.services.dropbox.DropboxService") as mock_cls:
        mock_cls.return_value.download_image.return_value = jpeg_bytes
        r = portal_client.get(
            "/api/v1/portal/avarias/image",
            params={"path": "/Avarias/GER-001/20260610_saida.jpg"},
        )

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == jpeg_bytes


def test_image_proxy_rejeita_path_invalido(portal_client, db):
    _make_user(db)
    _login(portal_client)
    # Fora dos prefixos permitidos (/Avarias/ ou /Sisloc/)
    r = portal_client.get(
        "/api/v1/portal/avarias/image",
        params={"path": "/etc/passwd"},
    )
    assert r.status_code == 422


def test_image_proxy_aceita_sisloc(portal_client, db):
    """Fotos do checklist de entrega (/Sisloc/) são servidas como base de comparação."""
    _make_user(db)
    _login(portal_client)

    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (10, 20, 30)).save(buf, format="JPEG")
    jpeg = buf.getvalue()

    with patch("app.services.dropbox.DropboxService") as mock_cls:
        mock_cls.return_value.download_image.return_value = jpeg
        r = portal_client.get(
            "/api/v1/portal/avarias/image",
            params={"path": "/Sisloc/FILIAL SP/entrega.jpg"},
        )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_image_proxy_404_quando_nao_encontrado(portal_client, db):
    _make_user(db)
    _login(portal_client)
    with patch("app.services.dropbox.DropboxService") as mock_cls:
        from app.services.dropbox import ResourceNotFoundError
        mock_cls.return_value.download_image.side_effect = ResourceNotFoundError(
            "not found", details={}
        )
        r = portal_client.get(
            "/api/v1/portal/avarias/image",
            params={"path": "/Avarias/GER-001/missing.jpg"},
        )
    assert r.status_code == 404


# ── POST /avarias/upload — validação de asset_code (auditoria) ─────────────────


def _csrf(client: TestClient) -> str:
    return client.get("/api/v1/portal/csrf").json()["token"]


@pytest.mark.parametrize("bad_asset", ["../etc", "GER/001", "a b", "..", "GER_001", "x;y"])
def test_upload_asset_code_invalido_retorna_422(portal_client, db, bad_asset):
    """asset_code fora do whitelist [A-Za-z0-9-] é rejeitado antes de tocar o Dropbox."""
    _make_user(db)
    _login(portal_client)
    token = _csrf(portal_client)
    resp = portal_client.post(
        "/api/v1/portal/avarias/upload",
        data={
            "asset_code": bad_asset,
            "checklist_id": "276800",
            "moment": "retorno",
            "angle": "c145",
            "uploader": "portal",
        },
        files={"foto": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 422


def test_upload_asset_code_valido_passa_da_validacao(portal_client, db):
    """asset_code com hífen (ex.: GER-001) é aceito; falha adiante (Dropbox mockado)."""
    _make_user(db)
    _login(portal_client)
    token = _csrf(portal_client)
    with patch("app.services.dropbox.DropboxService") as mock_cls:
        mock_cls.return_value.upload_avaria_image.return_value = (
            "/Avarias/GER-001/20260610_120000_retorno_c145_portal_276800.jpg"
        )
        resp = portal_client.post(
            "/api/v1/portal/avarias/upload",
            data={
                "asset_code": "GER-001",
                "checklist_id": "276800",
                "moment": "retorno",
                "angle": "c145",
                "uploader": "portal",
            },
            files={"foto": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")},
            headers={"X-CSRF-Token": token},
        )
    # Não é 422 de asset_code — passou da validação (202 queued/metadata_missing).
    assert resp.status_code != 422


def test_image_proxy_requer_auth(portal_client):
    r = portal_client.get(
        "/api/v1/portal/avarias/image",
        params={"path": "/Avarias/GER-001/x.jpg"},
    )
    assert r.status_code == 401
