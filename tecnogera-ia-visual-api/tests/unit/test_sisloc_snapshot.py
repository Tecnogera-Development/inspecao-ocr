"""Parse do `projeto` e snapshot do ERP — ticket mvp-c54-c57/17.

Módulo puro: nenhuma consulta, nenhum banco, **nenhuma chamada de LLM**.

O caso que mais importa aqui é o **negativo**: 0,03% dos valores de `projeto`
não casam com `<contrato>/<ano>-<CLIENTE>`. Um parser que engolisse esses
valores (ou que os aceitasse frouxamente) trocaria "não sei o cliente" por "o
cliente é este", que é pior.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.sisloc import (
    ProjetoParseado,
    SislocChecklist,
    SislocSnapshot,
    parse_projeto,
)

pytestmark = pytest.mark.unit


# ── parse do `projeto` (é o CLIENTE) ──────────────────────────────────────────


def test_projeto_real_vira_contrato_ano_e_cliente() -> None:
    """O valor da linha real do F180 medido em 2026-08-02."""
    p = parse_projeto("035514/2026-EBAZAR.COM.BR. LTDA")
    assert p.contrato == "035514"
    assert p.ano == 2026
    assert p.cliente == "EBAZAR.COM.BR. LTDA"
    assert p.padrao_reconhecido
    assert p.bruto == "035514/2026-EBAZAR.COM.BR. LTDA"


@pytest.mark.parametrize(
    ("cru", "cliente"),
    [
        # Contrato guarda-chuva e estoque próprio: casam com o padrão como
        # qualquer outro — interpretá-los é decisão de tela, não de parser.
        ("999999/9999-PETROBRAS NACIONAL 2020", "PETROBRAS NACIONAL 2020"),
        ("000000/2016-TECNOGERA", "TECNOGERA"),
        # Cliente com hífen no nome: o split é no PRIMEIRO hífen depois do ano.
        ("024026/2024-ABC-XYZ LTDA", "ABC-XYZ LTDA"),
    ],
)
def test_variantes_reais_do_padrao(cru: str, cliente: str) -> None:
    assert parse_projeto(cru).cliente == cliente


@pytest.mark.parametrize(
    "fora_do_padrao",
    [
        "CONTRATO ANTIGO",
        "35514/2026-CLIENTE",  # contrato com 5 dígitos
        "035514/26-CLIENTE",  # ano com 2 dígitos
        "035514-2026-CLIENTE",  # separador errado
        "035514/2026",  # sem cliente nenhum
    ],
)
def test_fora_do_padrao_preserva_o_bruto_e_nao_inventa_cliente(fora_do_padrao: str) -> None:
    """0,03% dos casos. O bruto é a única forma de não perdê-los."""
    p = parse_projeto(fora_do_padrao)
    assert p.bruto == fora_do_padrao
    assert not p.padrao_reconhecido
    assert p.contrato is None
    assert p.cliente is None


@pytest.mark.parametrize("vazio", [None, "", "   "])
def test_projeto_vazio_nao_quebra(vazio: str | None) -> None:
    """`projeto` está vazio em 6,2% do recorte `Concluído` (80,9% no F038)."""
    p = parse_projeto(vazio)
    assert p == ProjetoParseado()
    assert p.bruto is None


def test_espacos_nas_bordas_somem_sem_perder_o_conteudo() -> None:
    p = parse_projeto("  035514/2026-EBAZAR  ")
    assert p.bruto == "035514/2026-EBAZAR"
    assert p.cliente == "EBAZAR"


# ── snapshot ──────────────────────────────────────────────────────────────────


def _linha(**extra: object) -> SislocChecklist:
    base = {
        "codigo_checklist": "311771",
        "formulario": "F180-VISITA GMG_REV04",
        "filial": "SP - SBC",
        "patrimonio": "TERP00601",
        "projeto": "035514/2026-EBAZAR.COM.BR. LTDA",
        "responsavel": "FILIPE.VIEIRA",
        "data_conclusao": datetime(2026, 7, 31, 23, 38, 7, tzinfo=UTC),
        "status": "Concluído",
        "origem": "OM",
        "numero_om": 104556,
        "ordem": 1,
    }
    base.update(extra)
    return SislocChecklist(**base)  # type: ignore[arg-type]


def test_snapshot_congela_a_linha_com_o_projeto_ja_decomposto() -> None:
    snap = _linha().snapshot()
    assert snap.patrimonio == "TERP00601"
    assert snap.projeto.cliente == "EBAZAR.COM.BR. LTDA"
    assert snap.projeto.bruto == "035514/2026-EBAZAR.COM.BR. LTDA"
    assert snap.numero_om == 104556


def test_snapshot_carrega_lido_em() -> None:
    """Sem ele não se distingue 'o dado era esse' de 'lido antes da correção'."""
    quando = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    assert _linha().snapshot(lido_em=quando).lido_em == quando
    assert _linha().snapshot().lido_em is not None


def test_como_json_e_serializavel_para_a_coluna_jsonb() -> None:
    import json

    payload = _linha().snapshot().como_json()
    # Se um datetime escapar, `json.dumps` explode — que é exatamente o erro
    # que aparece só na hora do INSERT em produção.
    assert json.loads(json.dumps(payload))["projeto"]["contrato"] == "035514"
    assert isinstance(payload["data_conclusao"], str)
    assert isinstance(payload["lido_em"], str)


def test_multi_ativo_so_quando_a_view_tem_mais_de_uma_linha() -> None:
    assert not _linha().snapshot().multi_ativo
    assert _linha(n_linhas=4).snapshot().multi_ativo


def test_snapshot_recusa_campo_desconhecido() -> None:
    """JSON sem validação apodrece; `extra='forbid'` é o que impede isso."""
    with pytest.raises(ValidationError):
        SislocSnapshot(
            codigo_checklist="1",
            lido_em=datetime.now(UTC),
            campo_que_nao_existe="x",  # type: ignore[call-arg]
        )


def test_snapshot_e_imutavel() -> None:
    snap = _linha().snapshot()
    with pytest.raises(ValidationError):
        snap.patrimonio = "OUTRO"  # type: ignore[misc]


def test_formulario_vazio_vira_none_no_snapshot() -> None:
    """Coluna vazia é ausência; string vazia na tela seria ruído."""
    assert _linha(formulario="").snapshot().formulario is None
