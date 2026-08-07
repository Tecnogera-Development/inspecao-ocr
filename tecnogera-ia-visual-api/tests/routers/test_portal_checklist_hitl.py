"""HITL — o operador confirma ou corrige o veredito. Ticket ``mvp-c54-c57/10``.

Custo de API: **zero**. Persistência, endpoint e serialização; nenhuma chamada a
OpenAI ou Anthropic acontece aqui. SQLite em memória.

O que estes testes protegem, em ordem de quanto dói errar:

1. **Idempotência.** Validar duas vezes não pode duplicar linha nem inflar o
   eval — é a diferença entre uma métrica de aceite e um número inventado.
2. **O tipo do erro é capturado.** Os quatro tipos, um a um. "Corrigido" sem
   dizer o quê só serve para contar.
3. **A lista passa a devolver dados** em ``confirmado``/``corrigido``, e o
   contador "a validar" acompanha.
4. **Escrita exige CSRF**, ao contrário das rotas de leitura da mesma tela.
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
from app.models.checklist_analysis import (
    STATUS_ANALISADA,
    STATUS_FALHOU,
    ChecklistViewResult,
)
from app.models.pipeline import PipelineJob
from app.models.user import User

LISTA = "/api/v1/portal/checklists"
EVAL = "/api/v1/portal/checklists/eval"

pytestmark = pytest.mark.unit


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
    db.add(User(email="operador@tecnogera.com", password_hash=hashed, is_active=True))
    db.commit()
    portal_client.post(
        "/api/v1/portal/login",
        json={"email": "operador@tecnogera.com", "password": "s3cr3t"},
    )
    return portal_client


@pytest.fixture
def csrf(logado: TestClient) -> str:
    return logado.get("/api/v1/portal/csrf").json()["token"]


# ── helpers ───────────────────────────────────────────────────────────────────


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
        created_at=datetime(2026, 8, 2, 16, 0, tzinfo=UTC),
    )
    db.add(job)
    db.commit()
    return job


def _vista(
    db: Session,
    job: PipelineJob,
    campo: str,
    *,
    status: str = STATUS_ANALISADA,
    conformidade: str | None = "conforme",
    classe: str | None = None,
    tipo_defeito: str | None = None,
    severidade: int | None = None,
    confianca: float | None = None,
) -> ChecklistViewResult:
    achados: list[dict[str, Any]] = []
    if classe:
        achados = [
            {
                "classe": classe,
                "tipo_defeito": tipo_defeito,
                "severidade": severidade,
                "local": "quadrante inferior direito",
                "observacao": "Amassado visível na chapa inferior.",
                "confianca": confianca,
            }
        ]
    linha = ChecklistViewResult(
        id=uuid.uuid4(),
        job_id=job.id,
        checklist_id=job.checklist_id,
        campo=campo,
        dropbox_path=f"/Sisloc/MG-CGE/{job.checklist_id} 01/{campo} foto.jpg",
        status=status,
        conformidade=conformidade,
        vista_confere=True,
        achados=achados,
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


def _checklist_nao_conforme(db: Session, checklist_id: str = "311989") -> PipelineJob:
    """c54 com amassado (dano_visivel, sev. 2); c55 e c56 conformes."""
    job = _job(db, checklist_id)
    _vista(
        db,
        job,
        "c54",
        conformidade="nao_conforme",
        classe="dano_visivel",
        tipo_defeito="amassado_deformacao",
        severidade=2,
        confianca=0.87,
    )
    _vista(db, job, "c55")
    _vista(db, job, "c56")
    return job


def _linhas(db: Session, job: PipelineJob) -> dict[str, ChecklistViewResult]:
    db.expire_all()
    return {
        linha.campo: linha
        for linha in db.query(ChecklistViewResult)
        .filter(ChecklistViewResult.job_id == job.id)
        .all()
    }


def _confirmar(client: TestClient, csrf: str, alvo: str):
    return client.post(f"{LISTA}/{alvo}/confirmar", headers={"X-CSRF-Token": csrf})


def _corrigir(client: TestClient, csrf: str, alvo: str, **body: Any):
    return client.post(
        f"{LISTA}/{alvo}/corrigir", json=body, headers={"X-CSRF-Token": csrf}
    )


# ── autenticação e CSRF ───────────────────────────────────────────────────────


def test_confirmar_sem_sessao_401(portal_client: TestClient):
    r = portal_client.post(f"{LISTA}/{uuid.uuid4()}/confirmar")
    assert r.status_code in (401, 403)


def test_confirmar_sem_csrf_403(logado: TestClient, db: Session):
    """As rotas de leitura da tela são GET e dispensam CSRF; estas não."""
    job = _checklist_nao_conforme(db)
    assert logado.post(f"{LISTA}/{job.id}/confirmar").status_code == 403


def test_corrigir_sem_csrf_403(logado: TestClient, db: Session):
    job = _checklist_nao_conforme(db)
    r = logado.post(
        f"{LISTA}/{job.id}/corrigir", json={"campo": "c54", "tipo_erro": "falso_positivo"}
    )
    assert r.status_code == 403


def test_confirmar_checklist_inexistente_404(logado: TestClient, csrf: str):
    assert _confirmar(logado, csrf, str(uuid.uuid4())).status_code == 404


# ── confirmação: um clique, checklist inteiro ─────────────────────────────────


def test_confirmar_e_um_clique_e_cobre_todas_as_vistas(
    logado: TestClient, csrf: str, db: Session
):
    job = _checklist_nao_conforme(db)

    r = _confirmar(logado, csrf, str(job.id))
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["validacao"] == "confirmado"
    assert corpo["vistas_validadas"] == 3
    assert corpo["vistas_validaveis"] == 3
    assert corpo["vistas_corrigidas"] == 0
    assert corpo["validado_por"] == "operador@tecnogera.com"
    assert corpo["validado_em"] is not None

    linhas = _linhas(db, job)
    # o gabarito de cada vista é a própria predição
    assert linhas["c54"].gt_classe == "dano_visivel"
    assert linhas["c54"].gt_severidade == 2
    assert linhas["c55"].gt_classe == "conforme"
    assert linhas["c56"].gt_classe == "conforme"
    assert all(linha.gt_tipo_erro is None for linha in linhas.values())


def test_confirmar_por_codigo_de_checklist(logado: TestClient, csrf: str, db: Session):
    """O operador conhece o número do Sisloc — a rota aceita os dois."""
    job = _checklist_nao_conforme(db, "311989")
    assert _confirmar(logado, csrf, "311989").json()["validacao"] == "confirmado"
    db.expire_all()
    assert db.get(PipelineJob, job.id).validacao == "confirmado"


def test_confirmar_registra_quem_validou_e_quando_no_detalhe(
    logado: TestClient, csrf: str, db: Session
):
    job = _checklist_nao_conforme(db)
    _confirmar(logado, csrf, str(job.id))

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    assert corpo["validacao"] == "confirmado"
    assert corpo["validado_por"] == "operador@tecnogera.com"
    assert corpo["validado_em"] is not None

    c54 = next(v for v in corpo["vistas"] if v["campo"] == "c54")
    assert c54["validacao"]["estado"] == "confirmado"
    assert c54["validacao"]["por"] == "operador@tecnogera.com"
    assert c54["validacao"]["tipo_erro"] is None


def test_confirmar_checklist_sem_laudo_422(logado: TestClient, csrf: str, db: Session):
    """Job criado e nunca processado não tem o que confirmar."""
    job = _job(db, "311900", conformidade=None, severidade=None, vista_determinante=None)

    r = _confirmar(logado, csrf, str(job.id))
    assert r.status_code == 422
    assert "não tem laudo" in r.json()["detail"]
    db.expire_all()
    assert db.get(PipelineJob, job.id).validacao in (None, "pendente")


def test_vista_que_falhou_nao_entra_no_gabarito(logado: TestClient, csrf: str, db: Session):
    """Erro de download não é erro de classificação — não pode virar métrica."""
    job = _job(db, "311950", conformidade="conforme", severidade=None, vista_determinante=None)
    _vista(db, job, "c54")
    _vista(db, job, "c55", status=STATUS_FALHOU, conformidade=None)

    corpo = _confirmar(logado, csrf, str(job.id)).json()
    assert corpo["vistas_validaveis"] == 1
    assert corpo["vistas_validadas"] == 1

    linhas = _linhas(db, job)
    assert linhas["c54"].gt_classe == "conforme"
    assert linhas["c55"].gt_classe is None

    detalhe = logado.get(f"{LISTA}/{job.id}").json()
    c55 = next(v for v in detalhe["vistas"] if v["campo"] == "c55")
    assert c55["corrigivel"] is False


# ── idempotência ──────────────────────────────────────────────────────────────


def test_confirmar_duas_vezes_nao_duplica_registro(
    logado: TestClient, csrf: str, db: Session
):
    job = _checklist_nao_conforme(db)

    primeiro = _confirmar(logado, csrf, str(job.id)).json()
    segundo = _confirmar(logado, csrf, str(job.id)).json()

    assert primeiro["vistas_validadas"] == segundo["vistas_validadas"] == 3
    assert segundo["validacao"] == "confirmado"
    # a garantia vem da chave (job_id, campo): não há INSERT a duplicar
    assert db.query(ChecklistViewResult).filter(
        ChecklistViewResult.job_id == job.id
    ).count() == 3


def test_confirmar_duas_vezes_nao_infla_o_eval(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)

    _confirmar(logado, csrf, str(job.id))
    primeiro = logado.get(EVAL).json()
    _confirmar(logado, csrf, str(job.id))
    segundo = logado.get(EVAL).json()

    assert primeiro["vistas_validadas"] == segundo["vistas_validadas"] == 3
    assert primeiro["checklists_validados"] == segundo["checklists_validados"] == 1
    assert primeiro["relatorio"]["n_evaluated"] == segundo["relatorio"]["n_evaluated"] == 3


def test_corrigir_duas_vezes_a_mesma_vista_sobrescreve(
    logado: TestClient, csrf: str, db: Session
):
    job = _checklist_nao_conforme(db)

    _corrigir(logado, csrf, str(job.id), campo="c54", tipo_erro="falso_positivo")
    corpo = _corrigir(
        logado, csrf, str(job.id), campo="c54", tipo_erro="severidade_errada", severidade=4
    ).json()

    assert corpo["vistas_corrigidas"] == 1
    linhas = _linhas(db, job)
    assert linhas["c54"].gt_tipo_erro == "severidade_errada"
    assert linhas["c54"].gt_classe == "dano_visivel"
    assert linhas["c54"].gt_severidade == 4


def test_confirmar_depois_de_corrigir_preserva_a_correcao(
    logado: TestClient, csrf: str, db: Session
):
    """O julgamento específico ganha do genérico — senão um clique apaga trabalho."""
    job = _checklist_nao_conforme(db)

    _corrigir(logado, csrf, str(job.id), campo="c54", tipo_erro="falso_positivo")
    corpo = _confirmar(logado, csrf, str(job.id)).json()

    assert corpo["validacao"] == "corrigido"
    assert corpo["vistas_corrigidas"] == 1
    assert _linhas(db, job)["c54"].gt_classe == "conforme"


# ── correção: os quatro tipos ─────────────────────────────────────────────────


def test_corrigir_falso_positivo(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)

    r = _corrigir(
        logado,
        csrf,
        str(job.id),
        campo="c54",
        tipo_erro="falso_positivo",
        observacao="É sombra de árvore, não amassado.",
    )
    assert r.status_code == 200
    assert r.json()["validacao"] == "corrigido"

    c54 = _linhas(db, job)["c54"]
    assert c54.gt_classe == "conforme"
    assert c54.gt_severidade is None
    assert c54.gt_tipo_erro == "falso_positivo"
    assert c54.gt_observacao == "É sombra de árvore, não amassado."
    assert c54.validado_por == "operador@tecnogera.com"


def test_corrigir_classe_errada(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)

    r = _corrigir(
        logado, csrf, str(job.id), campo="c54", tipo_erro="classe_errada", classe="ausencia_item"
    )
    assert r.status_code == 200

    c54 = _linhas(db, job)["c54"]
    assert c54.gt_classe == "ausencia_item"
    assert c54.gt_tipo_erro == "classe_errada"
    # severidade não contestada continua a da predição
    assert c54.gt_severidade == 2


def test_corrigir_classe_errada_sem_classe_422(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    r = _corrigir(logado, csrf, str(job.id), campo="c54", tipo_erro="classe_errada")
    assert r.status_code == 422
    assert "classe" in r.json()["detail"]


def test_corrigir_classe_errada_captura_falso_negativo(
    logado: TestClient, csrf: str, db: Session
):
    """Vista dada como conforme que na verdade tinha defeito — buraco no recall."""
    job = _checklist_nao_conforme(db)

    _corrigir(
        logado,
        csrf,
        str(job.id),
        campo="c55",
        tipo_erro="classe_errada",
        classe="dano_visivel",
        severidade=1,
    )

    c55 = _linhas(db, job)["c55"]
    assert c55.gt_classe == "dano_visivel"
    assert c55.gt_severidade == 1

    relatorio = logado.get(EVAL).json()["relatorio"]
    # o modelo previu conforme onde havia dano: recall de dano_visivel cai
    assert relatorio["per_class"]["dano_visivel"]["recall"] == pytest.approx(0.5)


def test_corrigir_severidade_errada(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)

    r = _corrigir(
        logado, csrf, str(job.id), campo="c54", tipo_erro="severidade_errada", severidade=1
    )
    assert r.status_code == 200

    c54 = _linhas(db, job)["c54"]
    assert c54.gt_severidade == 1
    # a CLASSE continua certa: severidade errada não é erro de classificação
    assert c54.gt_classe == "dano_visivel"
    assert c54.gt_tipo_erro == "severidade_errada"


def test_corrigir_severidade_errada_sem_severidade_422(
    logado: TestClient, csrf: str, db: Session
):
    job = _checklist_nao_conforme(db)
    r = _corrigir(logado, csrf, str(job.id), campo="c54", tipo_erro="severidade_errada")
    assert r.status_code == 422


def test_corrigir_severidade_de_vista_conforme_422(logado: TestClient, csrf: str, db: Session):
    """Severidade de uma vista sem achado é pedido incoerente, não silêncio."""
    job = _checklist_nao_conforme(db)
    r = _corrigir(
        logado, csrf, str(job.id), campo="c55", tipo_erro="severidade_errada", severidade=2
    )
    assert r.status_code == 422


def test_corrigir_severidade_fora_da_escala_422(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    r = _corrigir(
        logado, csrf, str(job.id), campo="c54", tipo_erro="severidade_errada", severidade=7
    )
    assert r.status_code == 422


def test_corrigir_foto_nao_julgavel(logado: TestClient, csrf: str, db: Session):
    """Não julgável NÃO vira conforme — é o terceiro estado."""
    job = _checklist_nao_conforme(db)

    r = _corrigir(logado, csrf, str(job.id), campo="c56", tipo_erro="nao_julgavel")
    assert r.status_code == 200

    c56 = _linhas(db, job)["c56"]
    assert c56.gt_classe == "nao_processavel"
    assert c56.gt_severidade is None
    assert c56.gt_tipo_erro == "nao_julgavel"


def test_corrigir_tipo_de_erro_invalido_422(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    r = _corrigir(logado, csrf, str(job.id), campo="c54", tipo_erro="mais_ou_menos")
    assert r.status_code == 422


def test_corrigir_vista_inexistente_422(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    r = _corrigir(logado, csrf, str(job.id), campo="c57", tipo_erro="falso_positivo")
    assert r.status_code == 422
    assert "c57" in r.json()["detail"]


def test_corrigir_confirma_as_demais_vistas(logado: TestClient, csrf: str, db: Session):
    """O operador leu o relatório inteiro; o que não contestou, aceitou."""
    job = _checklist_nao_conforme(db)

    corpo = _corrigir(
        logado, csrf, str(job.id), campo="c54", tipo_erro="falso_positivo"
    ).json()
    assert corpo["vistas_validadas"] == 3
    assert corpo["vistas_corrigidas"] == 1

    linhas = _linhas(db, job)
    assert linhas["c55"].gt_classe == "conforme"
    assert linhas["c55"].gt_tipo_erro is None


def test_detalhe_mostra_a_correcao_com_rotulos(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    _corrigir(
        logado,
        csrf,
        str(job.id),
        campo="c54",
        tipo_erro="classe_errada",
        classe="fora_padrao_visual",
        observacao="É encardido de chuva.",
    )

    c54 = next(
        v for v in logado.get(f"{LISTA}/{job.id}").json()["vistas"] if v["campo"] == "c54"
    )
    validacao = c54["validacao"]
    assert validacao["estado"] == "corrigido"
    assert validacao["tipo_erro_rotulo"] == "Classe errada"
    assert validacao["classe_rotulo"] == "Fora do padrão visual"
    assert validacao["observacao"] == "É encardido de chuva."


# ── a lista com dados de verdade ──────────────────────────────────────────────


def test_lista_filtra_por_confirmado_e_corrigido(logado: TestClient, csrf: str, db: Session):
    a = _checklist_nao_conforme(db, "311001")
    b = _checklist_nao_conforme(db, "311002")
    _checklist_nao_conforme(db, "311003")  # fica pendente

    _confirmar(logado, csrf, str(a.id))
    _corrigir(logado, csrf, str(b.id), campo="c54", tipo_erro="falso_positivo")

    confirmados = logado.get(LISTA, params={"validacao": "confirmado"}).json()
    assert confirmados["total"] == 1
    assert confirmados["itens"][0]["checklist_id"] == "311001"
    assert confirmados["itens"][0]["validacao"] == "confirmado"

    corrigidos = logado.get(LISTA, params={"validacao": "corrigido"}).json()
    assert corrigidos["total"] == 1
    assert corrigidos["itens"][0]["checklist_id"] == "311002"

    pendentes = logado.get(LISTA, params={"validacao": "pendente"}).json()
    assert [i["checklist_id"] for i in pendentes["itens"]] == ["311003"]


def test_contador_a_validar_cai_conforme_se_valida(
    logado: TestClient, csrf: str, db: Session
):
    a = _checklist_nao_conforme(db, "311001")
    _checklist_nao_conforme(db, "311002")

    assert logado.get(LISTA).json()["contadores"]["a_validar"] == 2
    _confirmar(logado, csrf, str(a.id))

    contadores = logado.get(LISTA).json()["contadores"]
    assert contadores["a_validar"] == 1
    # o contador de volume não se move: validar não muda o veredito
    assert contadores["nao_conformes"] == 2
    assert contadores["total"] == 2


def test_contador_a_validar_ignora_o_filtro_de_validacao(
    logado: TestClient, csrf: str, db: Session
):
    a = _checklist_nao_conforme(db, "311001")
    _checklist_nao_conforme(db, "311002")
    _confirmar(logado, csrf, str(a.id))

    corpo = logado.get(LISTA, params={"validacao": "confirmado"}).json()
    assert corpo["total"] == 1
    assert corpo["contadores"]["a_validar"] == 1


# ── eval ──────────────────────────────────────────────────────────────────────


def test_eval_exige_sessao(portal_client: TestClient):
    assert portal_client.get(EVAL).status_code == 401


def test_eval_sem_validacao_devolve_422(logado: TestClient, db: Session):
    """Zeros seriam indistinguíveis de um modelo péssimo."""
    _checklist_nao_conforme(db)
    r = logado.get(EVAL)
    assert r.status_code == 422
    assert "Nenhum checklist validado" in r.json()["detail"]


def test_eval_a_rota_nao_e_engolida_pelo_path_param(logado: TestClient, db: Session):
    """`/checklists/eval` tem de vir ANTES de `/checklists/{identificador}`.

    O path param aceita ``codigo_checklist``, não só UUID: fora de ordem, "eval"
    resolveria como um checklist inexistente e o endpoint sumiria com um 404.
    """
    assert logado.get(EVAL).status_code == 422  # 422 = rota certa, sem gabarito
    assert logado.get(f"{LISTA}/eval-que-nao-existe").status_code == 404


def test_eval_com_confirmacao_perfeita(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    _confirmar(logado, csrf, str(job.id))

    corpo = logado.get(EVAL).json()
    relatorio = corpo["relatorio"]
    assert corpo["vistas_validadas"] == 3
    assert corpo["checklists_validados"] == 1
    assert relatorio["accuracy"] == pytest.approx(1.0)
    assert relatorio["macro_f1"] == pytest.approx(1.0)
    assert relatorio["per_class"]["dano_visivel"]["f1"] == pytest.approx(1.0)
    assert relatorio["per_class"]["dano_visivel"]["support"] == 1
    assert relatorio["per_class"]["conforme"]["support"] == 2
    assert corpo["por_tipo_erro"] == {}


def test_eval_reconta_depois_de_uma_correcao(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    _confirmar(logado, csrf, str(job.id))
    assert logado.get(EVAL).json()["relatorio"]["accuracy"] == pytest.approx(1.0)

    _corrigir(logado, csrf, str(job.id), campo="c54", tipo_erro="falso_positivo")

    corpo = logado.get(EVAL).json()
    relatorio = corpo["relatorio"]
    # 3 vistas, 1 errada: o modelo disse dano_visivel onde a verdade é conforme
    assert relatorio["n_evaluated"] == 3
    assert relatorio["accuracy"] == pytest.approx(2 / 3)
    assert relatorio["per_class"]["dano_visivel"]["precision"] == pytest.approx(0.0)
    assert relatorio["per_class"]["conforme"]["recall"] == pytest.approx(2 / 3)
    assert corpo["por_tipo_erro"] == {"falso_positivo": 1}


def test_eval_separa_nao_processavel_de_conforme(logado: TestClient, csrf: str, db: Session):
    """Colapsar os dois premiaria o modelo por um 'tudo bem' que ninguém julgou."""
    job = _checklist_nao_conforme(db)
    _corrigir(logado, csrf, str(job.id), campo="c55", tipo_erro="nao_julgavel")

    relatorio = logado.get(EVAL).json()["relatorio"]
    assert "nao_processavel" in relatorio["per_class"]
    assert relatorio["per_class"]["nao_processavel"]["support"] == 1
    assert relatorio["per_class"]["nao_processavel"]["recall"] == pytest.approx(0.0)
    assert relatorio["accuracy"] == pytest.approx(2 / 3)


def test_eval_traz_acuracia_por_vista(logado: TestClient, csrf: str, db: Session):
    job = _checklist_nao_conforme(db)
    _corrigir(logado, csrf, str(job.id), campo="c54", tipo_erro="falso_positivo")

    per_angle = logado.get(EVAL).json()["relatorio"]["per_angle"]
    assert per_angle["c54"] == pytest.approx(0.0)
    assert per_angle["c55"] == pytest.approx(1.0)


def test_eval_soma_varios_checklists(logado: TestClient, csrf: str, db: Session):
    a = _checklist_nao_conforme(db, "311001")
    b = _checklist_nao_conforme(db, "311002")
    _confirmar(logado, csrf, str(a.id))
    _confirmar(logado, csrf, str(b.id))

    corpo = logado.get(EVAL).json()
    assert corpo["checklists_validados"] == 2
    assert corpo["vistas_validadas"] == 6
    assert corpo["relatorio"]["n_evaluated"] == 6


# ── opções do formulário ──────────────────────────────────────────────────────


def test_detalhe_traz_as_opcoes_do_formulario_de_correcao(logado: TestClient, db: Session):
    """O front não monta enum próprio — o vocabulário vem do backend."""
    job = _checklist_nao_conforme(db)

    opcoes = logado.get(f"{LISTA}/{job.id}").json()["opcoes_validacao"]
    assert [o["valor"] for o in opcoes["tipos_erro"]] == [
        "falso_positivo",
        "classe_errada",
        "severidade_errada",
        "nao_julgavel",
    ]
    assert [o["valor"] for o in opcoes["classes"]] == [
        "ausencia_item",
        "fora_padrao_visual",
        "dano_visivel",
    ]
    assert opcoes["classes"][2]["rotulo"] == "Dano visível"
    assert [o["valor"] for o in opcoes["severidades"]] == ["1", "2", "3", "4"]
    assert opcoes["severidades"][0]["rotulo"] == "Crítica"


def test_detalhe_expoe_rotulo_de_classe_e_tipo_de_defeito(logado: TestClient, db: Session):
    """Os dois únicos campos de laudo que faltavam `*_rotulo` no contrato."""
    job = _checklist_nao_conforme(db)

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    c54 = next(v for v in corpo["vistas"] if v["campo"] == "c54")
    assert c54["classe_rotulo"] == "Dano visível"
    assert c54["tipo_defeito_rotulo"] == "Amassado / deformação"
    assert corpo["achados"][0]["classe_rotulo"] == "Dano visível"
    assert corpo["achados"][0]["tipo_defeito_rotulo"] == "Amassado / deformação"


def test_detalhe_diz_se_o_checklist_e_validavel(logado: TestClient, db: Session):
    validavel = _checklist_nao_conforme(db, "311001")
    sem_laudo = _job(db, "311002", conformidade=None, severidade=None, vista_determinante=None)

    assert logado.get(f"{LISTA}/{validavel.id}").json()["validavel"] is True
    assert logado.get(f"{LISTA}/{sem_laudo.id}").json()["validavel"] is False
