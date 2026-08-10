"""Contrato da inspeção por vista — taxonomia v0.2, ticket mvp-c54-c57/08.

O foco é ``parse_inspecao``: function calling forçado garante o *formato* da
saída, nunca a *coerência*. Estes testes são o que impede um laudo
autocontraditório de contaminar o rollup do checklist.
"""

from __future__ import annotations

import pytest

from app.services import view_inspection as vi

pytestmark = pytest.mark.unit


def _raw(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "processavel": True,
        "conteudo_observado": "lateral da cabine, portas fechadas",
        "vista_confere": True,
        "conformidade": "conforme",
        "achados": [],
    }
    base.update(extra)
    return base


def _achado(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "classe": "dano_visivel",
        "tipo_defeito": "corrosao_ferrugem",
        "severidade": 3,
        "local": "quina superior direita",
        "observacao": "mancha laranja com textura na junta de chapa",
        "confianca": 0.82,
    }
    base.update(extra)
    return base


# ── caminho feliz ─────────────────────────────────────────────────────────────


def test_laudo_conforme_atravessa_intacto() -> None:
    laudo = vi.parse_inspecao(_raw(), campo="c54", model_version="gpt-4.1-mini")

    assert laudo.conformidade == "conforme"
    assert laudo.processavel is True
    assert laudo.achados == []
    assert laudo.severidade_max is None
    assert laudo.achado_principal is None
    assert laudo.campo == "c54"


def test_achado_vira_nao_conforme_com_severidade() -> None:
    laudo = vi.parse_inspecao(
        _raw(conformidade="nao_conforme", achados=[_achado()]),
        campo="c55",
        model_version="gpt-4.1-mini",
    )

    assert laudo.conformidade == "nao_conforme"
    assert laudo.severidade_max == 3
    assert laudo.achado_principal is not None
    assert laudo.achado_principal.tipo_defeito == "corrosao_ferrugem"


def test_achado_principal_e_o_mais_critico_desempatado_por_confianca() -> None:
    laudo = vi.parse_inspecao(
        _raw(
            conformidade="nao_conforme",
            achados=[
                _achado(severidade=2, confianca=0.5, tipo_defeito="fixacao_solta"),
                _achado(severidade=2, confianca=0.9, tipo_defeito="pintura_danificada"),
                _achado(severidade=4, confianca=0.99, tipo_defeito="sujeira_grosseira"),
            ],
        ),
        campo="c56",
        model_version="m",
    )

    assert laudo.severidade_max == 2
    assert laudo.achado_principal is not None
    assert laudo.achado_principal.tipo_defeito == "pintura_danificada"


# ── coerência: as três regras de normalização ─────────────────────────────────


def test_nao_processavel_apaga_achados_e_manda_na_conformidade() -> None:
    """Foto ruim não gera achado. Inferir defeito de foto ilegível é proibido."""
    laudo = vi.parse_inspecao(
        _raw(
            processavel=False,
            motivo_nao_processavel="foto_estourada",
            conformidade="nao_conforme",
            achados=[_achado()],
        ),
        campo="c57",
        model_version="m",
    )

    assert laudo.conformidade == "nao_processavel"
    assert laudo.achados == []
    assert laudo.motivo_nao_processavel == "foto_estourada"


def test_nao_processavel_sem_motivo_valido_recebe_fallback() -> None:
    """Motivo fora do enum sumiria da tela — vira `obstrucao`, não `None`."""
    laudo = vi.parse_inspecao(
        _raw(processavel=False, motivo_nao_processavel="ceu_muito_azul"),
        campo="c54",
        model_version="m",
    )

    assert laudo.motivo_nao_processavel == "obstrucao"


def test_conformidade_nao_processavel_implica_processavel_false() -> None:
    """O modelo pode marcar a conformidade e esquecer o booleano."""
    laudo = vi.parse_inspecao(
        _raw(conformidade="nao_processavel", motivo_nao_processavel="foto_desfocada"),
        campo="c54",
        model_version="m",
    )

    assert laudo.processavel is False
    assert laudo.motivo_nao_processavel == "foto_desfocada"


def test_nao_conforme_sem_achado_cai_para_conforme() -> None:
    laudo = vi.parse_inspecao(
        _raw(conformidade="nao_conforme", achados=[]), campo="c54", model_version="m"
    )

    assert laudo.conformidade == "conforme"


def test_conforme_com_achado_sobe_para_nao_conforme() -> None:
    laudo = vi.parse_inspecao(
        _raw(conformidade="conforme", achados=[_achado()]), campo="c54", model_version="m"
    )

    assert laudo.conformidade == "nao_conforme"


def test_conformidade_desconhecida_e_decidida_pelos_achados() -> None:
    laudo = vi.parse_inspecao(
        _raw(conformidade="talvez", achados=[_achado()]), campo="c54", model_version="m"
    )

    assert laudo.conformidade == "nao_conforme"


def test_processavel_apaga_motivo_residual() -> None:
    laudo = vi.parse_inspecao(
        _raw(processavel=True, motivo_nao_processavel="foto_escura"),
        campo="c54",
        model_version="m",
    )

    assert laudo.motivo_nao_processavel is None


# ── enum fechado ──────────────────────────────────────────────────────────────


