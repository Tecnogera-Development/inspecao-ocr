"""Filtro qualificado por formulário — tickets mvp-c54-c57/07 e /17.

O caso que mais importa aqui é o **negativo**: um checklist F013 com `c55` e
`c57` NÃO pode passar. Nele esses códigos são plaqueta de dados do alternador e
carregador de bateria; aprová-los mandaria as fotos erradas para a IA rotuladas
como "lateral esquerda" e "traseira".

O ticket 17 acrescentou o recorte `status = 'Concluído'`. Como o status é
obrigatório para aprovar, os testes passam `CONCLUIDO` explicitamente — a
ausência de status é, ela própria, uma reprovação testada abaixo.
"""

from __future__ import annotations

import pytest

from app.services.checklist_filter import (
    CAMPOS_OBRIGATORIOS,
    FORMULARIOS_ALVO,
    STATUS_CONCLUIDO,
    VISTAS_ESPERADAS_POR_FORMULARIO,
    MotivoDescarte,
    avaliar,
    normalizar_campos,
    prefixo_formulario,
    status_concluido,
)

pytestmark = pytest.mark.unit

CONCLUIDO = STATUS_CONCLUIDO


# ── prefixo (a coluna vem truncada em varchar(30)) ────────────────────────────


@pytest.mark.parametrize(
    ("cru", "esperado"),
    [
        ("F180-VISITA GMG_REV04", "F180"),
        ("F038 - PRÉ LOCAÇÃO DE GERADOR", "F038"),
        # Truncado no banco: "F066 - CHECKLIST TRANSPORTE EX" é "...EXPEDIÇÃO".
        ("F066 - CHECKLIST TRANSPORTE EX", "F066"),
        ("  F013 - ALGO", "F013"),
        ("", None),
        ("   ", None),
        (None, None),
        ("(vazio)", None),
        ("CHECKLIST SEM CODIGO", None),
    ],
)
def test_prefixo_formulario(cru: str | None, esperado: str | None) -> None:
    assert prefixo_formulario(cru) == esperado


def test_prefixo_nao_casa_por_igualdade_de_string() -> None:
    """Duas grafias truncadas diferentes do mesmo formulário casam igual."""
    assert prefixo_formulario("F180-VISITA GMG_REV04") == prefixo_formulario(
        "F180-VISITA GMG_REV05 (NOVA"
    )


# ── regra completa ────────────────────────────────────────────────────────────


def test_c57_e_opcional_aprova_sem_ela() -> None:
    """``avaliar`` nunca reprova por falta de c57 — ela é opcional em qualquer
    formulário do conjunto alvo, não só no F180 (dormente, ver
    ``VISTAS_ESPERADAS_POR_FORMULARIO``)."""
    v = avaliar("F038 - PRÉ LOCAÇÃO DE GERADOR", {"c54", "c55", "c56"}, status=CONCLUIDO)
    assert v.aprovado
    assert v.formulario_codigo == "F038"
    assert v.campos_utilizados == ("c54", "c55", "c56")
    assert v.rotulo == "aprovado"


def test_c57_presente_entra_nos_campos_utilizados() -> None:
    v = avaliar(
        "F038 - PRÉ LOCAÇÃO DE GERADOR", {"c54", "c55", "c56", "c57", "c12"}, status=CONCLUIDO
    )
    assert v.aprovado
    assert v.campos_utilizados == ("c54", "c55", "c56", "c57")


def test_f038_tambem_aprova() -> None:
    assert avaliar(
        "F038 - PRÉ LOCAÇÃO DE GERADOR", {"c54", "c55", "c56"}, status=CONCLUIDO
    ).aprovado


def test_f180_saiu_do_alvo_na_v1() -> None:
    """Corte para F038: o F180 sai do produto mesmo com o checklist
    completo e concluído — o corte é por formulário, não por qualidade da
    evidência."""
    v = avaliar("F180-VISITA GMG_REV04", {"c54", "c55", "c56"}, status=CONCLUIDO)
    assert not v.aprovado
    assert v.motivo is MotivoDescarte.FORMULARIO_FORA_WHITELIST
    assert v.rotulo == "formulario_fora_whitelist:F180"
    assert v.terminal


