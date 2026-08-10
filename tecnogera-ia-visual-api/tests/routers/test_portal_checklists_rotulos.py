"""`classe_rotulo` / `tipo_defeito_rotulo` no contrato do detalhe — ticket
``v1-entregavel/02``.

Arquivo separado de ``test_portal_checklists.py`` de propósito: outro agente
edita ``checklist_filter.py``/``checklist_query.py`` em paralelo (ticket
``v1-entregavel/01``) e inchar o arquivo compartilhado só aumentaria o risco
de conflito de merge sem necessidade.

Custo de API: **zero**. Tudo aqui é leitura de banco e serialização; nenhuma
chamada a OpenAI ou Anthropic acontece nestes testes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

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
from app.models.checklist_analysis import ChecklistViewResult
from app.models.pipeline import PipelineJob
from app.models.sisloc import SislocChecklist
from app.models.user import User

LISTA = "/api/v1/portal/checklists"


# ── fixtures (self-contidas de propósito — ver docstring do módulo) ────────────


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


def _snapshot(checklist_id: str) -> dict[str, Any]:
    linha = SislocChecklist(
        codigo_checklist=checklist_id,
        formulario="F038 - PRÉ LOCAÇÃO DE GERADOR",
        filial="MG-CGE",
        patrimonio="TECG01364",
        projeto="035514/2026-EBAZAR.COM.BR. LTDA",
        responsavel="MATHEUS.PARAISO",
        data_conclusao=datetime(2026, 8, 2, 14, 30, tzinfo=UTC),
        status="Concluído",
        origem="OM",
        numero_om=36729,
        ordem=1,
        n_linhas=1,
    )
    return linha.snapshot(lido_em=datetime(2026, 8, 2, 15, 0, tzinfo=UTC)).como_json()


def _job(
    db: Session,
    checklist_id: str,
    *,
    conformidade: str | None = "nao_conforme",
    severidade: int | None = 2,
    vista_determinante: str | None = "c54",
) -> PipelineJob:
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status="done",
        mode="sync",
        conformidade=conformidade,
        severidade_max=severidade,
        vista_determinante=vista_determinante,
        vistas_recebidas="c54,c55,c56",
        formulario="F038 - PRÉ LOCAÇÃO DE GERADOR",
        patrimonio="TECG01364",
        projeto="035514/2026-EBAZAR.COM.BR. LTDA",
        n_linhas=1,
        llm_cost_usd=0.002,
        llm_calls=3,
        created_at=datetime(2026, 8, 2, 16, 0, tzinfo=UTC),
        sisloc_snapshot=_snapshot(checklist_id),
    )
    db.add(job)
    db.commit()
    return job


def _vista(
    db: Session,
    job: PipelineJob,
    campo: str,
    *,
    conformidade: str = "conforme",
    classe: str | None = None,
    tipo_defeito: str | None = None,
    severidade: int | None = None,
    confianca: float | None = None,
    achados: list[dict[str, Any]] | None = None,
) -> ChecklistViewResult:
    linha = ChecklistViewResult(
        id=uuid.uuid4(),
        job_id=job.id,
        checklist_id=job.checklist_id,
        campo=campo,
        dropbox_path=f"/Sisloc/MG-CGE/{job.checklist_id} 01/{campo} foto.jpg",
        status="analisada",
        conformidade=conformidade,
        vista_confere=True,
        conteudo_observado="Gerador em pátio de obra.",
        achados=achados or [],
        severidade_max=severidade,
        classe=classe,
        tipo_defeito=tipo_defeito,
        confianca=confianca,
        model_version="gpt-4.1-mini",
        cost_usd=0.002,
    )
    db.add(linha)
    db.commit()
    return linha


def _detalhe(logado: TestClient, job: PipelineJob) -> dict[str, Any]:
    r = logado.get(f"{LISTA}/{job.id}")
    assert r.status_code == 200
    return r.json()


# ── vistas[]: classe_rotulo / tipo_defeito_rotulo no nível da vista ────────────


def test_vista_traz_classe_rotulo_e_tipo_defeito_rotulo(logado, db):
    job = _job(db, "311989")
    _vista(
        db,
        job,
        "c54",
        conformidade="nao_conforme",
        classe="dano_visivel",
        tipo_defeito="amassado_deformacao",
        severidade=2,
        confianca=0.87,
        achados=[
            {
                "classe": "dano_visivel",
                "tipo_defeito": "amassado_deformacao",
                "severidade": 2,
                "local": "quadrante inferior direito",
                "observacao": "Amassado visível.",
                "confianca": 0.87,
            }
        ],
    )
    _vista(db, job, "c55")
    _vista(db, job, "c56")

    corpo = _detalhe(logado, job)
    vista = next(v for v in corpo["vistas"] if v["campo"] == "c54")
    assert vista["classe"] == "dano_visivel"
    assert vista["classe_rotulo"] == "Dano visível"
    assert vista["tipo_defeito"] == "amassado_deformacao"
    assert vista["tipo_defeito_rotulo"] == "Amassado / deformação"


# ── achados[] DENTRO de cada vista — o ponto que o contrato ainda não cobria ───


def test_achados_da_vista_tambem_trazem_os_rotulos(logado, db):
    """`ChecklistViewResponse.achados[]` é uma lista separada da `achados[]` da
    raiz — o mesmo achado passa duas vezes pela serialização, e as duas
    precisam do rótulo. Antes deste ticket só a lista da raiz vinha enriquecida."""
    job = _job(db, "311989")
    _vista(
        db,
        job,
        "c54",
        conformidade="nao_conforme",
        classe="dano_visivel",
        tipo_defeito="corrosao_ferrugem",
        severidade=3,
        confianca=0.7,
        achados=[
            {
                "classe": "dano_visivel",
                "tipo_defeito": "corrosao_ferrugem",
                "severidade": 3,
                "local": "quina do teto",
                "observacao": "Mancha laranja com textura.",
                "confianca": 0.7,
            }
        ],
    )
    _vista(db, job, "c55")
    _vista(db, job, "c56")

    corpo = _detalhe(logado, job)
    vista = next(v for v in corpo["vistas"] if v["campo"] == "c54")
    assert len(vista["achados"]) == 1
    achado = vista["achados"][0]
    assert achado["classe_rotulo"] == "Dano visível"
    assert achado["tipo_defeito_rotulo"] == "Corrosão / ferrugem"


# ── achados[] da raiz — já existia, mantém coberto ──────────────────────────────


def test_achados_da_raiz_trazem_os_rotulos(logado, db):
    job = _job(db, "311989")
    _vista(
        db,
        job,
        "c54",
        conformidade="nao_conforme",
        classe="ausencia_item",
        tipo_defeito="componente_ausente",
        severidade=2,
        confianca=0.8,
        achados=[
            {
                "classe": "ausencia_item",
                "tipo_defeito": "componente_ausente",
                "severidade": 2,
                "local": "fecho da porta",
                "observacao": "Fecho ausente.",
                "confianca": 0.8,
            }
        ],
    )
    _vista(db, job, "c55")
    _vista(db, job, "c56")

    corpo = _detalhe(logado, job)
    assert len(corpo["achados"]) == 1
    achado = corpo["achados"][0]
    assert achado["classe_rotulo"] == "Ausência de item"
    assert achado["tipo_defeito_rotulo"] == "Componente ausente"


# ── fallback: taxonomia evoluiu antes do rótulo, e não pode quebrar a rota ──────


def test_valor_fora_do_mapa_nao_quebra_e_ganha_fallback(logado, db):
    """`classe`/`tipo_defeito` persistidos não são validados contra o enum atual
    (o laudo é histórico) — um valor novo, gravado por uma versão futura do
    prompt, não pode derrubar a rota nem sair em snake_case cru."""
    job = _job(db, "311989")
    _vista(
        db,
        job,
        "c54",
        conformidade="nao_conforme",
        classe="risco_novo_nao_mapeado",
        tipo_defeito="vidro_trincado",
        severidade=3,
        confianca=0.65,
        achados=[
            {
                "classe": "risco_novo_nao_mapeado",
                "tipo_defeito": "vidro_trincado",
                "severidade": 3,
                "local": "visor do painel",
                "observacao": "Trinca no vidro.",
                "confianca": 0.65,
            }
        ],
    )
    _vista(db, job, "c55")
    _vista(db, job, "c56")

    r = logado.get(f"{LISTA}/{job.id}")
    assert r.status_code == 200
    corpo = r.json()

    vista = next(v for v in corpo["vistas"] if v["campo"] == "c54")
    assert vista["classe_rotulo"] == "Risco novo nao mapeado"
    assert vista["tipo_defeito_rotulo"] == "Vidro trincado"
    assert vista["achados"][0]["classe_rotulo"] == "Risco novo nao mapeado"
    assert vista["achados"][0]["tipo_defeito_rotulo"] == "Vidro trincado"
    assert corpo["achados"][0]["classe_rotulo"] == "Risco novo nao mapeado"
    assert corpo["achados"][0]["tipo_defeito_rotulo"] == "Vidro trincado"


def test_vista_sem_achado_nao_tem_rotulo_nem_explode(logado, db):
    job = _job(db, "311776", conformidade="conforme", severidade=None, vista_determinante=None)
    _vista(db, job, "c54")
    _vista(db, job, "c55")
    _vista(db, job, "c56")

    corpo = _detalhe(logado, job)
    vista = next(v for v in corpo["vistas"] if v["campo"] == "c54")
    assert vista["classe"] is None
    assert vista["classe_rotulo"] is None
    assert vista["tipo_defeito"] is None
    assert vista["tipo_defeito_rotulo"] is None
    assert vista["achados"] == []
    assert corpo["achados"] == []
