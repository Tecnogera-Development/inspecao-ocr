"""Corte de produto para F038 — corte de produto para F038.

O pedido da Tecnogera foi "filtrar apenas o F038": não é filtro de tela, é
corte de produto. ``FORMULARIOS_ALVO`` (``app/services/checklist_filter.py``)
vira fonte única, e ``checklist_query`` (lista e detalhe do portal) passa a
obedecer a ela — porta trancada, nem por ``?formulario=F180``.

Custo de API: **zero**. Leitura de banco e serialização; nenhuma chamada a
OpenAI ou Anthropic acontece aqui.

Fixtures deliberadamente duplicadas de ``test_portal_checklists.py`` (mesmo
padrão do resto da suíte, ver ``test_portal_checklist_hitl.py``) — arquivo novo
para não inchar o de colisão com o ticket 02, que mexe em
``app/routers/portal.py`` em paralelo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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

LISTA = "/api/v1/portal/checklists"


# ── fixtures ──────────────────────────────────────────────────────────────────


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


@pytest.fixture
def logado(portal_client: TestClient, db: Session) -> TestClient:
    hashed = bcrypt.hashpw(b"s3cr3t", bcrypt.gensalt()).decode()
    db.add(User(email="test@tecnogera.com", password_hash=hashed, is_active=True))
    db.commit()
    portal_client.post(
        "/api/v1/portal/login",
        json={"email": "test@tecnogera.com", "password": "s3cr3t"},
    )
    return portal_client


# ── helpers ───────────────────────────────────────────────────────────────────


def _job(
    db: Session,
    checklist_id: str,
    *,
    formulario: str,
    conformidade: str | None = "conforme",
    filial: str | None = "MG-CGE",
    vistas: str | None = "c54,c55,c56",
) -> PipelineJob:
    """Job mínimo — só o que os testes deste arquivo precisam: formulário."""
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status="done",
        mode="sync",
        conformidade=conformidade,
        vistas_recebidas=vistas,
        formulario=formulario,
        created_at=datetime(2026, 8, 2, 16, 0, tzinfo=UTC),
        sisloc_snapshot={"filial": filial, "formulario": formulario},
    )
    db.add(job)
    db.commit()
    return job


def _f180(db: Session, checklist_id: str = "311989", **kwargs) -> PipelineJob:
    """Um job antigo, do formulário que saiu do produto na v1."""
    return _job(db, checklist_id, formulario="F180-VISITA GMG_REV04", **kwargs)


def _f038(db: Session, checklist_id: str, **kwargs) -> PipelineJob:
    return _job(db, checklist_id, formulario="F038 - PRÉ LOCAÇÃO DE GERADOR", **kwargs)


# ── porta trancada: lista ────────────────────────────────────────────────────


def test_f180_nao_aparece_na_lista(logado, db):
    """Job F180 já gravado no banco (era o formulário dominante) some da tela."""
    _f180(db, "311989")
    _f038(db, "400100")

    corpo = logado.get(LISTA).json()
    assert [i["checklist_id"] for i in corpo["itens"]] == ["400100"]
    assert corpo["total"] == 1


def test_f180_nao_aparece_nem_pedindo_formulario_f180_explicitamente(logado, db):
    """``?formulario=`` só ESTREITA dentro do conjunto alvo — nunca amplia."""
    _f180(db, "311989")

    corpo = logado.get(LISTA, params={"formulario": "F180"}).json()
    assert corpo["total"] == 0
    assert corpo["itens"] == []


def test_f180_nao_conta_nos_contadores_do_topo(logado, db):
    """A âncora de volume de trabalho não pode incluir o que a tela não mostra."""
    _f180(db, "311989", conformidade="nao_conforme")
    _f038(db, "400100", conformidade="nao_conforme")

    contadores = logado.get(LISTA).json()["contadores"]
    assert contadores["total"] == 1
    assert contadores["nao_conformes"] == 1


def test_f180_nao_aparece_mesmo_sem_nenhum_filtro_de_indicador(logado, db):
    """Job F180 sem análise nenhuma (``sem_analise``) também some — não é só
    quem já tem laudo."""
    _f180(db, "311777", conformidade=None, vistas=None)

    corpo = logado.get(LISTA).json()
    assert corpo["total"] == 0


# ── porta trancada: detalhe ──────────────────────────────────────────────────


def test_detalhe_de_job_f180_e_404(logado, db):
    """``GET /portal/checklists/{job_id}`` de um job F180 devolve 404."""
    job = _f180(db, "311989")

    r = logado.get(f"{LISTA}/{job.id}")
    assert r.status_code == 404


def test_detalhe_de_job_f180_por_codigo_de_checklist_e_404(logado, db):
    """A rota também aceita ``codigo_checklist`` — a porta é a mesma."""
    _f180(db, "311989")

    r = logado.get(f"{LISTA}/311989")
    assert r.status_code == 404


def test_detalhe_de_job_f038_continua_acessivel(logado, db):
    """Controle: o corte não derruba o que É do conjunto alvo."""
    job = _f038(db, "400100")

    r = logado.get(f"{LISTA}/{job.id}")
    assert r.status_code == 200
    assert r.json()["checklist_id"] == "400100"


# ── facetas ───────────────────────────────────────────────────────────────────


def test_facetas_nao_oferecem_f180_como_opcao(logado, db):
    """``facetas.formularios`` é a fonte que o front usa para esconder o
    seletor (ticket 03) — não pode listar um formulário que a lista não mostra.
    """
    _f180(db, "311989", filial="SP-GRU")
    _f038(db, "400100", filial="MG-CGE")

    facetas = logado.get(LISTA).json()["facetas"]
    assert facetas["formularios"] == ["F038"]
    # A filial do job F180 também não vaza para o seletor: ele não está no
    # escopo visível, então oferecer o filtro seria um beco sem saída.
    assert facetas["filiais"] == ["MG-CGE"]
