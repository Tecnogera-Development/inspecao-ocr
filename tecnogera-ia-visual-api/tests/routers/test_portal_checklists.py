"""Tela de checklists do portal — ticket ``mvp-c54-c57/09``.

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


def _snapshot(
    checklist_id: str,
    *,
    formulario: str = "F038 - PRÉ LOCAÇÃO DE GERAD",
    filial: str | None = "MG-CGE",
    patrimonio: str | None = "TECG01364",
    projeto: str | None = "035514/2026-EBAZAR.COM.BR. LTDA",
    responsavel: str | None = "MATHEUS.PARAISO",
    data_conclusao: datetime | None = None,
    n_linhas: int = 1,
) -> dict[str, Any]:
    linha = SislocChecklist(
        codigo_checklist=checklist_id,
        formulario=formulario,
        filial=filial,
        patrimonio=patrimonio,
        projeto=projeto,
        responsavel=responsavel,
        data_conclusao=data_conclusao or datetime(2026, 8, 2, 14, 30, tzinfo=UTC),
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
    conformidade: str | None = "conforme",
    severidade: int | None = None,
    vista_determinante: str | None = None,
    vistas: str | None = "c54,c55,c56",
    status: str = "done",
    formulario: str = "F038 - PRÉ LOCAÇÃO DE GERAD",
    filial: str | None = "MG-CGE",
    patrimonio: str | None = "TECG01364",
    projeto: str | None = "035514/2026-EBAZAR.COM.BR. LTDA",
    data_conclusao: datetime | None = None,
    n_linhas: int = 1,
    created_at: datetime | None = None,
    com_snapshot: bool = True,
) -> PipelineJob:
    job = PipelineJob(
        id=uuid.uuid4(),
        checklist_id=checklist_id,
        status=status,
        mode="sync",
        conformidade=conformidade,
        severidade_max=severidade,
        vista_determinante=vista_determinante,
        vistas_recebidas=vistas,
        formulario=formulario,
        patrimonio=patrimonio,
        projeto=projeto,
        n_linhas=n_linhas,
        llm_cost_usd=0.002,
        llm_calls=3,
        created_at=created_at or datetime(2026, 8, 2, 16, 0, tzinfo=UTC),
        sisloc_snapshot=(
            _snapshot(
                checklist_id,
                formulario=formulario,
                filial=filial,
                patrimonio=patrimonio,
                projeto=projeto,
                data_conclusao=data_conclusao,
                n_linhas=n_linhas,
            )
            if com_snapshot
            else None
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
    status: str = "analisada",
    conformidade: str = "conforme",
    motivo: str | None = None,
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
        status=status,
        conformidade=conformidade,
        motivo_nao_processavel=motivo,
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


_ACHADO = {
    "classe": "dano_visivel",
    "tipo_defeito": "amassado_deformacao",
    "severidade": 2,
    "local": "quadrante inferior direito, chapa da lateral",
    "observacao": "Amassado visível na chapa inferior, cerca de 30 cm, com tinta lascada.",
    "confianca": 0.87,
}


def _nao_conforme(db: Session, checklist_id: str, **kwargs: Any) -> PipelineJob:
    job = _job(
        db,
        checklist_id,
        conformidade="nao_conforme",
        severidade=kwargs.pop("severidade", 2),
        vista_determinante="c54",
        **kwargs,
    )
    _vista(
        db,
        job,
        "c54",
        conformidade="nao_conforme",
        classe="dano_visivel",
        tipo_defeito="amassado_deformacao",
        severidade=2,
        confianca=0.87,
        achados=[_ACHADO],
    )
    _vista(db, job, "c55")
    _vista(db, job, "c56")
    return job


# ── autenticação ──────────────────────────────────────────────────────────────


def test_lista_requer_autenticacao(portal_client):
    assert portal_client.get(LISTA).status_code == 401


def test_detalhe_requer_autenticacao(portal_client):
    assert portal_client.get(f"{LISTA}/{uuid.uuid4()}").status_code == 401


def test_lista_vazia(logado):
    r = logado.get(LISTA)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["total"] == 0
    assert corpo["itens"] == []
    assert corpo["contadores"]["nao_conformes"] == 0
    assert corpo["contadores"]["a_validar"] == 0


# ── os TRÊS indicadores ───────────────────────────────────────────────────────


def test_tres_indicadores_sao_distintos(logado, db):
    _nao_conforme(db, "311989")
    _job(db, "311902", conformidade="nao_processavel", vista_determinante="c55")
    _job(db, "311776", conformidade="conforme")

    itens = logado.get(LISTA).json()["itens"]
    por_id = {i["checklist_id"]: i for i in itens}
    assert por_id["311989"]["indicador"] == "nao_conforme"
    assert por_id["311989"]["indicador_rotulo"] == "Não conforme"
    assert por_id["311902"]["indicador"] == "nao_processavel"
    assert por_id["311902"]["indicador_rotulo"] == "Não processável"
    assert por_id["311776"]["indicador"] == "conforme"
    assert por_id["311776"]["indicador_rotulo"] == "Conforme"


def test_nao_processavel_nao_colapsa_em_conforme(logado, db):
    """Terceiro estado é terceiro estado — colapsá-lo subnotifica em silêncio."""
    _job(db, "311902", conformidade="nao_processavel")

    contadores = logado.get(LISTA).json()["contadores"]
    assert contadores["nao_processaveis"] == 1
    assert contadores["conformes"] == 0

    apenas_conformes = logado.get(LISTA, params={"indicador": "conforme"}).json()
    assert apenas_conformes["total"] == 0


def test_job_sem_analise_vira_sem_analise_e_nao_conforme(logado, db):
    _job(db, "311500", conformidade=None, vistas=None, status="pending")

    item = logado.get(LISTA).json()["itens"][0]
    assert item["indicador"] == "sem_analise"
    assert item["indicador_rotulo"] == "Sem análise"
    assert item["severidade"] is None


# ── ordenação padrão ──────────────────────────────────────────────────────────


def test_ordenacao_padrao_severidade_desc(logado, db):
    _job(db, "conf", conformidade="conforme")
    _job(db, "semanalise", conformidade=None, status="pending")
    _job(db, "naoproc", conformidade="nao_processavel")
    _job(db, "nc-media", conformidade="nao_conforme", severidade=3)
    _job(db, "nc-critica", conformidade="nao_conforme", severidade=1)
    _job(db, "nc-alta", conformidade="nao_conforme", severidade=2)

    ordem = [i["checklist_id"] for i in logado.get(LISTA).json()["itens"]]
    assert ordem == ["nc-critica", "nc-alta", "nc-media", "naoproc", "conf", "semanalise"]


def test_ordenacao_recente_ignora_severidade(logado, db):
    _job(
        db, "antigo", conformidade="nao_conforme", severidade=1,
        created_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )
    _job(
        db, "novo", conformidade="conforme",
        created_at=datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
    )

    corpo = logado.get(LISTA, params={"ordenar": "recente"}).json()
    assert [i["checklist_id"] for i in corpo["itens"]] == ["novo", "antigo"]


def test_ordenar_invalido_422(logado, db):
    assert logado.get(LISTA, params={"ordenar": "custo"}).status_code == 422


# ── contadores ────────────────────────────────────────────────────────────────


def test_contadores_no_topo(logado, db):
    _nao_conforme(db, "1")
    _nao_conforme(db, "2")
    _job(db, "3", conformidade="nao_processavel")
    _job(db, "4", conformidade="conforme")
    _job(db, "5", conformidade=None, status="pending")

    contadores = logado.get(LISTA).json()["contadores"]
    assert contadores == {
        "total": 5,
        "nao_conformes": 2,
        "nao_processaveis": 1,
        "conformes": 1,
        "sem_analise": 1,
        "a_validar": 5,
    }


def test_contadores_ignoram_filtro_de_indicador_mas_honram_filial(logado, db):
    _nao_conforme(db, "1", filial="MG-CGE")
    _job(db, "2", conformidade="conforme", filial="SP-GRU")

    corpo = logado.get(LISTA, params={"indicador": "conforme"}).json()
    # o filtro de indicador não pode zerar a âncora de volume de trabalho
    assert corpo["contadores"]["nao_conformes"] == 1
    assert corpo["total"] == 1

    por_filial = logado.get(LISTA, params={"filial": "SP-GRU"}).json()
    assert por_filial["contadores"]["total"] == 1
    assert por_filial["contadores"]["nao_conformes"] == 0


# ── filtros ───────────────────────────────────────────────────────────────────


def test_filtro_indicador_csv(logado, db):
    _nao_conforme(db, "1")
    _job(db, "2", conformidade="nao_processavel")
    _job(db, "3", conformidade="conforme")

    corpo = logado.get(LISTA, params={"indicador": "nao_conforme,nao_processavel"}).json()
    assert {i["checklist_id"] for i in corpo["itens"]} == {"1", "2"}


def test_filtro_indicador_sem_analise(logado, db):
    _job(db, "1", conformidade="conforme")
    _job(db, "2", conformidade=None, status="pending")

    corpo = logado.get(LISTA, params={"indicador": "sem_analise"}).json()
    assert [i["checklist_id"] for i in corpo["itens"]] == ["2"]


def test_filtro_indicador_mistura_veredito_e_ausencia_de_veredito(logado, db):
    _nao_conforme(db, "1")
    _job(db, "2", conformidade="conforme")
    _job(db, "3", conformidade=None, status="pending")

    corpo = logado.get(LISTA, params={"indicador": "nao_conforme,sem_analise"}).json()
    assert {i["checklist_id"] for i in corpo["itens"]} == {"1", "3"}


def test_filtro_indicador_invalido_422(logado, db):
    assert logado.get(LISTA, params={"indicador": "quase_conforme"}).status_code == 422


def test_filtros_combinados(logado, db):
    _nao_conforme(db, "111", filial="MG-CGE", formulario="F038 - PRÉ LOCAÇÃO DE GERAD")
    _nao_conforme(db, "222", filial="SP-GRU", formulario="F038 - PRÉ LOCAÇÃO DE GERAD")
    _nao_conforme(db, "333", filial="MG-CGE", formulario="F038 - LOCAÇÃO DEFINITIVA")
    _job(db, "444", conformidade="conforme", filial="MG-CGE")

    corpo = logado.get(
        LISTA,
        params={"filial": "mg-cge", "formulario": "PRÉ LOCAÇÃO", "indicador": "nao_conforme"},
    ).json()
    assert [i["checklist_id"] for i in corpo["itens"]] == ["111"]


def test_filtro_formulario_por_trecho_de_texto(logado, db):
    """O filtro `formulario` só ESTREITA dentro do conjunto alvo — nunca amplia
    (ver ``test_portal_checklists_f038.py`` para o F180 fora do alvo)."""
    _job(db, "1", formulario="F038 - PRÉ LOCAÇÃO DE GERAD")
    _job(db, "2", formulario="F038 - LOCAÇÃO DEFINITIVA")

    corpo = logado.get(LISTA, params={"formulario": "PRÉ LOCAÇÃO"}).json()
    assert [i["checklist_id"] for i in corpo["itens"]] == ["1"]


def test_filtro_codigo_checklist(logado, db):
    _job(db, "311989")
    _job(db, "311776")

    corpo = logado.get(LISTA, params={"codigo_checklist": "311776"}).json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["checklist_id"] == "311776"


def test_filtro_periodo_usa_data_de_conclusao(logado, db):
    _job(db, "julho", data_conclusao=datetime(2026, 7, 15, 8, 0, tzinfo=UTC))
    _job(db, "agosto", data_conclusao=datetime(2026, 8, 2, 23, 30, tzinfo=UTC))

    corpo = logado.get(LISTA, params={"data_de": "2026-08-01", "data_ate": "2026-08-02"}).json()
    assert [i["checklist_id"] for i in corpo["itens"]] == ["agosto"]

    so_julho = logado.get(LISTA, params={"data_ate": "2026-07-31"}).json()
    assert [i["checklist_id"] for i in so_julho["itens"]] == ["julho"]


def test_paginacao(logado, db):
    for i in range(5):
        _job(db, f"c{i}", conformidade="nao_conforme", severidade=i + 1 if i < 4 else 4)

    pagina1 = logado.get(LISTA, params={"limit": 2, "offset": 0}).json()
    pagina2 = logado.get(LISTA, params={"limit": 2, "offset": 2}).json()
    assert pagina1["total"] == 5
    assert len(pagina1["itens"]) == 2
    assert pagina2["offset"] == 2
    ids1 = {i["checklist_id"] for i in pagina1["itens"]}
    ids2 = {i["checklist_id"] for i in pagina2["itens"]}
    assert not (ids1 & ids2)


def test_facetas_alimentam_os_seletores(logado, db):
    """Com o corte, só existe uma opção de
    formulário — é o sinal que o front usa para esconder o seletor."""
    _job(db, "1", filial="MG-CGE", formulario="F038 - PRÉ LOCAÇÃO DE GERAD")
    _job(db, "2", filial="SP-GRU", formulario="F038 - LOCAÇÃO DEFINITIVA")

    facetas = logado.get(LISTA).json()["facetas"]
    assert facetas["filiais"] == ["MG-CGE", "SP-GRU"]
    assert facetas["formularios"] == ["F038"]


# ── validação (dimensão ortogonal — ticket 10) ────────────────────────────────


def test_validacao_e_ortogonal_ao_indicador(logado, db):
    _nao_conforme(db, "311989")

    item = logado.get(LISTA).json()["itens"][0]
    # não conforme E pendente ao mesmo tempo: não é um quarto valor do indicador
    assert item["indicador"] == "nao_conforme"
    assert item["validacao"] == "pendente"


def test_filtro_validacao_sem_ninguem_validado(logado, db):
    """Antes de qualquer HITL tudo está pendente — ver o ticket 10 para o resto."""
    _job(db, "1", conformidade="conforme")

    assert logado.get(LISTA, params={"validacao": "pendente"}).json()["total"] == 1
    assert logado.get(LISTA, params={"validacao": "confirmado"}).json()["total"] == 0
    assert logado.get(LISTA, params={"validacao": "corrigido"}).json()["total"] == 0


def test_validacao_invalida_422(logado, db):
    assert logado.get(LISTA, params={"validacao": "talvez"}).status_code == 422


# ── detalhe: 3 vs 4 vistas ────────────────────────────────────────────────────
#
# O F180 (3 vistas) saiu da consulta do portal no corte de produto para F038
# — o detalhe de um job F180 devolve 404 (ver test_portal_checklists_f038.py).
# A máquina de 3-vs-4 vistas continua coberta na origem, sem HTTP:
# tests/unit/test_checklist_query.py (`vistas_esperadas`, `_nota_vistas`) e
# tests/unit/test_checklist_filter.py (`VISTAS_ESPERADAS_POR_FORMULARIO["F180"]`).


def test_detalhe_f038_com_quatro_vistas(logado, db):
    job = _job(
        db, "400100",
        formulario="F038 - PRÉ LOCAÇÃO DE GERAD",
        vistas="c54,c55,c56,c57",
        conformidade="conforme",
    )
    for campo in ("c54", "c55", "c56", "c57"):
        _vista(db, job, campo)

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    assert corpo["vistas_esperadas"] == ["c54", "c55", "c56", "c57"]
    assert corpo["vistas_ausentes"] == []
    assert corpo["nota_vistas"] is None
    assert [v["campo"] for v in corpo["vistas"]] == ["c54", "c55", "c56", "c57"]
    assert all(v["recebida"] for v in corpo["vistas"])
    assert [v["rotulo"] for v in corpo["vistas"]] == [
        "Lateral direita", "Lateral esquerda", "Frontal (painel)", "Traseira",
    ]


def test_detalhe_f038_sem_c57_marca_vista_ausente(logado, db):
    """No F038 a traseira É esperada — a moldura aparece vazia, e é lacuna real."""
    job = _job(
        db, "400200",
        formulario="F038 - PRÉ LOCAÇÃO DE GERAD",
        vistas="c54,c55,c56",
        conformidade="conforme",
    )
    for campo in ("c54", "c55", "c56"):
        _vista(db, job, campo)

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    assert corpo["vistas_ausentes"] == ["c57"]
    assert corpo["nota_vistas"] is None
    traseira = next(v for v in corpo["vistas"] if v["campo"] == "c57")
    assert traseira["esperada"] is True
    assert traseira["recebida"] is False
    assert traseira["foto_url"] is None
    assert traseira["indicador"] is None


def test_lista_expoe_vistas_esperadas_e_recebidas(logado, db):
    _job(db, "400200", formulario="F038 - PRÉ LOCAÇÃO DE GERAD", vistas="c54,c55,c56")

    item = logado.get(LISTA).json()["itens"][0]
    assert item["vistas_esperadas"] == ["c54", "c55", "c56", "c57"]
    assert item["vistas_recebidas"] == ["c54", "c55", "c56"]
    assert item["vistas_ausentes"] == ["c57"]


# ── detalhe: rollup, equipamento, achados ─────────────────────────────────────


def test_detalhe_traz_rollup_e_vista_determinante(logado, db):
    job = _nao_conforme(db, "311989")

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    assert corpo["indicador"] == "nao_conforme"
    assert corpo["severidade"] == 2
    assert corpo["severidade_rotulo"] == "Alta"
    assert corpo["vista_determinante"] == "c54"
    assert corpo["vista_determinante_rotulo"] == "Lateral direita"
    assert corpo["confianca"] == pytest.approx(0.87)
    assert corpo["validacao"] == "pendente"

    determinante = next(v for v in corpo["vistas"] if v["determinante"])
    assert determinante["campo"] == "c54"
    assert determinante["tipo_defeito"] == "amassado_deformacao"
    assert determinante["classe"] == "dano_visivel"
    assert "tinta lascada" in determinante["observacao"]


def test_detalhe_bloco_de_equipamento_com_cliente_e_contrato(logado, db):
    job = _nao_conforme(db, "311989")

    equipamento = logado.get(f"{LISTA}/{job.id}").json()["equipamento"]
    assert equipamento["patrimonio"] == "TECG01364"
    assert equipamento["cliente"] == "EBAZAR.COM.BR. LTDA"
    assert equipamento["contrato"] == "035514"
    assert equipamento["projeto_bruto"] == "035514/2026-EBAZAR.COM.BR. LTDA"
    assert equipamento["filial"] == "MG-CGE"
    assert equipamento["formulario_codigo"] == "F038"
    assert equipamento["responsavel"] == "MATHEUS.PARAISO"
    assert equipamento["data_conclusao"].startswith("2026-08-02")
    assert equipamento["numero_om"] == 36729
    assert equipamento["status_sisloc"] == "Concluído"
    assert equipamento["multi_ativo"] is False
    assert equipamento["aviso"] is None


def test_detalhe_n_linhas_maior_que_um_avisa(logado, db):
    job = _job(db, "311989", n_linhas=2)

    equipamento = logado.get(f"{LISTA}/{job.id}").json()["equipamento"]
    assert equipamento["n_linhas"] == 2
    assert equipamento["multi_ativo"] is True
    assert "2 ativos" in equipamento["aviso"]
    assert "TECG01364" in equipamento["aviso"]


def test_lista_marca_multi_ativo(logado, db):
    _job(db, "311989", n_linhas=3)
    item = logado.get(LISTA).json()["itens"][0]
    assert item["n_linhas"] == 3
    assert item["multi_ativo"] is True


def test_detalhe_achados_agregados_e_ordenados(logado, db):
    job = _job(db, "311989", conformidade="nao_conforme", severidade=1, vista_determinante="c55")
    _vista(
        db, job, "c54", conformidade="nao_conforme", severidade=3, confianca=0.6,
        achados=[{**_ACHADO, "severidade": 3, "confianca": 0.6}],
    )
    _vista(
        db, job, "c55", conformidade="nao_conforme", severidade=1, confianca=0.9,
        achados=[{**_ACHADO, "severidade": 1, "confianca": 0.9,
                  "tipo_defeito": "vazamento_oleo"}],
    )
    _vista(db, job, "c56")

    achados = logado.get(f"{LISTA}/{job.id}").json()["achados"]
    assert [a["campo"] for a in achados] == ["c55", "c54"]
    assert achados[0]["tipo_defeito"] == "vazamento_oleo"
    assert achados[0]["vista"] == "Lateral esquerda"


def test_detalhe_vista_nao_processavel_mostra_o_motivo(logado, db):
    job = _job(db, "278154", conformidade="nao_processavel", vista_determinante="c56")
    _vista(db, job, "c54")
    _vista(db, job, "c55")
    _vista(
        db, job, "c56",
        status="nao_processavel",
        conformidade="nao_processavel",
        motivo="foto_estourada",
    )

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    vista = next(v for v in corpo["vistas"] if v["campo"] == "c56")
    assert vista["indicador"] == "nao_processavel"
    assert vista["indicador_rotulo"] == "Não processável"
    assert vista["motivo_nao_processavel"] == "foto_estourada"
    assert vista["motivo_rotulo"] == "Contraluz / superexposição"
    assert vista["severidade"] is None


def test_detalhe_foto_url_aponta_para_o_proxy(logado, db):
    job = _nao_conforme(db, "311989")

    vista = logado.get(f"{LISTA}/{job.id}").json()["vistas"][0]
    assert vista["foto_path"].startswith("/Sisloc/")
    assert vista["foto_url"].startswith("/api/v1/portal/avarias/image?path=%2FSisloc%2F")
    assert " " not in vista["foto_url"]


def test_detalhe_sem_analise_ainda(logado, db):
    """Job criado pela esteira e ainda não despachado — a tela precisa abrir."""
    job = _job(db, "311500", conformidade=None, vistas=None, status="pending")

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    assert corpo["indicador"] == "sem_analise"
    assert corpo["status"] == "pending"
    assert corpo["vistas_recebidas"] == []
    assert corpo["vistas_ausentes"] == ["c54", "c55", "c56", "c57"]
    assert all(v["recebida"] is False for v in corpo["vistas"])
    assert corpo["achados"] == []
    assert corpo["equipamento"]["patrimonio"] == "TECG01364"


def test_detalhe_grid_segue_as_linhas_de_laudo_nao_o_csv_do_rollup(logado, db):
    """Moldura sem laudo é moldura sem foto — declará-la recebida seria mentira."""
    job = _job(db, "311989", vistas="c54,c55,c56", conformidade="conforme")
    _vista(db, job, "c54")

    corpo = logado.get(f"{LISTA}/{job.id}").json()
    assert corpo["vistas_recebidas"] == ["c54"]
    assert corpo["vistas_ausentes"] == ["c55", "c56", "c57"]
    assert [v["recebida"] for v in corpo["vistas"]] == [True, False, False, False]


def test_detalhe_sem_snapshot_cai_nas_colunas_tipadas(logado, db):
    """`POST /pipeline/run` cria job sem linha no ERP — a tela não pode quebrar."""
    job = _job(db, "999999", com_snapshot=False, conformidade="conforme")

    equipamento = logado.get(f"{LISTA}/{job.id}").json()["equipamento"]
    assert equipamento["codigo_checklist"] == "999999"
    assert equipamento["patrimonio"] == "TECG01364"
    assert equipamento["cliente"] == "EBAZAR.COM.BR. LTDA"
    assert equipamento["filial"] is None
    assert equipamento["data_conclusao"] is None


def test_detalhe_por_codigo_checklist_pega_a_execucao_mais_recente(logado, db):
    _job(
        db, "311989", conformidade="nao_conforme", severidade=1,
        created_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )
    novo = _job(
        db, "311989", conformidade="conforme",
        created_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
    )

    corpo = logado.get(f"{LISTA}/311989").json()
    assert corpo["job_id"] == str(novo.id)
    assert corpo["indicador"] == "conforme"


def test_detalhe_nao_encontrado_404(logado, db):
    assert logado.get(f"{LISTA}/{uuid.uuid4()}").status_code == 404
    assert logado.get(f"{LISTA}/000000").status_code == 404
