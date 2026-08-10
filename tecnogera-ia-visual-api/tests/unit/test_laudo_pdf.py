"""Testes de ``app/services/laudo_pdf.py`` — ticket ``v1-entregavel/05``.

Puro (sem HTTP, sem banco): alimenta `contexto_laudo()`/`montar_html()` com um
dict que espelha `ChecklistDetailResponse.model_dump(mode="json")` — o mesmo
formato que a rota de detalhe devolve.

**WeasyPrint não carrega no macOS deste checkout** (falta `libgobject-2.0-0`,
mesma causa do `test_pdf_renderer` pré-existente). Por isso este arquivo
testa até `montar_html()` (Jinja puro, sem WeasyPrint) e MOCKA
`renderizar_pdf`/`weasyprint` onde precisar ir além. A renderização real (PDF
de verdade) só foi confirmada rodando dentro do container Docker do repo —
ver relato da tarefa.

Custo de API: **zero**. Nada aqui fala com OpenAI, Anthropic ou Dropbox de
verdade — `baixar_fotos` recebe um dublê.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app.services import laudo_pdf as lp
from app.services.checklist_query import ChecklistDetalhe
from app.services.dropbox import IntegrationError, ResourceNotFoundError


def _vista(
    campo: str,
    rotulo: str,
    *,
    recebida: bool = True,
    indicador: str | None = "conforme",
    classe: str | None = None,
    classe_rotulo: str | None = None,
    tipo_defeito: str | None = None,
    tipo_defeito_rotulo: str | None = None,
    severidade: int | None = None,
    severidade_rotulo: str | None = None,
    motivo_rotulo: str | None = None,
    confianca: float | None = None,
    observacao: str | None = None,
    local: str | None = None,
    foto_path: str | None = None,
    achados: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "campo": campo,
        "rotulo": rotulo,
        "esperada": True,
        "recebida": recebida,
        "status": "analisada" if recebida else None,
        "indicador": indicador if recebida else None,
        "indicador_rotulo": None,
        "motivo_nao_processavel": None,
        "motivo_rotulo": motivo_rotulo,
        "classe": classe,
        "classe_rotulo": classe_rotulo,
        "tipo_defeito": tipo_defeito,
        "tipo_defeito_rotulo": tipo_defeito_rotulo,
        "severidade": severidade,
        "severidade_rotulo": severidade_rotulo,
        "confianca": confianca,
        "observacao": observacao,
        "local": local,
        "conteudo_observado": "Cabine acústica em pátio de galpão.",
        "vista_confere": True,
        "foto_path": (
            foto_path or (f"/Sisloc/MG-CGE/310149 01/{campo} foto 01.jpg" if recebida else None)
        ),
        "foto_url": None,
        "achados": achados or [],
        "erro": None,
        "determinante": campo == "c54",
    }


def _laudo(**overrides: Any) -> dict[str, Any]:
    """Um laudo `nao_conforme` completo — mesma forma da resposta do detalhe."""
    base: dict[str, Any] = {
        "job_id": "94aaf94e-783e-4499-9f83-97935e266456",
        "checklist_id": "310149",
        "status": "done",
        "indicador": "nao_conforme",
        "indicador_rotulo": "Não conforme",
        "severidade": 2,
        "severidade_rotulo": "Alta",
        "confianca": 0.87,
        "vista_determinante": "c54",
        "vista_determinante_rotulo": "Lateral direita",
        "validacao": "pendente",
        "validado_por": None,
        "validado_em": None,
        "equipamento": {
            "codigo_checklist": "310149",
            "patrimonio": "TECG007883",
            "cliente": "EBAZAR.COM.BR. LTDA",
            "contrato": "035514",
            "projeto_bruto": "035514/2026-EBAZAR.COM.BR. LTDA",
            "filial": "MG-CGE",
            "formulario": "F038 - PRÉ LOCAÇÃO DE GERADOR",
            "formulario_codigo": "F038",
            "data_conclusao": "2026-08-02T14:30:00Z",
            "responsavel": "MATHEUS.PARAISO",
            "numero_om": 36729,
            "origem": "OM",
            "status_sisloc": "Concluído",
            "n_linhas": 1,
            "multi_ativo": False,
            "aviso": None,
            "lido_em": "2026-08-02T15:00:00Z",
        },
        "vistas": [
            _vista(
                "c54",
                "Lateral direita",
                indicador="nao_conforme",
                classe="dano_visivel",
                classe_rotulo="Dano visível",
                tipo_defeito="amassado_deformacao",
                tipo_defeito_rotulo="Amassado / deformação",
                severidade=2,
                severidade_rotulo="Alta",
                confianca=0.87,
                observacao="Amassado visível na chapa inferior.",
                local="quadrante inferior direito",
                achados=[
                    {
                        "classe": "dano_visivel",
                        "classe_rotulo": "Dano visível",
                        "tipo_defeito": "amassado_deformacao",
                        "tipo_defeito_rotulo": "Amassado / deformação",
                        "severidade": 2,
                        "local": "quadrante inferior direito",
                        "observacao": "Amassado visível na chapa inferior.",
                        "confianca": 0.87,
                        "campo": "c54",
                        "vista": "Lateral direita",
                    }
                ],
            ),
            _vista("c55", "Lateral esquerda", indicador="conforme"),
            _vista("c56", "Frontal (painel)", indicador="conforme"),
            _vista("c57", "Traseira", indicador="conforme"),
        ],
        "vistas_esperadas": ["c54", "c55", "c56", "c57"],
        "vistas_recebidas": ["c54", "c55", "c56", "c57"],
        "vistas_ausentes": [],
        "nota_vistas": None,
        "achados": [
            {
                "classe": "dano_visivel",
                "classe_rotulo": "Dano visível",
                "tipo_defeito": "amassado_deformacao",
                "tipo_defeito_rotulo": "Amassado / deformação",
                "severidade": 2,
                "local": "quadrante inferior direito",
                "observacao": "Amassado visível na chapa inferior.",
                "confianca": 0.87,
                "campo": "c54",
                "vista": "Lateral direita",
            }
        ],
        "custo_usd": 0.0081,
        "chamadas_llm": 4,
    }
    base.update(overrides)
    return base


def _jpeg_bytes(cor: tuple[int, int, int] = (110, 110, 110)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color=cor).save(buffer, format="JPEG")
    return buffer.getvalue()


_EMITIDO_EM = datetime(2026, 8, 3, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


# ── nome_arquivo — ticket 04, pergunta 7 ────────────────────────────────────


def test_nome_arquivo_segue_o_padrao_cliente():
    nome = lp.nome_arquivo(_laudo())
    assert nome == "Laudo_TECG007883_2026-08-02_ckl310149.pdf"


def test_nome_arquivo_e_ascii_sem_espaco_nem_acento():
    laudo = _laudo()
    laudo["equipamento"] = {
        **laudo["equipamento"],
        "patrimonio": "GERAÇÃO 01 - Ó",
        "codigo_checklist": "310149",
    }
    nome = lp.nome_arquivo(laudo)
    assert nome.isascii()
    assert " " not in nome
    assert "Ç" not in nome and "ç" not in nome


def test_nome_arquivo_sem_data_conclusao_usa_sentinela():
    laudo = _laudo()
    laudo["equipamento"] = {**laudo["equipamento"], "data_conclusao": None}
    nome = lp.nome_arquivo(laudo)
    assert "sem-data" in nome


# ── garantir_pronto — 409 quando não há laudo para exportar ─────────────────


def _detalhe_minimo(*, status: str, indicador: str) -> ChecklistDetalhe:
    """``ChecklistDetalhe`` real, mas só com os dois campos que importam aqui."""
    import uuid as _uuid

    from app.services.checklist_query import (
        EquipamentoDetalhe,
        OpcaoValidacao,
        OpcoesValidacao,
    )

    return ChecklistDetalhe(
        job_id=_uuid.uuid4(),
        checklist_id="310149",
        status=status,
        indicador=indicador,
        indicador_rotulo="x",
        severidade=None,
        severidade_rotulo=None,
        confianca=None,
        vista_determinante=None,
        vista_determinante_rotulo=None,
        validacao="pendente",
        validado_por=None,
        validado_em=None,
        validavel=False,
        opcoes_validacao=OpcoesValidacao(
            tipos_erro=(OpcaoValidacao("x", "x"),),
            classes=(),
            severidades=(),
        ),
        criado_em=datetime(2026, 8, 2, 16, 0),
        iniciado_em=None,
        finalizado_em=None,
        erro=None,
        equipamento=EquipamentoDetalhe(
            codigo_checklist="310149",
            patrimonio="TECG007883",
            cliente=None,
            contrato=None,
            projeto_bruto=None,
            projeto_padrao_reconhecido=False,
            filial=None,
            formulario=None,
            formulario_codigo="F038",
            data_conclusao=None,
            responsavel=None,
            numero_om=None,
            origem=None,
            status_sisloc=None,
            n_linhas=1,
            multi_ativo=False,
            aviso=None,
            lido_em=None,
        ),
        vistas=(),
        vistas_esperadas=(),
        vistas_recebidas=(),
        vistas_ausentes=(),
        nota_vistas=None,
        achados=(),
        custo_usd=0.0,
        chamadas_llm=0,
    )


def test_garantir_pronto_status_nao_done_levanta():
    with pytest.raises(lp.LaudoIndisponivelError):
        lp.garantir_pronto(_detalhe_minimo(status="running", indicador="sem_analise"))


def test_garantir_pronto_sem_analise_levanta_mesmo_com_status_done():
    with pytest.raises(lp.LaudoIndisponivelError):
        lp.garantir_pronto(_detalhe_minimo(status="done", indicador="sem_analise"))


def test_garantir_pronto_done_com_indicador_nao_levanta():
    lp.garantir_pronto(_detalhe_minimo(status="done", indicador="conforme"))


# ── contexto_laudo / montar_html — o que sai e o que NÃO sai do documento ───


def _html(laudo: dict[str, Any], **kwargs: Any) -> str:
    ctx = lp.contexto_laudo(laudo, emitido_em=_EMITIDO_EM, **kwargs)
    return lp.montar_html(ctx)


def test_rodape_e_o_texto_sobrio_exato():
    html = _html(_laudo(), fotos={})
    assert "Laudo produzido por leitura automatizada de imagens · Tecnogera" in html


def test_declaracao_nao_menciona_estado_de_validacao():
    """Consequência do rodapé sóbrio (ticket 04): o parágrafo de validação some."""
    html = _html(_laudo(), fotos={})
    assert "ainda não foi revisado" not in html
    assert "revisado e confirmado" not in html.lower()
    assert "revisado por um técnico" not in html


def test_aviso_multi_ativo_nunca_aparece_mesmo_quando_o_checklist_tem():
    laudo = _laudo()
    laudo["equipamento"] = {
        **laudo["equipamento"],
        "n_linhas": 2,
        "multi_ativo": True,
        "aviso": "Este checklist cobre 2 equipamentos; laudo referente ao primeiro por ordem.",
    }
    html = _html(laudo, fotos={})
    assert "Abrangência deste laudo" not in html
    assert "cobre 2 equipamentos" not in html


def test_nao_processavel_vira_avaliacao_inconclusiva_no_documento():
    laudo = _laudo(indicador="nao_processavel", severidade=None, severidade_rotulo=None)
    html = _html(laudo, fotos={})
    assert "AVALIAÇÃO INCONCLUSIVA" in html
    assert "NÃO PROCESSÁVEL" not in html.upper() or "AVALIAÇÃO INCONCLUSIVA" in html


def test_confianca_nao_aparece_em_lugar_nenhum():
    html = _html(_laudo(), fotos={})
    assert "confianç" not in html.lower()
    assert "87%" not in html


@pytest.mark.parametrize(
    "chave",
    [
        "custo_usd",
        "chamadas_llm",
        "job_id",
        "vista_confere",
        "projeto_bruto",
        "status_sisloc",
        "lido_em",
        "nota_vistas",
        "conteudo_observado",
    ],
)
def test_vocabulario_interno_nao_vaza_para_o_html(chave: str):
    html = _html(_laudo(), fotos={})
    assert chave not in html


def test_classe_e_tipo_defeito_saem_traduzidos_nunca_em_snake_case():
    html = _html(_laudo(), fotos={})
    assert "dano_visivel" not in html
    assert "amassado_deformacao" not in html
    assert "Dano visível" in html
    assert "Amassado / deformação" in html


def test_vista_nao_processavel_mostra_o_motivo_nao_fica_em_branco():
    laudo = _laudo()
    laudo["vistas"][1] = _vista(
        "c55",
        "Lateral esquerda",
        indicador="nao_processavel",
        motivo_rotulo="Foto desfocada",
    )
    html = _html(laudo, fotos={})
    assert "Foto desfocada" in html
    assert "Não foi possível avaliar" in html


def test_foto_indisponivel_vira_moldura_com_aviso_nao_derruba_render():
    html = _html(_laudo(), fotos={}, fotos_indisponiveis={"c54"})
    assert "Foto indisponível" in html


def test_vista_sem_foto_recebida_mostra_sem_registro():
    laudo = _laudo()
    laudo["vistas"][1] = _vista("c55", "Lateral esquerda", recebida=False, indicador=None)
    html = _html(laudo, fotos={})
    assert "Sem registro fotográfico" in html


def test_layout_e_sempre_grade_sem_destaque_de_largura_inteira():
    html = _html(_laudo(), fotos={})
    assert 'class="grade"' in html
    assert 'class="dossie"' not in html


def test_responsavel_sai_title_cased():
    html = _html(_laudo(), fotos={})
    assert "Matheus Paraiso" in html
    assert "MATHEUS.PARAISO" not in html


def test_fuso_america_sao_paulo_na_data_da_inspecao():
    # 2026-08-02T14:30:00Z -> 11h30 em America/Sao_Paulo (UTC-3)
    html = _html(_laudo(), fotos={})
    assert "02/08/2026" in html


# ── baixar_fotos — falha de download não derruba o laudo ────────────────────


class _DropboxFalhaSeletiva:
    """Dublê: `download_image` falha só para paths que CONTÊM um dos campos marcados.

    `falhas={"c54"}` casa com ``/Sisloc/.../c54 foto 01.jpg`` — não precisa o
    caminho inteiro, só o campo da vista que o teste quer derrubar.
    """

    def __init__(self, falhas: frozenset[str] = frozenset(), *, foto: bytes | None = None):
        self._falhas = falhas
        self._foto = foto or _jpeg_bytes()
        self.chamadas: list[str] = []

    def download_image(self, path: str) -> bytes:
        self.chamadas.append(path)
        if any(campo in path for campo in self._falhas):
            raise ResourceNotFoundError("arquivo não encontrado", details={"path": path})
        return self._foto


def test_baixar_fotos_sucesso_gera_data_uri():
    dropbox = _DropboxFalhaSeletiva()
    fotos, indisponiveis = lp.baixar_fotos(dropbox, _laudo()["vistas"])
    assert indisponiveis == set()
    assert set(fotos) == {"c54", "c55", "c56", "c57"}
    assert all(uri.startswith("data:image/jpeg;base64,") for uri in fotos.values())


def test_baixar_fotos_falha_de_uma_nao_derruba_as_outras():
    dropbox = _DropboxFalhaSeletiva(falhas=frozenset({"c54"}))
    fotos, indisponiveis = lp.baixar_fotos(dropbox, _laudo()["vistas"])
    assert indisponiveis == {"c54"}
    assert set(fotos) == {"c55", "c56", "c57"}


def test_baixar_fotos_pula_vista_nao_recebida_sem_tentar_download():
    laudo = _laudo()
    laudo["vistas"][1] = _vista("c55", "Lateral esquerda", recebida=False, indicador=None)
    dropbox = _DropboxFalhaSeletiva()
    fotos, indisponiveis = lp.baixar_fotos(dropbox, laudo["vistas"])
    assert "c55" not in fotos
    assert "c55" not in indisponiveis
    assert "/c55%20foto" not in " ".join(dropbox.chamadas)
    assert not any("c55" in c for c in dropbox.chamadas)


def test_baixar_fotos_imagem_corrompida_tambem_vira_indisponivel():
    class _DropboxBytesCorrompidos:
        def download_image(self, path: str) -> bytes:
            return b"isto-nao-e-uma-imagem"

    fotos, indisponiveis = lp.baixar_fotos(_DropboxBytesCorrompidos(), _laudo()["vistas"])
    assert fotos == {}
    assert indisponiveis == {"c54", "c55", "c56", "c57"}


def test_baixar_fotos_erro_generico_do_dropbox_nao_propaga():
    class _DropboxIntegrationError:
        def download_image(self, path: str) -> bytes:
            raise IntegrationError("Dropbox fora do ar", details={"path": path})

    fotos, indisponiveis = lp.baixar_fotos(_DropboxIntegrationError(), _laudo()["vistas"])
    assert fotos == {}
    assert indisponiveis == {"c54", "c55", "c56", "c57"}


# ── gerar_pdf — orquestração, com renderizar_pdf mockado (WeasyPrint) ───────


def test_gerar_pdf_chama_renderizar_pdf_com_o_html_montado(monkeypatch: pytest.MonkeyPatch):
    capturado: dict[str, str] = {}

    def _fake_renderizar(html: str) -> bytes:
        capturado["html"] = html
        return b"%PDF-1.7 conteudo-fake"

    monkeypatch.setattr(lp, "renderizar_pdf", _fake_renderizar)

    dropbox = _DropboxFalhaSeletiva()
    pdf = lp.gerar_pdf(_laudo(), dropbox=dropbox, emitido_em=_EMITIDO_EM)

    assert pdf == b"%PDF-1.7 conteudo-fake"
    assert "TECG007883" in capturado["html"]
    assert "Laudo produzido por leitura automatizada de imagens · Tecnogera" in capturado["html"]
