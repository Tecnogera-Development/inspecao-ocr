"""Geração do PDF do laudo — ticket ``v1-entregavel/05``.

Transforma o protótipo aprovado no ticket 04 (``docs/laudo-pdf/``) em módulo
de produção. Migra ``contexto_laudo()``, ``filtros()``, ``nome_arquivo()`` e
``preparar_foto()`` do harness (``docs/laudo-pdf/render_prototipo.py``), já
com as decisões do dev **fixas** (não configuráveis — o protótipo tinha
variantes lado a lado para a conversa; aqui só sobra a vencedora):

  - Layout: SEMPRE grade 2×2, sem destaque para a vista com achado.
  - Rodapé: SEMPRE sóbrio (``Laudo produzido por leitura automatizada de
    imagens · Tecnogera``) — não declara estado de validação, e por isso o
    parágrafo "ainda não revisado por técnico" também não aparece.
  - Aviso de multi-ativo (``equipamento.aviso``): NUNCA vai para o documento
    externo — fica só na tela do portal.
  - "Não processável" vira "Avaliação inconclusiva" (``ROTULO_EXTERNO``).

**A mesma verdade do JSON.** A rota chama ``checklist_query.obter_checklist``
e serializa com o MESMO ``ChecklistDetailResponse`` da rota de detalhe antes
de passar para ``gerar_pdf`` — o PDF não pode discordar da tela porque nasce
do mesmo dict.

**Fotos.** Baixadas do Dropbox em memória (``DropboxService.download_image``,
somente leitura — nada volta para lá) e embutidas como data-URI. Falha de
download de UMA foto não derruba o PDF: a moldura sai com o aviso "foto
indisponível" e o documento é gerado do mesmo jeito.

**WeasyPrint não carrega no macOS deste checkout**
(``OSError: cannot load library 'libgobject-2.0-0'`` — mesma causa do
``test_pdf_renderer`` pré-existente). A renderização real só foi confirmada
rodando dentro da imagem Docker do repo; os testes de unidade que rodam no
macOS mockam ``renderizar_pdf``/``weasyprint``.
"""

from __future__ import annotations

import base64
import io
import re
import unicodedata
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.core.logging import get_logger
from app.services import checklist_query as cq
from app.services.dropbox import DropboxService, IntegrationError, ResourceNotFoundError
from app.services.view_inspection import ROTULO_CLASSE, ROTULO_TIPO_DEFEITO

if TYPE_CHECKING:
    from collections.abc import Iterable

_log = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "laudo"
_LOGO_PATH = _TEMPLATES_DIR / "assets" / "tecnogera-logo.png"

#: O Sisloc devolve tudo em UTC (contrato §"Datas são ISO-8601"). Num
#: documento que vai ao cliente, imprimir 14:30 quando o técnico registrou
#: 11:30 é erro de fato, não de estilo — a conversão acontece aqui, uma vez.
FUSO_TECNOGERA = ZoneInfo("America/Sao_Paulo")

#: Vocabulário EXTERNO do veredito. Deliberadamente diferente do
#: `indicador_rotulo` do contrato: "Não processável" é a palavra da esteira, e
#: num laudo endereçado ao cliente ela soa a defeito do equipamento, não a
#: limite da fotografia (ticket 04, pergunta 5).
ROTULO_EXTERNO: dict[str, str] = {
    "conforme": "CONFORME",
    "nao_conforme": "NÃO CONFORME",
    "nao_processavel": "AVALIAÇÃO INCONCLUSIVA",
}

#: Formulário -> como o cliente chama o serviço. O código fica entre
#: parênteses para a Tecnogera continuar se achando no documento.
ROTULO_TIPO_INSPECAO: dict[str, str] = {
    "F038": "Pré-locação de gerador (F038)",
    "F180": "Visita técnica a gerador (F180)",
}

#: Lado maior da foto embutida, em pixels. Ver nota de dimensionamento no
#: protótipo (``docs/laudo-pdf/render_prototipo.py``): 1200 px dá folga para
#: ampliar na tela sem virar mosaico, e reduz uma foto de celular de ~2,5 MB
#: para ~180 KB — sem isso, quatro fotos cruas fazem um anexo que o e-mail
#: corporativo rejeita.
_FOTO_LADO_MAIOR = 1200
_FOTO_QUALIDADE = 80

