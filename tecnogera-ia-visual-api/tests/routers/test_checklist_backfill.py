"""POST /api/v1/checklists/backfill — ticket mvp-c54-c57/11.

Dropbox e Sisloc são mockados no módulo do router; **nenhuma chamada de LLM**
acontece aqui nem no código sob teste (o backfill só cria jobs ``pending``).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.dropbox import ImageMetadata
from app.models.pipeline import PipelineJob
from app.models.sisloc import SislocChecklist
from app.services.dropbox import parse_filename

pytestmark = pytest.mark.unit

URL = "/api/v1/checklists/backfill"


@pytest.fixture
def sqlite_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _client(settings: Settings, db: Session) -> TestClient:
    def _override_db() -> Generator[Session, None, None]:
        yield db

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _override_db
    app.state.arq_pool = None
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def backfill_client(settings: Settings, sqlite_session: Session) -> TestClient:
    return _client(settings, sqlite_session)


def _imagem(checklist_id: str, campo: str) -> ImageMetadata:
    nome = f"153269005_checklist_{checklist_id}_{campo}_0_15_02_2026 09_00_00.jpeg"
    return ImageMetadata(
        dropbox_path=f"/Sisloc/MG - CGE/Checklist/Chk/{nome}",
        filename=nome,
        size_bytes=1234,
        parsed=parse_filename(nome),
        server_modified=datetime(2026, 2, 15, 9, 0, 0),
    )


def _mocks(por_id: dict[str, list[str]], formularios: dict[str, str]):
    """Patcha Dropbox e Sisloc no módulo do router. Somente leitura, sem rede."""
    dropbox = MagicMock()
    dropbox.list_checklist_images.side_effect = lambda cid: [
        _imagem(cid, campo) for campo in por_id.get(cid, [])
    ]
    sisloc = MagicMock()
    sisloc.fetch_checklists.return_value = {
        cid: SislocChecklist(codigo_checklist=cid, formulario=form, status="Concluído")
        for cid, form in formularios.items()
    }
    return (
        patch("app.routers.checklists.DropboxService", return_value=dropbox),
        patch("app.routers.checklists.SislocService", return_value=sisloc),
    )


# ── caminho feliz ─────────────────────────────────────────────────────────────


def test_id_valido_devolve_202_e_cria_job(
    backfill_client: TestClient, sqlite_session: Session
) -> None:
    p_dpx, p_sis = _mocks({"278749": ["c54", "c55", "c56"]}, {"278749": "F038 - PRÉ LOC"})
    with p_dpx, p_sis:
        resp = backfill_client.post(URL, json={"checklist_ids": ["278749"]})

    assert resp.status_code == 202
    body = resp.json()
    assert body["aceitos"] == 1
    assert body["recusados"] == 0
    assert body["teto_por_requisicao"] == 20
    assert body["chamadas_visao_estimadas"] == 3
    assert len(body["job_ids"]) == 1
    assert body["itens"][0]["tentativa"] == 1
    assert "custo de LLM" in body["aviso"]

    job = sqlite_session.query(PipelineJob).one()
    assert job.checklist_id == "278749"
    assert job.status == "pending"


def test_reprocessamento_cria_execucao_nova(
    backfill_client: TestClient, sqlite_session: Session
) -> None:
    anterior = uuid.uuid4()
    sqlite_session.add(
        PipelineJob(id=anterior, checklist_id="300", status="done", mode="sync")
    )
    sqlite_session.commit()

    p_dpx, p_sis = _mocks({"300": ["c54", "c55", "c56", "c57"]}, {"300": "F038 - PRÉ LOC"})
    with p_dpx, p_sis:
        resp = backfill_client.post(URL, json={"checklist_ids": ["300"]})

    assert resp.status_code == 202
    item = resp.json()["itens"][0]
    assert item["reprocessamento"] is True
    assert item["tentativa"] == 2
    assert item["job_id"] != str(anterior)
    assert sqlite_session.query(PipelineJob).count() == 2


# ── recusas ───────────────────────────────────────────────────────────────────


def test_campo_faltante_devolve_422_dizendo_qual_vista(backfill_client: TestClient) -> None:
    p_dpx, p_sis = _mocks({"301": ["c54", "c56"]}, {"301": "F038"})
    with p_dpx, p_sis:
        resp = backfill_client.post(URL, json={"checklist_ids": ["301"]})

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["aceitos"] == 0
    item = detail["itens"][0]
    assert item["motivo"] == "campo_faltante:c55"
    assert item["campos_faltantes"] == ["c55"]
    assert "lateral esquerda" in item["detalhe"]


def test_formulario_fora_da_whitelist_devolve_422(backfill_client: TestClient) -> None:
    p_dpx, p_sis = _mocks({"302": ["c54", "c55", "c56"]}, {"302": "F013 - CHECKLIST"})
    with p_dpx, p_sis:
        resp = backfill_client.post(URL, json={"checklist_ids": ["302"]})

    assert resp.status_code == 422
    item = resp.json()["detail"]["itens"][0]
    assert item["motivo"] == "formulario_fora_whitelist:F013"
    assert "F013" in item["detalhe"]


def test_id_inexistente_na_view_devolve_422_explicando(backfill_client: TestClient) -> None:
    p_dpx, p_sis = _mocks({"303": ["c54", "c55", "c56"]}, {})
    with p_dpx, p_sis:
        resp = backfill_client.post(URL, json={"checklist_ids": ["303"]})

    assert resp.status_code == 422
    item = resp.json()["detail"]["itens"][0]
    assert item["motivo"] == "formulario_ausente"
    assert "dbo.checklist_produto" in item["detalhe"]


def test_lote_misto_devolve_202_com_o_desfecho_de_cada_id(
    backfill_client: TestClient, sqlite_session: Session
) -> None:
    p_dpx, p_sis = _mocks(
        {"304": ["c54", "c55", "c56"], "305": ["c54"]}, {"304": "F038", "305": "F038"}
    )
    with p_dpx, p_sis:
        resp = backfill_client.post(URL, json={"checklist_ids": ["304", "305"]})

    assert resp.status_code == 202
    body = resp.json()
    assert (body["aceitos"], body["recusados"]) == (1, 1)
    assert sqlite_session.query(PipelineJob).count() == 1


# ── guarda-corpo de lote ──────────────────────────────────────────────────────


def test_teto_de_lote_excedido_devolve_422_com_mensagem_clara(
    sqlite_session: Session,
) -> None:
    cfg = Settings(
        _env_file=None, app_env=AppEnv.TEST, log_level="DEBUG", checklist_backfill_max_ids=2
    )
    client = _client(cfg, sqlite_session)

    p_dpx, p_sis = _mocks({}, {})
    with p_dpx, p_sis:
        resp = client.post(URL, json={"checklist_ids": ["1", "2", "3"]})

    assert resp.status_code == 422
    erro = resp.json()["error"]
    assert erro["code"] == "domain_error"
    assert "teto de 2" in erro["message"]
    assert erro["details"] == {"solicitados": 3, "teto": 2}
    assert sqlite_session.query(PipelineJob).count() == 0


def test_lista_vazia_e_rejeitada_pelo_schema(backfill_client: TestClient) -> None:
    resp = backfill_client.post(URL, json={"checklist_ids": []})
    assert resp.status_code == 422


def test_id_nao_numerico_e_rejeitado_com_mensagem_util(backfill_client: TestClient) -> None:
    resp = backfill_client.post(URL, json={"checklist_ids": ["abc"]})
    assert resp.status_code == 422
    assert "dígitos" in resp.text


# ── proteção ──────────────────────────────────────────────────────────────────


def test_sem_api_key_devolve_401_quando_configurada(sqlite_session: Session) -> None:
    cfg = Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        log_level="DEBUG",
        pipeline_api_key=SecretStr("segredo"),
    )
    client = _client(cfg, sqlite_session)

    p_dpx, p_sis = _mocks({"306": ["c54", "c55", "c56"]}, {"306": "F038"})
    with p_dpx, p_sis:
        sem_chave = client.post(URL, json={"checklist_ids": ["306"]})
        errada = client.post(
            URL, json={"checklist_ids": ["306"]}, headers={"X-API-Key": "outra"}
        )
        certa = client.post(
            URL, json={"checklist_ids": ["306"]}, headers={"X-API-Key": "segredo"}
        )

    assert sem_chave.status_code == 401
    assert errada.status_code == 401
    assert certa.status_code == 202
    assert sqlite_session.query(PipelineJob).count() == 1