def test_tipo_defeito_fora_do_enum_e_descartado() -> None:
    """Tipo inventado não entra: a taxonomia é vocabulário fechado."""
    laudo = vi.parse_inspecao(
        _raw(
            conformidade="nao_conforme",
            achados=[_achado(tipo_defeito="gerador_feio"), _achado()],
        ),
        campo="c54",
        model_version="m",
    )

    assert len(laudo.achados) == 1
    assert laudo.achados[0].tipo_defeito == "corrosao_ferrugem"


def test_severidade_e_confianca_sao_clampadas() -> None:
    laudo = vi.parse_inspecao(
        _raw(conformidade="nao_conforme", achados=[_achado(severidade=9, confianca=4.2)]),
        campo="c54",
        model_version="m",
    )

    assert laudo.achados[0].severidade == 4
    assert laudo.achados[0].confianca == 1.0


def test_vista_confere_falso_e_preservado() -> None:
    """Métrica de alarme do dicionário de campos (taxonomia §8), não veredito."""
    laudo = vi.parse_inspecao(
        _raw(vista_confere=False, conteudo_observado="plaqueta de dados em close"),
        campo="c55",
        model_version="m",
    )

    assert laudo.vista_confere is False
    assert laudo.conformidade == "conforme"


# ── atalho da validação técnica ───────────────────────────────────────────────


def test_inspecao_nao_processavel_nao_precisa_de_modelo() -> None:
    laudo = vi.inspecao_nao_processavel("c56", "foto_desfocada", "quadro preto")

    assert laudo.conformidade == "nao_processavel"
    assert laudo.achados == []
    assert laudo.model_version == "validacao_tecnica"
    assert laudo.motivo_nao_processavel == "foto_desfocada"


def test_inspecao_nao_processavel_normaliza_motivo_invalido() -> None:
    laudo = vi.inspecao_nao_processavel("c56", "motivo_inventado", "x")

    assert laudo.motivo_nao_processavel == "obstrucao"


# ── prompt e schema ───────────────────────────────────────────────────────────


def test_prompt_carrega_as_tres_correcoes_do_ticket_15() -> None:
    """c56 é painel (não radiador), painel aberto não é defeito, c57 é opcional."""
    prompt = vi.SYSTEM_PROMPT_V02

    assert "PAINEL DE COMANDO" in prompt
    assert "**Painel de comando aberto em c56 NÃO é defeito**" in prompt
    assert "parou de emitir o campo c57 em setembro/2025" in prompt
    assert "infira nada sobre vistas que não chegaram" in prompt


def test_prompt_nao_menciona_as_quatro_vistas() -> None:
    """Três vistas é o caso NORMAL — citar 'as 4 vistas' puxaria o veredito."""
    assert "4 vistas" not in vi.SYSTEM_PROMPT_V02
    assert "quatro vistas" not in vi.SYSTEM_PROMPT_V02


def test_schema_da_tool_exige_os_campos_novos() -> None:
    params = vi.tool_parameters()

    assert set(params["required"]) == {
        "processavel",
        "conteudo_observado",
        "vista_confere",
        "conformidade",
        "achados",
    }
    achado = params["properties"]["achados"]["items"]
    assert "local" in achado["required"]
    assert achado["properties"]["tipo_defeito"]["enum"] == list(vi.TIPOS_DEFEITO)


def test_mensagem_usuario_declara_a_vista_esperada() -> None:
    assert "painel de comando" in vi.mensagem_usuario("c56")
    assert "traseira" in vi.mensagem_usuario("c57")
    # Campo desconhecido não explode — degrada para o rótulo genérico.
    assert "vista de conjunto" in vi.mensagem_usuario("c99")


# ── rótulos de classe e tipo_defeito — ticket v1-entregavel/02 ─────────────────


def test_todo_tipo_defeito_da_taxonomia_tem_rotulo() -> None:
    """Trava a dívida: tipo novo no enum sem rótulo correspondente quebra o CI,
    não silenciosamente na tela do operador."""
    faltando = set(vi.TIPOS_DEFEITO) - set(vi.ROTULO_TIPO_DEFEITO)
    assert not faltando, f"tipo_defeito sem rótulo: {sorted(faltando)}"


def test_toda_classe_tem_rotulo() -> None:
    faltando = set(vi.CLASSES) - set(vi.ROTULO_CLASSE)
    assert not faltando, f"classe sem rótulo: {sorted(faltando)}"


def test_rotulo_tipo_defeito_conhecido_vem_do_mapa() -> None:
    assert vi.rotulo_tipo_defeito("amassado_deformacao") == "Amassado / deformação"


def test_rotulo_classe_conhecida_vem_do_mapa() -> None:
    assert vi.rotulo_classe("dano_visivel") == "Dano visível"


def test_rotulo_tipo_defeito_desconhecido_cai_no_fallback() -> None:
    """Taxonomia evolui antes do rótulo: nunca lança exceção, nunca fica em
    branco nem em snake_case cru na tela."""
    assert vi.rotulo_tipo_defeito("vidro_trincado") == "Vidro trincado"


def test_rotulo_classe_desconhecida_cai_no_fallback() -> None:
    assert vi.rotulo_classe("defeito_estrutural") == "Defeito estrutural"


def test_rotulo_none_e_vazio_nao_quebram() -> None:
    assert vi.rotulo_classe(None) is None
    assert vi.rotulo_classe("") is None
    assert vi.rotulo_tipo_defeito(None) is None
    assert vi.rotulo_tipo_defeito("") is None