#: PDF marcado (tagged) — leitor de tela, ordem de leitura, idioma
#: declarado. Custa ~3 KB; decisão do ticket 04.
_PDF_VARIANT = "pdf/ua-1"

#: Exceções de download tratadas como "foto indisponível", não como falha do
#: processo. `OSError` cobre `PIL.UnidentifiedImageError` (imagem corrompida
#: ou formato inesperado) — o Dropbox pode devolver bytes que não abrem.
_ERROS_FOTO_TOLERADOS: tuple[type[Exception], ...] = (
    ResourceNotFoundError,
    IntegrationError,
    OSError,
)


class LaudoIndisponivelError(Exception):
    """`status != done` ou indicador `sem_analise` — não há laudo para exportar.

    Distinto de "checklist não encontrado" (404): o checklist existe, só não
    tem veredito ainda. "Não bloquear laudo pendente de VALIDAÇÃO" (decisão
    da definição de produto) não é o mesmo que "exportar laudo que não existe".
    """


def garantir_pronto(detalhe: cq.ChecklistDetalhe) -> None:
    """Levanta `LaudoIndisponivelError` se não há o que exportar ainda."""
    if detalhe.status != "done" or detalhe.indicador == cq.SEM_ANALISE:
        raise LaudoIndisponivelError(
            "Checklist ainda sem laudo processado — nada para exportar em PDF"
        )


# ─── Filtros Jinja ───────────────────────────────────────────────────────────


def _para_local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    texto = iso.replace("Z", "+00:00")
    momento = datetime.fromisoformat(texto)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(FUSO_TECNOGERA)


def data_br(iso: str | None) -> str:
    momento = _para_local(iso)
    return momento.strftime("%d/%m/%Y") if momento else "—"


def datahora_br(iso: str | None) -> str:
    momento = _para_local(iso)
    return momento.strftime("%d/%m/%Y às %Hh%M") if momento else "—"


def pessoa(bruto: str | None) -> str:
    """``MATHEUS.PARAISO`` -> ``Matheus Paraiso``.

    O campo vem do login de rede do técnico. Cru, ele entrega ao cliente o
    formato de usuário do AD da Tecnogera e lê como grito. Acentos não são
    reconstruídos — inventar "Paraíso" seria pior que imprimir sem acento.
    """
    if not bruto:
        return "—"
    return " ".join(parte.capitalize() for parte in bruto.replace("_", ".").split(".") if parte)


def ou(valor: Any, padrao: Any) -> Any:  # noqa: ANN401 — filtro genérico de template
    return padrao if valor in (None, "", []) else valor


def filtros(env: Environment) -> Environment:
    env.filters["data_br"] = data_br
    env.filters["datahora_br"] = datahora_br
    env.filters["pessoa"] = pessoa
    env.filters["ou"] = ou
    return env


# ─── Nome do arquivo ─────────────────────────────────────────────────────────


def _ascii_seguro(texto: str) -> str:
    """ASCII, sem espaço, sem acento — nome de anexo e valor de header HTTP.

    O patrimônio e o código do checklist vêm do Sisloc: não são confiáveis o
    bastante para ir direto num header ``Content-Disposition`` (injeção de
    CRLF) nem para abrir bem em Windows/WhatsApp/webmail corporativo.
    """
    normalizado = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    limpo = re.sub(r"[^A-Za-z0-9_.-]+", "", normalizado.replace(" ", ""))
    return limpo or "sem-valor"


def nome_arquivo(laudo: dict[str, Any]) -> str:
    """Nome do anexo — o que o operador vê ao anexar no e-mail (ticket 04, pergunta 7).

    ``Laudo_TECG007883_2026-08-02_ckl310149.pdf``: patrimônio primeiro (é o
    que o cliente reconhece), data ISO no meio (ordena na pasta), checklist
    no fim (rastro). ASCII, sem espaço, sem acento.
    """
    eq = laudo["equipamento"]
    patrimonio = _ascii_seguro(eq.get("patrimonio") or "sem-patrimonio")
    checklist = _ascii_seguro(str(eq["codigo_checklist"]))
    momento = _para_local(eq.get("data_conclusao"))
    data = momento.strftime("%Y-%m-%d") if momento else "sem-data"
    return f"Laudo_{patrimonio}_{data}_ckl{checklist}.pdf"