def test_f013_com_c55_e_c57_e_recusado_pelo_formulario() -> None:
    """Erro grave que o filtro existe para impedir: campo solto sem formulário."""
    v = avaliar("F013 - CHECKLIST GERADOR", {"c55", "c57"}, status=CONCLUIDO)
    assert not v.aprovado
    assert v.motivo is MotivoDescarte.FORMULARIO_FORA_WHITELIST
    assert v.rotulo == "formulario_fora_whitelist:F013"
    assert v.terminal


def test_f277_fica_fora_por_decisao_de_escopo() -> None:
    """Plataforma elevatória: outro equipamento, outra taxonomia."""
    v = avaliar("F277-LIBERAÇÃO PLATAFORMA_R00", {"c54", "c55", "c56", "c57"}, status=CONCLUIDO)
    assert not v.aprovado
    assert v.rotulo == "formulario_fora_whitelist:F277"


def test_formulario_vazio_tem_motivo_proprio() -> None:
    """36% do parque. Contado à parte de 'formulário errado'."""
    v = avaliar("", {"c54", "c55", "c56"}, status=CONCLUIDO)
    assert not v.aprovado
    assert v.motivo is MotivoDescarte.FORMULARIO_VAZIO
    assert not v.terminal  # pode ser preenchido depois no ERP


def test_sem_linha_no_erp_e_diferente_de_vazio() -> None:
    v = avaliar(None, {"c54", "c55", "c56"}, tem_linha_no_erp=False)
    assert v.motivo is MotivoDescarte.FORMULARIO_AUSENTE
    assert not v.terminal  # a foto pode chegar antes do fechamento do checklist


@pytest.mark.parametrize("faltante", CAMPOS_OBRIGATORIOS)
def test_campo_obrigatorio_faltando_reprova_e_identifica_qual(faltante: str) -> None:
    campos = {c for c in CAMPOS_OBRIGATORIOS if c != faltante}
    v = avaliar("F038 - PRÉ LOCAÇÃO DE GERADOR", campos, status=CONCLUIDO)
    assert not v.aprovado
    assert v.motivo is MotivoDescarte.CAMPO_FALTANTE
    assert v.campos_faltantes == (faltante,)
    assert v.rotulo == f"campo_faltante:{faltante}"
    assert not v.terminal  # a foto pode chegar no próximo delta


def test_varios_campos_faltantes_aparecem_no_rotulo() -> None:
    v = avaliar("F038", {"c54"}, status=CONCLUIDO)
    assert v.rotulo == "campo_faltante:c55+c56"


def test_a_ordem_e_formulario_antes_de_campo() -> None:
    """Formulário fora da whitelist reprova ANTES de olhar campo nenhum."""
    v = avaliar("F013", set(), status=CONCLUIDO)
    assert v.motivo is MotivoDescarte.FORMULARIO_FORA_WHITELIST
    assert v.campos_faltantes == ()


def test_campos_normalizados_case_insensitive() -> None:
    assert avaliar("F038", {"C54", " c55 ", "C56"}, status=CONCLUIDO).aprovado
    assert normalizar_campos(["C54", "", "  "]) == frozenset({"c54"})


def test_whitelist_configuravel() -> None:
    """``avaliar`` aceita whitelist por parâmetro — usada pelos testes acima, não
    pela esteira em produção (ver ``test_formularios_alvo_e_a_fonte_unica``)."""
    assert avaliar(
        "F277", {"c54", "c55", "c56"}, status=CONCLUIDO, formularios_alvo={"F277"}
    ).aprovado


