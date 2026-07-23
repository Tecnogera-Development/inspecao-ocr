"""Testes para ArtifactService e _build_composite (IAVS-065)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.event_pair  # noqa: F401
from app.db.base import Base
from app.models.event import Event
from app.models.event_pair import EventPair
from app.services.artifact_service import ArtifactService, _build_composite


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


def _jpeg(w: int = 320, h: int = 240, color: tuple = (128, 128, 128)) -> bytes:
    """Gera JPEG sintético em memória."""
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_event(
    db,
    moment: str = "saida",
    result_json: dict | None = None,
    asset_code: str = "GER-001",
    captured_at: datetime | None = None,
) -> Event:
    ev = Event(
        id=uuid.uuid4(),
        asset_code=asset_code,
        canonical_angle="frontal",
        captured_at=captured_at or datetime(2026, 6, 10, 8, 0, 0, tzinfo=UTC),
        moment=moment,
        uploaded_by="tech01",
        source_path=f"/Avarias/{asset_code}/20260610_{moment}.jpg",
        status="done",
        result_json=result_json,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def _make_pair(db, saida: Event, retorno: Event) -> EventPair:
    pair = EventPair(
        id=uuid.uuid4(),
        asset_code=saida.asset_code,
        pair_date=date(2026, 6, 10),
        saida_event_id=saida.id,
        retorno_event_id=retorno.id,
        status="complete",
    )
    db.add(pair)
    db.commit()
    db.refresh(pair)
    return pair


def _dropbox_mock(saida_bytes: bytes, retorno_bytes: bytes) -> MagicMock:
    mock = MagicMock()
    mock.download_image.side_effect = [saida_bytes, retorno_bytes]
    mock.upload_annotated_image.return_value = "/Avarias/_anotados/GER-001_2026-06-10.jpg"
    return mock


# ── _build_composite ──────────────────────────────────────────────────────────


def test_composite_retorna_bytes_jpeg():
    saida = _jpeg(320, 240, (200, 200, 200))
    retorno = _jpeg(320, 240, (100, 100, 200))
    result = _build_composite(
        saida_bytes=saida,
        retorno_bytes=retorno,
        saida_info={"no_conformity": False, "canonical_angle": "frontal"},
        retorno_info={"no_conformity": True, "damage_class": "dano_visivel", "damage_severity": "alta", "canonical_angle": "frontal"},
        asset_code="GER-001",
        pair_date=date(2026, 6, 10),
    )
    assert isinstance(result, bytes)
    img = Image.open(io.BytesIO(result))
    assert img.format == "JPEG"


def test_composite_largura_e_soma_das_metades():
    saida = _jpeg(400, 300)
    retorno = _jpeg(400, 300)
    result = _build_composite(
        saida_bytes=saida,
        retorno_bytes=retorno,
        saida_info={},
        retorno_info={},
        asset_code="GER-001",
        pair_date=date(2026, 6, 10),
    )
    img = Image.open(io.BytesIO(result))
    # Both images are resized to 480px height, keeping aspect ratio
    # 400/300 * 480 = 640px each side + divider
    assert img.width > 400  # pelo menos mais largo que uma metade


def test_composite_imagens_diferentes_tamanhos():
    saida = _jpeg(640, 480)
    retorno = _jpeg(320, 240)
    result = _build_composite(
        saida_bytes=saida,
        retorno_bytes=retorno,
        saida_info={},
        retorno_info={},
        asset_code="GER-002",
        pair_date=date(2026, 6, 11),
    )
    img = Image.open(io.BytesIO(result))
    # Both resized to same target height
    from app.services.artifact_service import _HEADER_H, _CAPTION_H, _TARGET_HEIGHT
    assert img.height == _HEADER_H + _TARGET_HEIGHT + _CAPTION_H


# ── ArtifactService ───────────────────────────────────────────────────────────


def test_generate_composite_par_completo(db):
    saida_ev = _make_event(db, moment="saida", result_json={"no_conformity": False, "canonical_angle": "frontal"})
    retorno_ev = _make_event(db, moment="retorno", result_json={"no_conformity": True, "damage_class": "dano_visivel", "canonical_angle": "frontal"})
    pair = _make_pair(db, saida_ev, retorno_ev)

    dropbox = _dropbox_mock(_jpeg(), _jpeg())
    settings = MagicMock()
    settings.dropbox_annotated_path = "/Avarias/_anotados"

    svc = ArtifactService(db, dropbox, settings)
    path = svc.generate_composite(pair)

    assert path == "/Avarias/_anotados/GER-001_2026-06-10.jpg"
    assert pair.annotated_image_path == path
    dropbox.upload_annotated_image.assert_called_once()


def test_generate_composite_par_partial_retorna_none(db):
    saida_ev = _make_event(db, moment="saida")
    pair = EventPair(
        id=uuid.uuid4(),
        asset_code="GER-001",
        pair_date=date(2026, 6, 10),
        saida_event_id=saida_ev.id,
        retorno_event_id=None,
        status="partial",
    )
    db.add(pair)
    db.commit()

    svc = ArtifactService(db, MagicMock(), MagicMock())
    result = svc.generate_composite(pair)
    assert result is None


def test_generate_composite_idempotente(db):
    saida_ev = _make_event(db, moment="saida")
    retorno_ev = _make_event(db, moment="retorno")
    pair = _make_pair(db, saida_ev, retorno_ev)
    pair.annotated_image_path = "/Avarias/_anotados/existing.jpg"
    db.commit()

    dropbox = MagicMock()
    svc = ArtifactService(db, dropbox, MagicMock())
    result = svc.generate_composite(pair)

    assert result == "/Avarias/_anotados/existing.jpg"
    dropbox.download_image.assert_not_called()
    dropbox.upload_annotated_image.assert_not_called()


def test_generate_composite_persiste_path_no_banco(db):
    saida_ev = _make_event(db, moment="saida")
    retorno_ev = _make_event(db, moment="retorno")
    pair = _make_pair(db, saida_ev, retorno_ev)

    dropbox = _dropbox_mock(_jpeg(), _jpeg())
    svc = ArtifactService(db, dropbox, MagicMock())
    path = svc.generate_composite(pair)

    db.refresh(pair)
    assert pair.annotated_image_path == path


def test_generate_composite_evento_ausente_retorna_none(db):
    pair = EventPair(
        id=uuid.uuid4(),
        asset_code="GER-001",
        pair_date=date(2026, 6, 10),
        saida_event_id=uuid.uuid4(),   # ID inexistente
        retorno_event_id=uuid.uuid4(),
        status="complete",
    )
    db.add(pair)
    db.commit()

    svc = ArtifactService(db, MagicMock(), MagicMock())
    result = svc.generate_composite(pair)
    assert result is None


def test_generate_composite_result_json_none_nao_falha(db):
    saida_ev = _make_event(db, moment="saida", result_json=None)
    retorno_ev = _make_event(db, moment="retorno", result_json=None)
    pair = _make_pair(db, saida_ev, retorno_ev)

    dropbox = _dropbox_mock(_jpeg(), _jpeg())
    svc = ArtifactService(db, dropbox, MagicMock())
    path = svc.generate_composite(pair)
    assert path is not None