# ─── Contexto ────────────────────────────────────────────────────────────────


def _resumo(laudo: dict[str, Any]) -> dict[str, Any]:
    """Números derivados que o template não deveria calcular sozinho.

    A regra dura aqui é uma só: **vista não avaliada nunca some**. Ela conta
    separadamente das avaliadas, aparece na frase do veredito e ganha seção
    própria. Um documento externo em que a lacuna vira silêncio é um
    documento que afirma conformidade que ninguém verificou.
    """
    vistas = laudo["vistas"]
    total = len(vistas)
    com_foto = sum(1 for v in vistas if v.get("recebida"))
    avaliadas = [v for v in vistas if v.get("indicador") in ("conforme", "nao_conforme")]

    nao_avaliadas: list[dict[str, str]] = []
    for v in vistas:
        if not v.get("recebida"):
            nao_avaliadas.append(
                {
                    "rotulo": v["rotulo"],
                    "explicacao": (
                        "O checklist prevê esta vista, mas nenhuma fotografia foi registrada."
                    ),
                }
            )
        elif v.get("indicador") == "nao_processavel":
            motivo = v.get("motivo_rotulo") or "qualidade insuficiente da imagem"
            nao_avaliadas.append(
                {
                    "rotulo": v["rotulo"],
                    "explicacao": (
                        "A fotografia foi registrada, mas não permitiu avaliação — "
                        f"{motivo.lower()}."
                    ),
                }
            )

    achados = laudo.get("achados") or []
    n = len(achados)
    ind = laudo["indicador"]

    if ind == "nao_conforme":
        plural = "s" if n > 1 else ""
        sev = laudo.get("severidade_rotulo") or "—"
        vista = (laudo.get("vista_determinante_rotulo") or "").lower()
        frase = (
            f"{n} não conformidade{plural} identificada{plural} nas fotografias do equipamento. "
            f"Maior severidade: {sev}, na vista {vista}."
        )
    elif ind == "nao_processavel":
        frase = (
            f"Não foi possível avaliar {len(nao_avaliadas)} de {total} vistas previstas. "
            "Nas vistas avaliadas não foi identificada não conformidade."
        )
    else:
        frase = f"Nenhuma não conformidade identificada nas {len(avaliadas)} vistas avaliadas."

    # Qualificação: a lacuna que o veredito sozinho esconderia.
    qualificacao = ""
    if ind != "nao_processavel" and nao_avaliadas:
        rotulos = ", ".join(v["rotulo"].lower() for v in nao_avaliadas)
        qualificacao = (
            f"{len(nao_avaliadas)} de {total} vistas previstas não foram avaliadas "
            f"({rotulos}). Este parecer não se estende a elas."
        )

    return {
        "total_vistas": total,
        "vistas_com_foto": com_foto,
        "vistas_avaliadas": len(avaliadas),
        "vistas_nao_avaliadas": nao_avaliadas,
        "frase": frase,
        "qualificacao": qualificacao,
        "detalhar_achados": bool(achados),
        "detalhar_nao_avaliadas": bool(nao_avaliadas),
    }


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    dados = _LOGO_PATH.read_bytes()
    return f"data:image/png;base64,{base64.b64encode(dados).decode('ascii')}"


def contexto_laudo(
    laudo: dict[str, Any],
    *,
    fotos: dict[str, str],
    fotos_indisponiveis: Iterable[str] = (),
    emitido_em: datetime | None = None,
) -> dict[str, Any]:
    """Monta o contexto do template a partir do payload cru do portal."""
    eq = laudo["equipamento"]
    agora = emitido_em or datetime.now(FUSO_TECNOGERA)

    return {
        "laudo": laudo,
        "resumo": _resumo(laudo),
        "fotos": fotos,
        "fotos_indisponiveis": set(fotos_indisponiveis),
        "assets": {"logo": _logo_data_uri()},
        "doc": {
            "referencia": f"LV-{eq['codigo_checklist']}",
            "emitido_em": agora.strftime("%d/%m/%Y"),
            "nome_arquivo": nome_arquivo(laudo),
        },
        "rotulos": {
            "externo": ROTULO_EXTERNO,
            "classe": ROTULO_CLASSE,
            "tipo_defeito": ROTULO_TIPO_DEFEITO,
            "severidade": cq.ROTULO_SEVERIDADE,
            "tipo_inspecao": ROTULO_TIPO_INSPECAO.get(
                eq.get("formulario_codigo") or "", eq.get("formulario") or "—"
            ),
        },
    }


