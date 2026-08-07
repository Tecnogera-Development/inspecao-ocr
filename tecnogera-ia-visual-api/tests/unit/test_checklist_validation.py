"""Regras puras do HITL — ticket ``mvp-c54-c57/10``.

Custo de API: **zero**. O caminho ponta a ponta (endpoints, CSRF, eval, lista)
está em ``tests/routers/test_portal_checklist_hitl.py``; aqui ficam as regras que
só se enxergam de perto: a projeção predição → classe do eval, o rollup derivado
das vistas, e o saneamento do valor lido do banco.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.checklist_analysis import (
    STATUS_ANALISADA,
    STATUS_FALHOU,
    STATUS_NAO_DESPACHADA,
    STATUS_NAO_PROCESSAVEL,
    ChecklistViewResult,
)
from app.models.pipeline import PipelineJob
from app.services import checklist_validation as cv
from app.services.checklist_query import VALIDACAO_PADRAO, validacao_de

pytestmark = pytest.mark.unit


def _linha(
    campo: str = "c54",
    *,
    status: str = STATUS_ANALISADA,
    conformidade: str | None = "conforme",
    classe: str | None = None,
    severidade: int | None = None,
    gt_classe: str | None = None,
    gt_tipo_erro: str | None = None,
) -> ChecklistViewResult:
    return ChecklistViewResult(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        checklist_id="311989",
        campo=campo,
        status=status,
        conformidade=conformidade,
        classe=classe,
        severidade_max=severidade,
        gt_classe=gt_classe,
        gt_tipo_erro=gt_tipo_erro,
    )


# ── projeção predição → classe do eval ────────────────────────────────────────


def test_conforme_vira_pseudo_classe_conforme():
    assert cv.classe_predita(_linha(conformidade="conforme")) == cv.CLASSE_CONFORME


def test_nao_processavel_nao_colapsa_em_conforme():
    """São desfechos diferentes; colapsá-los premiaria o modelo por não julgar."""
    linha = _linha(status=STATUS_NAO_PROCESSAVEL, conformidade="nao_processavel")
    assert cv.classe_predita(linha) == cv.CLASSE_NAO_PROCESSAVEL
    assert cv.CLASSE_NAO_PROCESSAVEL != cv.CLASSE_CONFORME


def test_nao_conforme_usa_a_classe_do_achado_principal():
    linha = _linha(conformidade="nao_conforme", classe="dano_visivel", severidade=2)
    assert cv.classe_predita(linha) == "dano_visivel"


@pytest.mark.parametrize(
    "linha",
    [
        _linha(status=STATUS_FALHOU, conformidade=None),
        _linha(status=STATUS_NAO_DESPACHADA, conformidade=None),
    ],
    ids=["falhou", "nao_despachada"],
)
def test_vista_sem_veredito_nao_tem_classe_predita(linha: ChecklistViewResult):
    """Erro de infraestrutura não é erro de classificação — fora da métrica."""
    assert cv.classe_predita(linha) is None


def test_nao_conforme_sem_classe_e_descartado():
    """Sem classe não há o que comparar; inventar uma contaminaria o F1."""
    assert cv.classe_predita(_linha(conformidade="nao_conforme", classe=None)) is None


def test_classe_fora_da_taxonomia_e_descartada():
    linha = _linha(conformidade="nao_conforme", classe="classe_que_nao_existe")
    assert cv.classe_predita(linha) is None


# ── estado de uma vista ───────────────────────────────────────────────────────


def test_vista_sem_gabarito_e_pendente():
    assert cv.validacao_da_vista(_linha()) == cv.VISTA_PENDENTE


def test_vista_com_gabarito_e_sem_tipo_de_erro_e_confirmada():
    assert cv.validacao_da_vista(_linha(gt_classe="conforme")) == cv.VISTA_CONFIRMADA


def test_vista_com_tipo_de_erro_e_corrigida():
    linha = _linha(gt_classe="conforme", gt_tipo_erro=cv.TIPO_FALSO_POSITIVO)
    assert cv.validacao_da_vista(linha) == cv.VISTA_CORRIGIDA


# ── rollup derivado das vistas ────────────────────────────────────────────────


def _job() -> PipelineJob:
    return PipelineJob(id=uuid.uuid4(), checklist_id="311989", status="done")


def test_rollup_sem_vista_alguma_e_pendente():
    job = _job()
    assert cv.recalcular_validacao(job, []) == cv.VISTA_PENDENTE
    assert job.validado_por is None


def test_rollup_com_todas_confirmadas():
    linhas = [
        _linha("c54", gt_classe="conforme"),
        _linha("c55", gt_classe="conforme"),
    ]
    assert cv.recalcular_validacao(_job(), linhas) == cv.VISTA_CONFIRMADA


def test_uma_correcao_domina_o_rollup():
    """"Corrigido" é a informação que o operador procura na fila."""
    linhas = [
        _linha("c54", gt_classe="conforme", gt_tipo_erro=cv.TIPO_FALSO_POSITIVO),
        _linha("c55", gt_classe="conforme"),
    ]
    assert cv.recalcular_validacao(_job(), linhas) == cv.VISTA_CORRIGIDA


def test_validacao_parcial_volta_para_a_fila():
    """Meio checklist julgado ainda é trabalho a fazer."""
    linhas = [_linha("c54", gt_classe="conforme"), _linha("c55")]
    assert cv.recalcular_validacao(_job(), linhas) == cv.VISTA_PENDENTE


def test_vista_sem_veredito_nao_impede_o_confirmado():
    """A vista que falhou não conta no denominador — senão nada fecharia."""
    linhas = [
        _linha("c54", gt_classe="conforme"),
        _linha("c55", status=STATUS_FALHOU, conformidade=None),
    ]
    assert cv.recalcular_validacao(_job(), linhas) == cv.VISTA_CONFIRMADA


def test_rollup_pendente_limpa_quem_validou():
    job = _job()
    job.validado_por = "alguem@tecnogera.com"
    cv.recalcular_validacao(job, [])
    assert job.validado_por is None
    assert job.validado_em is None


# ── leitura do estado pela camada de consulta ─────────────────────────────────


def test_validacao_de_le_a_coluna():
    job = _job()
    job.validacao = "confirmado"
    assert validacao_de(job) == "confirmado"


@pytest.mark.parametrize("valor", [None, "", "  ", "meio_confirmado"])
def test_valor_desconhecido_volta_para_a_fila(valor: str | None):
    """Estado que ninguém reconhece precisa reaparecer como trabalho a fazer."""
    job = _job()
    job.validacao = valor
    assert validacao_de(job) == VALIDACAO_PADRAO


# ── vocabulário ───────────────────────────────────────────────────────────────


def test_os_quatro_tipos_de_erro_do_ticket_estao_todos_rotulados():
    assert set(cv.TIPOS_ERRO) == {
        "falso_positivo",
        "classe_errada",
        "severidade_errada",
        "nao_julgavel",
    }
    assert all(cv.ROTULO_TIPO_ERRO[tipo] for tipo in cv.TIPOS_ERRO)


def test_classes_do_gabarito_incluem_as_duas_pseudo_classes():
    assert set(cv.CLASSES_GABARITO) == {
        "conforme",
        "nao_processavel",
        "ausencia_item",
        "fora_padrao_visual",
        "dano_visivel",
    }
    assert all(cv.ROTULO_CLASSE_GABARITO[c] for c in cv.CLASSES_GABARITO)
