"""Regras puras da tela de checklists — ticket ``mvp-c54-c57/09``.

Sem banco, sem rede, sem LLM: só o vocabulário e as regras que a tela usa.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.checklist_filter import (
    CAMPOS_OBRIGATORIOS,
    VISTAS_ESPERADAS_POR_FORMULARIO,
    vistas_esperadas,
)
from app.services.checklist_query import (
    INDICADORES,
    ROTULO_INDICADOR,
    SEM_ANALISE,
    _data,
    _nota_vistas,
    rotulo_severidade,
    url_da_foto,
)

# ── vistas esperadas por formulário ───────────────────────────────────────────


def test_f180_espera_tres_vistas():
    assert vistas_esperadas("F180-VISITA GMG_REV04") == ("c54", "c55", "c56")


def test_f038_espera_quatro_vistas():
    assert vistas_esperadas("F038 - PRÉ LOCAÇÃO DE GERAD") == ("c54", "c55", "c56", "c57")


def test_aceita_codigo_ja_extraido():
    assert vistas_esperadas("F038") == VISTAS_ESPERADAS_POR_FORMULARIO["F038"]


def test_formulario_desconhecido_cai_no_piso_da_esteira():
    assert vistas_esperadas("F277-LIBERAÇÃO PLATAFORMA") == CAMPOS_OBRIGATORIOS
    assert vistas_esperadas(None) == CAMPOS_OBRIGATORIOS
    assert vistas_esperadas("") == CAMPOS_OBRIGATORIOS


def test_vista_recebida_manda_sobre_o_mapa():
    """F180 anterior a set/2025 emitia c57 — a foto existe e precisa de moldura."""
    assert vistas_esperadas("F180", ("c54", "c55", "c56", "c57")) == (
        "c54", "c55", "c56", "c57",
    )


def test_recebidas_sao_normalizadas():
    assert "c57" in vistas_esperadas("F180", (" C57 ",))


def test_f038_sem_c57_continua_esperando_a_traseira():
    """Aqui a ausência é lacuna real, não característica do formulário."""
    esperadas = vistas_esperadas("F038", ("c54", "c55", "c56"))
    assert "c57" in esperadas


# ── nota das vistas ───────────────────────────────────────────────────────────


def test_nota_explica_o_corte_do_f180():
    nota = _nota_vistas("F180", ("c54", "c55", "c56"))
    assert nota is not None
    assert "setembro/2025" in nota


def test_nota_generica_para_formulario_desconhecido():
    nota = _nota_vistas("F277", ("c54", "c55", "c56"))
    assert nota == "Este formulário não inclui a foto traseira (c57)."


def test_sem_nota_quando_as_quatro_sao_esperadas():
    assert _nota_vistas("F038", ("c54", "c55", "c56", "c57")) is None


# ── vocabulário ───────────────────────────────────────────────────────────────


def test_indicador_tem_tres_valores_mais_a_ausencia_de_veredito():
    assert INDICADORES == ("nao_conforme", "nao_processavel", "conforme")
    assert SEM_ANALISE not in INDICADORES
    assert set(ROTULO_INDICADOR) == {*INDICADORES, SEM_ANALISE}


@pytest.mark.parametrize(
    ("nivel", "esperado"),
    [(1, "Crítica"), (2, "Alta"), (3, "Média"), (4, "Baixa"), (None, None), (9, None)],
)
def test_rotulo_de_severidade(nivel, esperado):
    assert rotulo_severidade(nivel) == esperado


# ── proxy de imagem ───────────────────────────────────────────────────────────


def test_url_da_foto_escapa_o_caminho():
    url = url_da_foto("/Sisloc/MG-CGE/311989 01/c54 foto.jpg")
    assert url == (
        "/api/v1/portal/avarias/image?path="
        "%2FSisloc%2FMG-CGE%2F311989%2001%2Fc54%20foto.jpg"
    )


def test_url_da_foto_sem_caminho():
    assert url_da_foto(None) is None
    assert url_da_foto("") is None


# ── datas do snapshot ─────────────────────────────────────────────────────────


def test_data_aceita_iso_com_z():
    assert _data("2026-08-02T14:30:00Z") == datetime(2026, 8, 2, 14, 30, tzinfo=UTC)


def test_data_aceita_datetime_pronto():
    agora = datetime(2026, 8, 2, tzinfo=UTC)
    assert _data(agora) is agora


def test_data_ilegivel_nao_derruba_a_tela():
    """Snapshot corrompido esconde a data, não o laudo inteiro."""
    assert _data("ontem") is None
    assert _data(None) is None
    assert _data(12345) is None
    assert _data("  ") is None