# ─── Fotos ───────────────────────────────────────────────────────────────────


def _preparar_foto_bytes(bruto: bytes) -> bytes:
    """Reamostra a foto baixada do Dropbox para embutir no PDF.

    Corrige de passagem a orientação EXIF: foto de celular deitada no
    arquivo e "de pé" na galeria entraria girada no laudo, e o cliente leria
    isso como sistema quebrado.
    """
    from PIL import Image, ImageOps  # noqa: PLC0415 — só quando há foto a preparar

    with Image.open(io.BytesIO(bruto)) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((_FOTO_LADO_MAIOR, _FOTO_LADO_MAIOR), Image.LANCZOS)
        saida = io.BytesIO()
        im.convert("RGB").save(saida, "JPEG", quality=_FOTO_QUALIDADE, optimize=True)
        return saida.getvalue()


def _data_uri_jpeg(dados: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(dados).decode('ascii')}"


def baixar_fotos(
    dropbox: DropboxService, vistas: list[dict[str, Any]]
) -> tuple[dict[str, str], set[str]]:
    """Baixa do Dropbox (somente leitura) cada vista recebida, em memória.

    Nada é escrito no Dropbox e nada é escrito em disco: os bytes originais
    baixados nunca tocam o filesystem, só a versão reamostrada, e só na
    memória do processo — o PDF sai direto do buffer.

    Falha de UMA foto (Dropbox fora do ar, path apagado, imagem corrompida)
    não aborta o laudo inteiro: o campo entra em `fotos_indisponiveis` e o
    template desenha a moldura de aviso. Um laudo com uma foto faltando ainda
    serve; um 500 no clique do operador, não.
    """
    fotos: dict[str, str] = {}
    indisponiveis: set[str] = set()

    for v in vistas:
        campo = v["campo"]
        foto_path = v.get("foto_path")
        if not v.get("recebida") or not foto_path:
            continue
        try:
            bruto = dropbox.download_image(foto_path)
            fotos[campo] = _data_uri_jpeg(_preparar_foto_bytes(bruto))
        except _ERROS_FOTO_TOLERADOS as exc:
            _log.warning(
                "laudo_pdf_foto_indisponivel",
                campo=campo,
                foto_path=foto_path,
                motivo=str(exc),
            )
            indisponiveis.add(campo)

    return fotos, indisponiveis


# ─── Renderização ────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _ambiente() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=False,
    )
    return filtros(env)


def montar_html(contexto: dict[str, Any]) -> str:
    return _ambiente().get_template("laudo.html.j2").render(**contexto)


def renderizar_pdf(html: str) -> bytes:
    # import tardio: só carrega com libpango — não disponível no macOS deste checkout.
    from weasyprint import HTML  # type: ignore[import-untyped]  # noqa: PLC0415

    documento = HTML(string=html, base_url=str(_TEMPLATES_DIR)).render()
    return documento.write_pdf(pdf_variant=_PDF_VARIANT)  # type: ignore[no-any-return]


def gerar_pdf(
    laudo: dict[str, Any],
    *,
    dropbox: DropboxService,
    emitido_em: datetime | None = None,
) -> bytes:
    """Orquestra: baixa fotos, monta o contexto, renderiza o PDF.

    `laudo` é o MESMO dict que `GET /api/v1/portal/checklists/{id}` devolve
    como JSON (`ChecklistDetailResponse.model_dump(mode="json")`) — este
    módulo não faz segunda consulta ao banco.
    """
    fotos, indisponiveis = baixar_fotos(dropbox, laudo["vistas"])
    contexto = contexto_laudo(
        laudo, fotos=fotos, fotos_indisponiveis=indisponiveis, emitido_em=emitido_em
    )
    html = montar_html(contexto)
    return renderizar_pdf(html)


__all__ = [
    "FUSO_TECNOGERA",
    "LaudoIndisponivelError",
    "ROTULO_EXTERNO",
    "ROTULO_TIPO_INSPECAO",
    "baixar_fotos",
    "contexto_laudo",
    "garantir_pronto",
    "gerar_pdf",
    "montar_html",
    "nome_arquivo",
    "renderizar_pdf",
]
