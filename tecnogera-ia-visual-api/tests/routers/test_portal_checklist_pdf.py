"""``GET /api/v1/portal/checklists/{id}/pdf`` — ticket ``v1-entregavel/05``.

Custo de API: **zero**. Nenhuma chamada a OpenAI/Anthropic acontece aqui. O
Dropbox é substituído por um dublê (`_DropboxFake`) — nunca toca a rede.

**WeasyPrint não carrega no macOS deste checkout** (falta
`libgobject-2.0-0`, mesma causa do `test_pdf_renderer` pré-existente):
`app.services.laudo_pdf.renderizar_pdf` é mockado em todo teste deste
arquivo (fixture `sem_weasyprint`, autouse). O HTML de verdade É montado
(`montar_html` roda sem mock) — só a chamada ao WeasyPrint em si vira um
retorno fixo. A confirmação de que o WeasyPrint gera PDF de verdade a partir
deste MESMO template rodou no Docker, à parte (ver relato da tarefa).

Fixtures deliberadamente self-contidas (mesmo padrão do resto da suíte, ver
``test_portal_checklists_export.py``) para este arquivo não colidir com
outro agente mexendo em ``checklist_query``/``checklist_filter`` em paralelo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import bcrypt
import pytest
from fastapi.testclient import TestClient
from PIL import Image
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
from app.services.dropbox import ResourceNotFoundError

LISTA = "/api/v1/portal/checklists"


def _pdf_url(identificador: str) -> str:
    return f"{LISTA}/{identificador}/pdf"


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


@pytest.fixture(autouse=True)
def sem_weasyprint(monkeypatch: pytest.MonkeyPatch):
    """WeasyPrint não carrega no macOS deste checkout — mocka só o passo final."""
    monkeypatch.setattr(
        "app.services.laudo_pdf.renderizar_pdf",
        lambda html: b"%PDF-1.7 conteudo-fake-do-teste",
    )


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color=(90, 90, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _DropboxFake:
    """Dublê de ``DropboxService`` — nunca toca a rede nem tem método de escrita.

    Registra toda chamada de leitura em ``LEITURAS`` (nível de classe, para o
    teste inspecionar depois de instanciado pela rota). Falha de download só
    para paths que contêm um dos campos em ``FALHAS``.
    """

    LEITURAS: list[str] = []
    FALHAS: frozenset[str] = frozenset()

    def __init__(self, settings: Any = None) -> None:  # noqa: ANN401
        self._settings = settings

    def download_image(self, path: str) -> bytes:
        _DropboxFake.LEITURAS.append(path)
        if any(campo in path for campo in _DropboxFake.FALHAS):
            raise ResourceNotFoundError("arquivo não encontrado", details={"path": path})
        return _jpeg_bytes()


@pytest.fixture(autouse=True)
def dropbox_fake(monkeypatch: pytest.MonkeyPatch):
    _DropboxFake.LEITURAS = []
    _DropboxFake.FALHAS = frozenset()
    monkeypatch.setattr("app.services.dropbox.DropboxService", _DropboxFake)
    return _DropboxFake


# ── helpers de fixture do checklist ─────────────────────────────────────────


def _snapshot(
    checklist_id: str,
    *,
    formulario: str = "F038 - PRÉ LOCAÇÃO DE GERADOR",
    patrimonio: str | None = "TECG007883",
    n_linhas: int = 1,
) -> dict[str, Any]:
    linha = SislocChecklist(
        codigo_checklist=checklist_id,
        formulario=formulario,
        filial="MG-CGE",
        patrimonio=patrimonio,
        projeto="035514/2026-EBAZAR.COM.BR. LTDA",
        responsavel="MATHEUS.PARAISO",
        data_conclusao=datetime(2026, 8, 2, 14, 30, tzinfo=UTC),
        status="Concluído",
        origem="OM",
        numero_om=36729,
        ordem=1,
        n_linhas=n_linhas,
    )
    return linha.snapshot(lido_em=datetime(2026, 8, 2, 15, 0, tzinfo=UTC)).como_json()


def _job(
    db: Session,
    checklist_id: str,
    *,
    conformidade: str | None = "nao_conforme",
    severidade: int | None = 2,
    vista_determinante: str | None = "c54",
    status: str = "done",
    formulario: str = "F038 - PRÉ LOCAÇÃO DE GERADOR",
    patrimonio: str | None = "TECG007883",
    n_linhas: int = 1,
) -> PipelineJob:
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status=status,
        mode="sync",
        conformidade=conformidade,
        severidade_max=severidade,
        vista_determinante=vista_determinante,
        vistas_recebidas="c54,c55,c56,c57",
        formulario=formulario,
        patrimonio=patrimonio,
        projeto="035514/2026-EBAZAR.COM.BR. LTDA",
        n_linhas=n_linhas,
        llm_cost_usd=0.002,
        llm_calls=4,
        created_at=datetime(2026, 8, 2, 16, 0, tzinfo=UTC),
        sisloc_snapshot=_snapshot(
            checklist_id, formulario=formulario, patrimonio=patrimonio, n_linhas=n_linhas
        ),
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
    achados: list[dict[str, Any]] | None = None,
) -> ChecklistViewResult:
    linha = ChecklistViewResult(
        id=uuid.uuid4(),
        job_id=job.id,
        checklist_id=job.checklist_id,
        campo=campo,
        dropbox_path=f"/Sisloc/MG-CGE/{job.checklist_id} 01/{campo} foto 01.jpg",
        status="analisada",
        conformidade=conformidade,
        vista_confere=True,
        achados=achados or [],
        severidade_max=severidade,
        classe=classe,
        tipo_defeito=tipo_defeito,
        confianca=0.87 if severidade else None,
        model_version="gpt-4.1-mini",
        cost_usd=0.002,
    )
    db.add(linha)
    db.commit()
    return linha


def _checklist_padrao(db: Session, checklist_id: str = "310149") -> PipelineJob:
    """F038 completo: c54 não conforme (com achado), c55/c56/c57 conformes."""
    job = _job(db, checklist_id)
    _vista(
        db,
        job,
        "c54",
        conformidade="nao_conforme",
        classe="dano_visivel",
        tipo_defeito="amassado_deformacao",
        severidade=2,
        achados=[
            {
                "classe": "dano_visivel",
                "tipo_defeito": "amassado_deformacao",
                "severidade": 2,
                "local": "quadrante inferior direito",
                "observacao": "Amassado visível na chapa inferior.",
                "confianca": 0.87,
            }
        ],
    )
    _vista(db, job, "c55")
    _vista(db, job, "c56")
    _vista(db, job, "c57")
    return job


# ── autenticação e resolução do identificador ───────────────────────────────


def test_pdf_requer_autenticacao(portal_client, db):
    job = _checklist_padrao(db)
    assert portal_client.get(_pdf_url(str(job.id))).status_code == 401


def test_pdf_404_checklist_inexistente(logado):
    assert logado.get(_pdf_url(str(uuid.uuid4()))).status_code == 404


def test_pdf_404_formulario_f180_trancado(logado, db):
    """Porta trancada do ticket 01: F180 não vira laudo, nem em PDF."""
    job = _job(db, "999001", formulario="F180 - VISITA TÉCNICA")
    _vista(db, job, "c54")
    _vista(db, job, "c55")
    _vista(db, job, "c56")

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 404


def test_pdf_aceita_job_id_ou_codigo_checklist(logado, db):
    job = _checklist_padrao(db, "310149")

    por_job_id = logado.get(_pdf_url(str(job.id)))
    por_codigo = logado.get(_pdf_url("310149"))

    assert por_job_id.status_code == 200
    assert por_codigo.status_code == 200


# ── 409: sem laudo pronto ────────────────────────────────────────────────────


def test_pdf_409_status_nao_done(logado, db):
    job = _job(db, "310150", status="running", conformidade=None)

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 409


def test_pdf_409_sem_analise(logado, db):
    """`conformidade=None` -> indicador `sem_analise`, mesmo com `status=done`."""
    job = _job(db, "310151", status="done", conformidade=None)

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 409


# ── 200: contrato HTTP ───────────────────────────────────────────────────────


def test_pdf_200_content_type_e_bytes_de_pdf(logado, db):
    job = _checklist_padrao(db)

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")


def test_pdf_content_disposition_com_nome_ascii_sem_espaco(logado, db):
    job = _checklist_padrao(db, "310149")

    resp = logado.get(_pdf_url(str(job.id)))

    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "Laudo_TECG007883_2026-08-02_ckl310149.pdf" in disposition
    assert " " not in disposition.split("filename=")[1]


def test_pdf_valida_com_validacao_pendente_ainda_exporta(logado, db):
    """Decisão da definição de produto: marca no rodapé, não bloqueia (ver ticket 04)."""
    job = _checklist_padrao(db)

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 200


def test_pdf_reusa_o_mesmo_dado_da_rota_de_detalhe(logado, db, monkeypatch):
    """O HTML montado tem de refletir o MESMO payload que `GET .../{id}` devolve."""
    job = _checklist_padrao(db)

    detalhe_json = logado.get(f"{LISTA}/{job.id}").json()

    capturado: dict[str, str] = {}

    def _fake_renderizar(html: str) -> bytes:
        capturado["html"] = html
        return b"%PDF-1.7 fake"

    monkeypatch.setattr("app.services.laudo_pdf.renderizar_pdf", _fake_renderizar)

    resp = logado.get(_pdf_url(str(job.id)))
    assert resp.status_code == 200

    html = capturado["html"]
    assert detalhe_json["equipamento"]["patrimonio"] in html
    assert detalhe_json["severidade_rotulo"] in html
    assert "Dano visível" in html  # classe_rotulo do achado de c54


# ── fotos: falha de download não derruba o PDF ──────────────────────────────


def test_pdf_foto_indisponivel_nao_derruba_o_documento(logado, db, dropbox_fake):
    dropbox_fake.FALHAS = frozenset({"c54"})
    job = _checklist_padrao(db)

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")


def test_pdf_todas_as_fotos_indisponiveis_ainda_assim_gera_o_documento(logado, db, dropbox_fake):
    dropbox_fake.FALHAS = frozenset({"c54", "c55", "c56", "c57"})
    job = _checklist_padrao(db)

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 200


# ── Dropbox somente leitura ──────────────────────────────────────────────────


def test_pdf_nao_escreve_no_dropbox(logado, db, dropbox_fake):
    """`_DropboxFake` não implementa NENHUM método de escrita — se a rota do PDF
    tentasse subir algo ao Dropbox, a chamada quebraria com `AttributeError`
    dentro da rota e o teste veria um 500, não um 200."""
    job = _checklist_padrao(db)

    resp = logado.get(_pdf_url(str(job.id)))

    assert resp.status_code == 200
    assert not hasattr(dropbox_fake, "upload_report")
    assert not hasattr(dropbox_fake, "upload_annotated_image")
    assert not hasattr(dropbox_fake, "upload_avaria_image")
    # só leituras foram registradas — uma por vista recebida (4, F038 completo)
    assert len(dropbox_fake.LEITURAS) == 4
    assert all("/Sisloc/" in caminho for caminho in dropbox_fake.LEITURAS)