def test_formularios_alvo_e_a_fonte_unica() -> None:
    """Corte para F038: só F038. Único lugar do repo com essa verdade —
    ``checklist_query``, ``checklist_ingestion`` e ``checklist_backfill`` importam
    esta constante, nenhum guarda literal próprio."""
    assert frozenset({"F038"}) == FORMULARIOS_ALVO


def test_vistas_esperadas_por_formulario_mantem_f180_dormente() -> None:
    """O F180 sai de ``FORMULARIOS_ALVO`` mas a máquina de 3-vs-4 vistas não é
    apagada — decisão explícita da definição de produto (``map.md``): é infraestrutura do
    próximo formulário. Este teste falha se alguém remover a entrada."""
    assert VISTAS_ESPERADAS_POR_FORMULARIO["F180"] == CAMPOS_OBRIGATORIOS


# ── recorte por status (ticket 17) ────────────────────────────────────────────


@pytest.mark.parametrize("aberto", ["A Executar", "A Conferir"])
def test_checklist_aberto_e_descartado_com_motivo_proprio(aberto: str) -> None:
    """14,8% dos F180/F038. Fotos possivelmente parciais, data de conclusão NULL."""
    v = avaliar("F038 - PRÉ LOCAÇÃO DE GERADOR", {"c54", "c55", "c56"}, status=aberto)
    assert not v.aprovado
    assert v.motivo is MotivoDescarte.STATUS_NAO_CONCLUIDO
    assert v.rotulo == f"status_nao_concluido:{aberto}"


def test_status_nao_concluido_nao_e_terminal() -> None:
    """O checklist fecha depois. Terminal aqui perderia 14,8% do volume calado."""
    v = avaliar("F038", {"c54", "c55", "c56"}, status="A Conferir")
    assert not v.terminal


def test_status_e_contado_a_parte_de_campo_faltante() -> None:
    """Checklist aberto E incompleto conta como status, não como campo.

    A ação da Tecnogera é diferente: `campo_faltante` cobra foto do técnico,
    `status_nao_concluido` cobra o fechamento no ERP. Num checklist aberto, a
    foto que falta pode simplesmente ainda não ter sido tirada — contá-la
    inflaria o contador que serve para achar técnico esquecendo de fotografar.
    """
    v = avaliar("F038", {"c54"}, status="A Executar")
    assert v.motivo is MotivoDescarte.STATUS_NAO_CONCLUIDO
    assert v.campos_faltantes == ()


def test_status_ausente_nao_passa() -> None:
    """Não saber o status não é permissão para gastar chave paga."""
    v = avaliar("F038", {"c54", "c55", "c56"}, status=None)
    assert not v.aprovado
    assert v.motivo is MotivoDescarte.STATUS_NAO_CONCLUIDO
    assert v.rotulo == "status_nao_concluido"  # sem qualificador: não há status


@pytest.mark.parametrize("variante", ["Concluído", "concluido", "CONCLUÍDO", " Concluido "])
def test_concluido_e_comparado_sem_acento_e_sem_caixa(variante: str) -> None:
    """O collation do servidor não pode decidir se um checklist é processado."""
    assert status_concluido(variante)
    assert avaliar("F038", {"c54", "c55", "c56"}, status=variante).aprovado


@pytest.mark.parametrize("nao", [None, "", "   ", "A Executar", "A Conferir", "Cancelado"])
def test_status_concluido_recusa_o_resto(nao: str | None) -> None:
    assert not status_concluido(nao)


def test_recorte_desligavel_para_quem_sabe_o_que_faz() -> None:
    """`exigir_concluido=False` existe para diagnóstico, não para a esteira."""
    v = avaliar("F038", {"c54", "c55", "c56"}, status="A Executar", exigir_concluido=False)
    assert v.aprovado


def test_ordem_e_formulario_antes_de_status() -> None:
    """F013 aberto é F013 antes de ser aberto — e F013 é terminal, aberto não."""
    v = avaliar("F013", {"c54", "c55", "c56"}, status="A Executar")
    assert v.motivo is MotivoDescarte.FORMULARIO_FORA_WHITELIST
    assert v.terminal
