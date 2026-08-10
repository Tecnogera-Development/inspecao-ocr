"""Consultas da tela de checklists do portal — ticket ``mvp-c54-c57/09``.

Camada de leitura pura: **nada é recalculado aqui**. O rollup foi decidido no
ticket 08 e vive em colunas de ``pipeline_jobs``; o enriquecimento foi decidido
no ticket 17 e vive em ``sisloc_snapshot``. Recalcular o veredito no render
permitiria a tela mostrar algo diferente do que foi persistido — e o
``ground_truth`` do HITL passaria a apontar para um julgamento que não é o que o
operador viu.

Três coisas do domínio estão codificadas aqui e quebram implementação ingênua:

**O indicador tem TRÊS valores, não dois.** ``conforme`` / ``nao_conforme`` /
``nao_processavel``. Colapsar o terceiro em "conforme" faria o sistema
subnotificar em silêncio: uma foto pode ser nítida (passa na validação técnica)
e ainda assim não julgável por contraluz. Há um quarto estado de *processo* —
``sem_analise``, o job que ainda não rodou ou falhou — que **não** é valor do
indicador: ele diz que não há veredito, não que o veredito é neutro.

**Validação é dimensão ORTOGONAL.** ``pendente``/``confirmado``/``corrigido``
não é um quarto indicador: um checklist pode ser não conforme e já validado, ou
conforme e nunca olhado. O julgamento humano é persistido pelo ticket 10 —
gabarito por vista em ``checklist_view_results.gt_*``, rollup em
``pipeline_jobs.validacao`` (ver ``checklist_validation``). Esta camada só lê.

**3 ou 4 vistas conforme o formulário.** ``vistas_esperadas`` (regra em
``checklist_filter``) separa "este formulário não tem traseira" de "faltou
foto". As duas coisas desenham telas diferentes.

**A consulta obedece a ``FORMULARIOS_ALVO``.**
Porta trancada: um job cujo formulário não está no conjunto alvo não aparece na
lista — nem por ``?formulario=`` — e o detalhe devolve "não encontrado". O
parâmetro ``formulario`` da lista continua existindo, mas só estreita dentro do
conjunto alvo; nunca o amplia. A constante é importada de ``checklist_filter``:
este módulo não guarda o valor.

A ordenação padrão — pior indicador, depois severidade mais crítica — não é
cosmética: se a validação humana não acontecer, o F1 do contrato fica sem fonte
de dados. O default da tela é o **trabalho a fazer**, não o histórico.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from sqlalchemy import case, func, or_

from app.models.checklist_analysis import ChecklistViewResult
from app.models.pipeline import PipelineJob
from app.models.sisloc import parse_projeto
from app.services.checklist_filter import FORMULARIOS_ALVO, prefixo_formulario, vistas_esperadas
from app.services.view_inspection import (
    CLASSES,
    ROTULO_CLASSE,
    ROTULO_MOTIVO_NAO_PROCESSAVEL,
    ROTULO_VISTA,
    rotulo_classe,
    rotulo_tipo_defeito,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Query, Session

# ── vocabulário ───────────────────────────────────────────────────────────────

#: Os três valores do indicador, do pior para o melhor. A ordem É a regra de
#: rollup do ticket 08 (``_ORDEM_CONFORMIDADE``) e a ordenação da lista.
INDICADORES: tuple[str, ...] = ("nao_conforme", "nao_processavel", "conforme")

#: Não é indicador: é ausência de indicador. Filtrável, porque "o que ainda não
#: rodou" é uma pergunta operacional legítima.
SEM_ANALISE = "sem_analise"

ROTULO_INDICADOR: dict[str, str] = {
    "nao_conforme": "Não conforme",
    "nao_processavel": "Não processável",
    "conforme": "Conforme",
    SEM_ANALISE: "Sem análise",
}

#: Dimensão ortogonal ao indicador. Persistida pelo ticket 10.
VALIDACOES: tuple[str, ...] = ("pendente", "confirmado", "corrigido")

#: Estado de quem nunca foi julgado — e de qualquer valor fora do enum. Um
#: rollup desconhecido tem de voltar para a fila de trabalho, não sumir dela.
VALIDACAO_PADRAO = "pendente"

#: Os dois estados que significam "já passou por humano". Usado no filtro e no
#: contador para que "a validar" seja o complemento exato do que a lista mostra.
VALIDACOES_FECHADAS: tuple[str, ...] = ("confirmado", "corrigido")

#: 1 é o PIOR (``docs/relatorio/severidade.md``). Ordenar "severidade desc" na
#: tela é ordenar este inteiro **ascendente** no banco.
ROTULO_SEVERIDADE: dict[int, str] = {
    1: "Crítica",
    2: "Alta",
    3: "Média",
    4: "Baixa",
}

ORDENACOES: tuple[str, ...] = ("severidade", "recente")

#: Rota do proxy de imagem já existente — libera ``/Sisloc/`` sem mudança.
_ROTA_IMAGEM = "/api/v1/portal/avarias/image"


def rotulo_severidade(severidade: int | None) -> str | None:
    return ROTULO_SEVERIDADE.get(severidade) if severidade is not None else None


def url_da_foto(dropbox_path: str | None) -> str | None:
    """URL do proxy autenticado para a foto da vista. ``None`` sem caminho."""
    if not dropbox_path:
        return None
    return f"{_ROTA_IMAGEM}?path={quote(dropbox_path, safe='')}"


# ── filtros ───────────────────────────────────────────────────────────────────


@dataclass
class ChecklistFiltros:
    """Filtros da lista. Todos opcionais; combináveis entre si."""

    limit: int = 50
    offset: int = 0
    #: Valores de ``INDICADORES`` e/ou ``SEM_ANALISE``. Vazio = todos.
    indicador: tuple[str, ...] = ()
    validacao: str | None = None
    filial: str | None = None
    #: Código ``F0NN`` ou trecho do texto do formulário.
    formulario: str | None = None
    #: ``codigo_checklist`` do Sisloc — casamento exato.
    codigo_checklist: str | None = None
    #: Período sobre a **data de conclusão** do checklist no Sisloc.
    data_de: date | None = None
    data_ate: date | None = None
    ordenar: str = "severidade"


# ── DTOs ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChecklistLinha:
    """Uma linha da lista (nível 1)."""

    job_id: uuid.UUID
    checklist_id: str
    status: str
    indicador: str
    indicador_rotulo: str
    severidade: int | None
    severidade_rotulo: str | None
    vista_determinante: str | None
    vista_determinante_rotulo: str | None
    validacao: str
    patrimonio: str | None
    cliente: str | None
    filial: str | None
    formulario: str | None
    formulario_codigo: str | None
    data: datetime | None
    criado_em: datetime
    n_linhas: int | None
    multi_ativo: bool
    vistas_recebidas: tuple[str, ...]
    vistas_esperadas: tuple[str, ...]
    vistas_ausentes: tuple[str, ...]


@dataclass(frozen=True)
class ChecklistContadores:
    """Contadores do topo da lista.

    Honram os filtros de escopo (filial, formulário, período, id) e **ignoram**
    ``indicador`` e ``validacao`` de propósito: o contador é a âncora do volume
    de trabalho, e um "0 não conformes" que só apareceu porque o operador
    filtrou por "Conforme" seria ruído, não informação.
    """

    total: int
    nao_conformes: int
    nao_processaveis: int
    conformes: int
    sem_analise: int
    a_validar: int


@dataclass(frozen=True)
class ChecklistFacetas:
    """Opções dos seletores da tela, extraídas do que existe no banco."""

    filiais: tuple[str, ...]
    formularios: tuple[str, ...]


@dataclass(frozen=True)
class ChecklistPagina:
    itens: tuple[ChecklistLinha, ...]
    total: int
    limit: int
    offset: int
    contadores: ChecklistContadores
    facetas: ChecklistFacetas


@dataclass(frozen=True)
class ValidacaoVista:
    """O julgamento humano de UMA vista — ticket 10.

    ``None`` no lugar deste bloco significa vista pendente. Preenchido com
    ``tipo_erro=None`` significa confirmada; com ``tipo_erro`` preenchido, o
    operador disse **o que** estava errado.
    """

    estado: str
    tipo_erro: str | None
    tipo_erro_rotulo: str | None
    classe: str | None
    classe_rotulo: str | None
    severidade: int | None
    severidade_rotulo: str | None
    observacao: str | None
    por: str | None
    em: datetime | None


@dataclass(frozen=True)
class VistaDetalhe:
    """Uma moldura do grid do relatório — recebida ou não."""

    campo: str
    rotulo: str
    esperada: bool
    recebida: bool
    #: ``analisada`` | ``nao_processavel`` | ``falhou`` | ``nao_despachada``.
    status: str | None
    indicador: str | None
    indicador_rotulo: str | None
    motivo_nao_processavel: str | None
    motivo_rotulo: str | None
    classe: str | None
    classe_rotulo: str | None
    tipo_defeito: str | None
    tipo_defeito_rotulo: str | None
    severidade: int | None
    severidade_rotulo: str | None
    confianca: float | None
    observacao: str | None
    local: str | None
    conteudo_observado: str | None
    vista_confere: bool | None
    foto_path: str | None
    foto_url: str | None
    achados: tuple[dict[str, Any], ...]
    erro: str | None
    determinante: bool
    #: ``True`` quando esta vista tem predição comparável — é o que o botão
    #: "Corrigir" pode alcançar. Vista que falhou no download não é corrigível:
    #: não houve julgamento a contestar.
    corrigivel: bool
    validacao: ValidacaoVista | None


@dataclass(frozen=True)
class EquipamentoDetalhe:
    """Bloco de equipamento — tudo congelado no snapshot do ERP."""

    codigo_checklist: str
    patrimonio: str | None
    cliente: str | None
    contrato: str | None
    projeto_bruto: str | None
    projeto_padrao_reconhecido: bool
    filial: str | None
    formulario: str | None
    formulario_codigo: str | None
    data_conclusao: datetime | None
    responsavel: str | None
    numero_om: int | None
    origem: str | None
    status_sisloc: str | None
    n_linhas: int | None
    multi_ativo: bool
    aviso: str | None
    lido_em: datetime | None


@dataclass(frozen=True)
class OpcaoValidacao:
    """Uma opção do formulário de correção — valor + rótulo já em português."""

    valor: str
    rotulo: str


@dataclass(frozen=True)
class OpcoesValidacao:
    """As listas do formulário de correção do ticket 09.

    Viajam no detalhe em vez de virarem constante no front pelo mesmo motivo de
    todo ``*_rotulo``: o vocabulário do domínio muda com a taxonomia, e se ele
    viver em dois repositórios diverge na primeira mudança. São ~11 itens.
    """

    tipos_erro: tuple[OpcaoValidacao, ...]
    classes: tuple[OpcaoValidacao, ...]
    severidades: tuple[OpcaoValidacao, ...]


@dataclass(frozen=True)
class ChecklistDetalhe:
    """O relatório (nível 2)."""

    job_id: uuid.UUID
    checklist_id: str
    status: str
    indicador: str
    indicador_rotulo: str
    severidade: int | None
    severidade_rotulo: str | None
    confianca: float | None
    vista_determinante: str | None
    vista_determinante_rotulo: str | None
    validacao: str
    validado_por: str | None
    validado_em: datetime | None
    #: ``False`` quando nenhuma vista produziu veredito comparável — não há o
    #: que confirmar, e a tela precisa saber disso antes de oferecer o botão.
    validavel: bool
    opcoes_validacao: OpcoesValidacao
    criado_em: datetime
    iniciado_em: datetime | None
    finalizado_em: datetime | None
    erro: str | None
    equipamento: EquipamentoDetalhe
    vistas: tuple[VistaDetalhe, ...]
    vistas_esperadas: tuple[str, ...]
    vistas_recebidas: tuple[str, ...]
    vistas_ausentes: tuple[str, ...]
    nota_vistas: str | None
    achados: tuple[dict[str, Any], ...]
    custo_usd: float
    chamadas_llm: int


# ── helpers de leitura do snapshot ────────────────────────────────────────────


def _snapshot(job: PipelineJob) -> dict[str, Any]:
    bruto = job.sisloc_snapshot
    return bruto if isinstance(bruto, dict) else {}


def _texto(valor: Any) -> str | None:  # noqa: ANN401 — JSON solto
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _data(valor: Any) -> datetime | None:  # noqa: ANN401 — JSON solto
    """ISO-8601 do snapshot → ``datetime``. Valor ilegível vira ``None``.

    Snapshot corrompido não pode derrubar a tela: o resto do laudo continua
    exibível sem a data.
    """
    if isinstance(valor, datetime):
        return valor
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _projeto(job: PipelineJob) -> tuple[str | None, str | None, str | None, bool]:
    """``(cliente, contrato, bruto, padrao_reconhecido)``.

    Prefere o snapshot (já parseado no ticket 17); cai no ``projeto`` tipado
    quando o job nasceu sem linha no ERP (``POST /pipeline/run``).
    """
    dados = _snapshot(job).get("projeto")
    if isinstance(dados, dict):
        return (
            _texto(dados.get("cliente")),
            _texto(dados.get("contrato")),
            _texto(dados.get("bruto")) or _texto(job.projeto),
            bool(dados.get("padrao_reconhecido")),
        )
    parseado = parse_projeto(job.projeto)
    return (
        parseado.cliente,
        parseado.contrato,
        parseado.bruto,
        parseado.padrao_reconhecido,
    )


def _indicador(job: PipelineJob) -> str:
    """Indicador exibível. Sem rollup persistido, é ``sem_analise``."""
    conformidade = (job.conformidade or "").strip()
    return conformidade if conformidade in INDICADORES else SEM_ANALISE


def validacao_de(job: PipelineJob) -> str:
    """Estado da validação humana, do rollup persistido pelo ticket 10.

    O rollup é derivado das vistas (``checklist_validation.recalcular_validacao``);
    aqui ele só é lido e saneado. Valor fora do enum vira ``pendente`` de
    propósito: um estado que ninguém reconhece precisa voltar para a fila de
    trabalho, não desaparecer dela em silêncio.
    """
    valor = (job.validacao or "").strip()
    return valor if valor in VALIDACOES else VALIDACAO_PADRAO


def _vistas_do_job(job: PipelineJob) -> tuple[str, ...]:
    """Vistas efetivamente recebidas, do CSV persistido pelo rollup."""
    bruto = job.vistas_recebidas or ""
    return tuple(c.strip().lower() for c in bruto.split(",") if c.strip())


def _formulario_codigo(job: PipelineJob) -> str | None:
    return prefixo_formulario(job.formulario or _texto(_snapshot(job).get("formulario")))


# ── expressões SQL reutilizadas ───────────────────────────────────────────────


def _campo_snapshot(*chaves: str) -> Any:  # noqa: ANN401 — expressão SQLAlchemy
    """``sisloc_snapshot -> 'a' -> 'b' ->> ...`` portátil (Postgres e SQLite)."""
    expr: Any = PipelineJob.sisloc_snapshot
    for chave in chaves:
        expr = expr[chave]
    return expr.as_string()


def _ordem_indicador() -> Any:  # noqa: ANN401 — expressão SQLAlchemy
    return case(
        *[
            (PipelineJob.conformidade == valor, posicao)
            for posicao, valor in enumerate(INDICADORES)
        ],
        else_=len(INDICADORES),
    )


def _filtro_formularios_alvo() -> Any:  # noqa: ANN401 — expressão SQLAlchemy
    """``PipelineJob.formulario`` começa com um dos prefixos de ``FORMULARIOS_ALVO``.

    Porta trancada do corte de produto: aplicada sempre, antes
    de qualquer filtro do operador. Um job sem formulário reconhecido (``NULL``
    ou fora do conjunto) não casa com nenhum prefixo — a mesma cautela do
    filtro de ingestão, que também não assume gerador sem saber o formulário.
    """
    return or_(*(PipelineJob.formulario.like(f"{codigo}%") for codigo in sorted(FORMULARIOS_ALVO)))


def _aplicar_escopo(q: Query[PipelineJob], filtros: ChecklistFiltros) -> Query[PipelineJob]:
    """Filtros de escopo — os que os contadores também honram."""
    q = q.filter(_filtro_formularios_alvo())

    if filtros.codigo_checklist:
        q = q.filter(PipelineJob.checklist_id == filtros.codigo_checklist.strip())

    if filtros.filial:
        q = q.filter(func.upper(_campo_snapshot("filial")) == filtros.filial.strip().upper())

    if filtros.formulario:
        alvo = filtros.formulario.strip()
        codigo = prefixo_formulario(alvo)
        if codigo:
            # `formulario` é varchar(30) TRUNCADO na origem — casar por prefixo
            # `F0NN` é a única forma correta (ver `checklist_filter`).
            q = q.filter(PipelineJob.formulario.like(f"{codigo}%"))
        else:
            q = q.filter(PipelineJob.formulario.ilike(f"%{alvo}%"))

    # Período sobre a data de CONCLUSÃO do checklist (o que o operador entende
    # por "data"), não sobre a data de processamento. O snapshot guarda
    # ISO-8601, cuja ordem lexicográfica é a ordem cronológica no recorte de
    # dia — que é a granularidade do filtro.
    if filtros.data_de is not None:
        q = q.filter(_campo_snapshot("data_conclusao") >= filtros.data_de.isoformat())
    if filtros.data_ate is not None:
        limite = (filtros.data_ate + timedelta(days=1)).isoformat()
        q = q.filter(_campo_snapshot("data_conclusao") < limite)

    return q


def _aplicar_indicador(q: Query[PipelineJob], indicador: tuple[str, ...]) -> Query[PipelineJob]:
    if not indicador:
        return q
    concretos = [v for v in indicador if v in INDICADORES]
    condicoes: list[Any] = []
    if concretos:
        condicoes.append(PipelineJob.conformidade.in_(concretos))
    if SEM_ANALISE in indicador:
        # `sem_analise` inclui o rollup NULL e qualquer valor fora do enum —
        # um estado desconhecido não pode sumir da lista em silêncio.
        condicoes.append(
            or_(
                PipelineJob.conformidade.is_(None),
                PipelineJob.conformidade.notin_(INDICADORES),
            )
        )
    if not condicoes:
        return q
    return q.filter(or_(*condicoes))


def _pendente() -> Any:  # noqa: ANN401 — expressão SQLAlchemy
    """Predicado de "a validar": nunca julgado **ou** com rollup irreconhecível.

    Espelha exatamente o saneamento de ``validacao_de``. Se os dois divergirem,
    a lista mostra um checklist que o contador não conta — e o operador perde a
    fila como medida de trabalho.
    """
    return or_(
        PipelineJob.validacao.is_(None),
        PipelineJob.validacao.notin_(VALIDACOES_FECHADAS),
    )


def _aplicar_validacao(q: Query[PipelineJob], validacao: str | None) -> Query[PipelineJob]:
    if not validacao:
        return q
    if validacao == VALIDACAO_PADRAO:
        return q.filter(_pendente())
    return q.filter(PipelineJob.validacao == validacao)


# ── consultas ─────────────────────────────────────────────────────────────────


def listar_checklists(db: Session, filtros: ChecklistFiltros) -> ChecklistPagina:
    """Lista paginada, com contadores e facetas.

    Ordenação padrão (``ordenar='severidade'``): pior indicador primeiro e,
    dentro dele, a severidade mais crítica; empate resolvido pelo mais recente.
    É o **trabalho a fazer** no topo. ``ordenar='recente'`` devolve o histórico
    puro, por data de processamento.
    """
    escopo = _aplicar_escopo(db.query(PipelineJob), filtros)
    contadores = _contar(escopo)

    q = _aplicar_validacao(_aplicar_indicador(escopo, filtros.indicador), filtros.validacao)

    total = q.count()

    if filtros.ordenar == "recente":
        ordem: list[Any] = [PipelineJob.created_at.desc()]
    else:
        ordem = [
            _ordem_indicador().asc(),
            func.coalesce(PipelineJob.severidade_max, 9).asc(),
            PipelineJob.created_at.desc(),
        ]

    jobs = (
        q.order_by(*ordem, PipelineJob.id.asc())
        .offset(filtros.offset)
        .limit(filtros.limit)
        .all()
    )

    return ChecklistPagina(
        itens=tuple(_para_linha(job) for job in jobs),
        total=total,
        limit=filtros.limit,
        offset=filtros.offset,
        contadores=contadores,
        facetas=_facetas(db),
    )


def _contar(escopo: Query[PipelineJob]) -> ChecklistContadores:
    por_indicador: dict[str, int] = {}
    for conformidade, quantos in escopo.with_entities(
        PipelineJob.conformidade, func.count(PipelineJob.id)
    ).group_by(PipelineJob.conformidade):
        chave = (conformidade or "").strip()
        chave = chave if chave in INDICADORES else SEM_ANALISE
        por_indicador[chave] = por_indicador.get(chave, 0) + int(quantos)

    total = sum(por_indicador.values())
    return ChecklistContadores(
        total=total,
        nao_conformes=por_indicador.get("nao_conforme", 0),
        nao_processaveis=por_indicador.get("nao_processavel", 0),
        conformes=por_indicador.get("conforme", 0),
        sem_analise=por_indicador.get(SEM_ANALISE, 0),
        # Fila de trabalho de verdade: o que ainda não passou por humano. Como
        # os contadores ignoram `indicador` e `validacao` de propósito, este
        # número não muda quando o operador filtra a tela — ele mede o volume,
        # não a seleção.
        a_validar=escopo.filter(_pendente()).count(),
    )


def _facetas(db: Session) -> ChecklistFacetas:
    """Opções dos seletores. Volume medido: ~371 checklists/mês — barato.

    Honra o mesmo escopo de ``FORMULARIOS_ALVO`` da lista: um formulário fora
    do conjunto alvo não pode oferecer opção no seletor que o front usa para
    decidir se mostra o seletor (ticket 03 do portal lê ``len(formularios)``).
    """
    escopo = db.query(PipelineJob).filter(_filtro_formularios_alvo())
    filiais = {
        texto
        for (valor,) in escopo.with_entities(_campo_snapshot("filial")).distinct()
        if (texto := _texto(valor))
    }
    formularios = {
        codigo
        for (valor,) in escopo.with_entities(PipelineJob.formulario).distinct()
        if (codigo := prefixo_formulario(valor))
    }
    return ChecklistFacetas(
        filiais=tuple(sorted(filiais)),
        formularios=tuple(sorted(formularios)),
    )


def _para_linha(job: PipelineJob) -> ChecklistLinha:
    cliente, _contrato, _bruto, _ok = _projeto(job)
    snapshot = _snapshot(job)
    recebidas = _vistas_do_job(job)
    codigo = _formulario_codigo(job)
    esperadas = vistas_esperadas(codigo, recebidas)
    indicador = _indicador(job)
    n_linhas = job.n_linhas
    return ChecklistLinha(
        job_id=job.id,
        checklist_id=job.checklist_id,
        status=job.status,
        indicador=indicador,
        indicador_rotulo=ROTULO_INDICADOR[indicador],
        severidade=job.severidade_max,
        severidade_rotulo=rotulo_severidade(job.severidade_max),
        vista_determinante=job.vista_determinante,
        vista_determinante_rotulo=ROTULO_VISTA.get(job.vista_determinante or ""),
        validacao=validacao_de(job),
        patrimonio=job.patrimonio or _texto(snapshot.get("patrimonio")),
        cliente=cliente,
        filial=_texto(snapshot.get("filial")),
        formulario=job.formulario or _texto(snapshot.get("formulario")),
        formulario_codigo=codigo,
        data=_data(snapshot.get("data_conclusao")),
        criado_em=job.created_at,
        n_linhas=n_linhas,
        multi_ativo=bool(n_linhas and n_linhas > 1),
        vistas_recebidas=recebidas,
        vistas_esperadas=esperadas,
        vistas_ausentes=tuple(c for c in esperadas if c not in recebidas),
    )


def obter_checklist(db: Session, identificador: str) -> ChecklistDetalhe | None:
    """Relatório de um checklist, por ``job_id`` (UUID) ou ``codigo_checklist``.

    Aceitar os dois não é conveniência: o operador conhece o número do checklist
    do Sisloc, e o front nem sempre carrega o ``job_id``. Por ``codigo_checklist``
    devolve a execução **mais recente** — reprocessar por backfill cria job novo
    e o laudo válido é o último.
    """
    job = resolver_job(db, identificador)
    if job is None:
        return None

    # Porta trancada do corte de produto: mesmo achando o job
    # por id, um formulário fora de FORMULARIOS_ALVO não vira laudo exibível.
    # Job sem formulário reconhecido (`None`) cai aqui também — mesma cautela
    # da lista, que também não assume gerador sem saber o formulário.
    if _formulario_codigo(job) not in FORMULARIOS_ALVO:
        return None

    linhas = (
        db.query(ChecklistViewResult)
        .filter(ChecklistViewResult.job_id == job.id)
        .all()
    )
    por_campo = {linha.campo.strip().lower(): linha for linha in linhas}

    # A verdade do grid é a LINHA de laudo, não o CSV do rollup: é dela que
    # saem a foto e o achado. Usar o CSV aqui deixaria a tela declarar uma vista
    # recebida sem ter o que desenhar dentro da moldura.
    recebidas = tuple(sorted(por_campo, key=_ordem_canonica))
    codigo = _formulario_codigo(job)
    esperadas = vistas_esperadas(codigo, recebidas)
    campos = tuple(sorted(set(esperadas) | set(recebidas), key=_ordem_canonica))
    ausentes = tuple(c for c in esperadas if c not in recebidas)

    vistas = tuple(
        _para_vista(campo, por_campo.get(campo), campo in esperadas, job.vista_determinante)
        for campo in campos
    )

    achados: list[dict[str, Any]] = []
    for vista in vistas:
        for achado in vista.achados:
            # `achado` já sai de `_para_vista` com `classe_rotulo` e
            # `tipo_defeito_rotulo` — só falta `campo`/`vista`, que dependem
            # de que vista o achado veio (a raiz achata as vistas).
            achados.append({**achado, "campo": vista.campo, "vista": vista.rotulo})
    achados.sort(key=lambda a: (int(a.get("severidade") or 9), -float(a.get("confianca") or 0.0)))

    determinante = por_campo.get((job.vista_determinante or "").strip().lower())
    indicador = _indicador(job)

    return ChecklistDetalhe(
        job_id=job.id,
        checklist_id=job.checklist_id,
        status=job.status,
        indicador=indicador,
        indicador_rotulo=ROTULO_INDICADOR[indicador],
        severidade=job.severidade_max,
        severidade_rotulo=rotulo_severidade(job.severidade_max),
        confianca=determinante.confianca if determinante else None,
        vista_determinante=job.vista_determinante,
        vista_determinante_rotulo=ROTULO_VISTA.get(job.vista_determinante or ""),
        validacao=validacao_de(job),
        validado_por=job.validado_por,
        validado_em=job.validado_em,
        validavel=any(v.corrigivel for v in vistas),
        opcoes_validacao=opcoes_validacao(),
        criado_em=job.created_at,
        iniciado_em=job.started_at,
        finalizado_em=job.finished_at,
        erro=job.error,
        equipamento=_para_equipamento(job, codigo),
        vistas=vistas,
        vistas_esperadas=esperadas,
        vistas_recebidas=recebidas,
        vistas_ausentes=ausentes,
        nota_vistas=_nota_vistas(codigo, esperadas),
        achados=tuple(achados),
        custo_usd=job.llm_cost_usd,
        chamadas_llm=job.llm_calls,
    )


def resolver_job(db: Session, identificador: str) -> PipelineJob | None:
    """``job_id`` (UUID) ou ``codigo_checklist`` → o job que a tela mostra.

    Público porque o HITL (ticket 10) resolve pelo **mesmo** identificador: se
    confirmar resolvesse diferente da tela, o operador confirmaria um laudo que
    nunca viu.
    """
    alvo = identificador.strip()
    try:
        return db.get(PipelineJob, uuid.UUID(alvo))
    except ValueError:
        return (
            db.query(PipelineJob)
            .filter(PipelineJob.checklist_id == alvo)
            .order_by(PipelineJob.created_at.desc(), PipelineJob.id.desc())
            .first()
        )


#: Ordem canônica das molduras no grid: c54, c55, c56, c57.
_ORDEM_CAMPOS: tuple[str, ...] = tuple(ROTULO_VISTA)


def _ordem_canonica(campo: str) -> tuple[int, str]:
    posicao = _ORDEM_CAMPOS.index(campo) if campo in _ORDEM_CAMPOS else len(_ORDEM_CAMPOS)
    return (posicao, campo)


def _nota_vistas(codigo: str | None, esperadas: tuple[str, ...]) -> str | None:
    """Explica um grid de 3 molduras. Sem isso, o operador lê 'faltou foto'."""
    if "c57" in esperadas:
        return None
    if codigo == "F180":
        return (
            "O formulário F180 não inclui a foto traseira (c57) desde setembro/2025 — "
            "três vistas é o checklist completo."
        )
    return "Este formulário não inclui a foto traseira (c57)."


def opcoes_validacao() -> OpcoesValidacao:
    """Listas do formulário de correção, montadas do vocabulário do domínio."""
    from app.services.checklist_validation import (  # noqa: PLC0415 — ciclo
        ROTULO_TIPO_ERRO,
        TIPOS_ERRO,
    )

    return OpcoesValidacao(
        tipos_erro=tuple(
            OpcaoValidacao(valor=tipo, rotulo=ROTULO_TIPO_ERRO[tipo]) for tipo in TIPOS_ERRO
        ),
        classes=tuple(
            OpcaoValidacao(valor=classe, rotulo=ROTULO_CLASSE[classe]) for classe in CLASSES
        ),
        severidades=tuple(
            OpcaoValidacao(valor=str(nivel), rotulo=rotulo)
            for nivel, rotulo in sorted(ROTULO_SEVERIDADE.items())
        ),
    )


def _para_validacao_da_vista(linha: ChecklistViewResult) -> ValidacaoVista | None:
    from app.services.checklist_validation import (  # noqa: PLC0415 — ciclo
        ROTULO_CLASSE_GABARITO,
        ROTULO_TIPO_ERRO,
        validacao_da_vista,
    )

    if not linha.gt_classe:
        return None
    tipo = linha.gt_tipo_erro
    return ValidacaoVista(
        estado=validacao_da_vista(linha),
        tipo_erro=tipo,
        tipo_erro_rotulo=ROTULO_TIPO_ERRO.get(tipo or ""),
        classe=linha.gt_classe,
        classe_rotulo=ROTULO_CLASSE_GABARITO.get(linha.gt_classe),
        severidade=linha.gt_severidade,
        severidade_rotulo=rotulo_severidade(linha.gt_severidade),
        observacao=linha.gt_observacao,
        por=linha.validado_por,
        em=linha.validado_em,
    )


def _para_vista(
    campo: str,
    linha: ChecklistViewResult | None,
    esperada: bool,
    determinante: str | None,
) -> VistaDetalhe:
    rotulo = ROTULO_VISTA.get(campo, campo)
    if linha is None:
        return VistaDetalhe(
            campo=campo,
            rotulo=rotulo,
            esperada=esperada,
            recebida=False,
            status=None,
            indicador=None,
            indicador_rotulo=None,
            motivo_nao_processavel=None,
            motivo_rotulo=None,
            classe=None,
            classe_rotulo=None,
            tipo_defeito=None,
            tipo_defeito_rotulo=None,
            severidade=None,
            severidade_rotulo=None,
            confianca=None,
            observacao=None,
            local=None,
            conteudo_observado=None,
            vista_confere=None,
            foto_path=None,
            foto_url=None,
            achados=(),
            erro=None,
            determinante=False,
            corrigivel=False,
            validacao=None,
        )

    achados = tuple(
        {
            **a,
            # `classe` e `tipo_defeito` eram os únicos campos do laudo sem
            # rótulo no contrato — o front supria formatando `snake_case`.
            # Agora vêm prontos, como todo o resto (ticket `v1-entregavel/02`).
            "classe_rotulo": rotulo_classe(a.get("classe")),
            "tipo_defeito_rotulo": rotulo_tipo_defeito(a.get("tipo_defeito")),
        }
        for a in (linha.achados or [])
        if isinstance(a, dict)
    )
    principal = (
        min(
            achados,
            key=lambda a: (int(a.get("severidade") or 9), -float(a.get("confianca") or 0.0)),
        )
        if achados
        else None
    )
    from app.services.checklist_validation import classe_predita  # noqa: PLC0415 — ciclo

    motivo = linha.motivo_nao_processavel
    return VistaDetalhe(
        campo=campo,
        rotulo=rotulo,
        esperada=esperada,
        recebida=True,
        status=linha.status,
        indicador=linha.conformidade,
        indicador_rotulo=ROTULO_INDICADOR.get(linha.conformidade or ""),
        motivo_nao_processavel=motivo,
        motivo_rotulo=ROTULO_MOTIVO_NAO_PROCESSAVEL.get(motivo or ""),
        classe=linha.classe,
        classe_rotulo=rotulo_classe(linha.classe),
        tipo_defeito=linha.tipo_defeito,
        tipo_defeito_rotulo=rotulo_tipo_defeito(linha.tipo_defeito),
        severidade=linha.severidade_max,
        severidade_rotulo=rotulo_severidade(linha.severidade_max),
        confianca=linha.confianca,
        observacao=_texto(principal.get("observacao")) if principal else None,
        local=_texto(principal.get("local")) if principal else None,
        conteudo_observado=linha.conteudo_observado,
        vista_confere=linha.vista_confere,
        foto_path=linha.dropbox_path,
        foto_url=url_da_foto(linha.dropbox_path),
        achados=achados,
        erro=linha.error,
        determinante=campo == (determinante or "").strip().lower(),
        corrigivel=classe_predita(linha) is not None,
        validacao=_para_validacao_da_vista(linha),
    )


def _para_equipamento(job: PipelineJob, codigo: str | None) -> EquipamentoDetalhe:
    snapshot = _snapshot(job)
    cliente, contrato, bruto, padrao_ok = _projeto(job)
    n_linhas = job.n_linhas if job.n_linhas is not None else snapshot.get("n_linhas")
    n_linhas = int(n_linhas) if isinstance(n_linhas, int) else None
    multi = bool(n_linhas and n_linhas > 1)
    patrimonio = job.patrimonio or _texto(snapshot.get("patrimonio"))
    numero_om = snapshot.get("numero_om")
    return EquipamentoDetalhe(
        codigo_checklist=_texto(snapshot.get("codigo_checklist")) or job.checklist_id,
        patrimonio=patrimonio,
        cliente=cliente,
        contrato=contrato,
        projeto_bruto=bruto,
        projeto_padrao_reconhecido=padrao_ok,
        filial=_texto(snapshot.get("filial")),
        formulario=job.formulario or _texto(snapshot.get("formulario")),
        formulario_codigo=codigo,
        data_conclusao=_data(snapshot.get("data_conclusao")),
        responsavel=_texto(snapshot.get("responsavel")),
        numero_om=numero_om if isinstance(numero_om, int) else None,
        origem=_texto(snapshot.get("origem")),
        status_sisloc=_texto(snapshot.get("status")),
        n_linhas=n_linhas,
        multi_ativo=multi,
        aviso=(
            f"Este checklist cobre {n_linhas} ativos no Sisloc; o laudo está atribuído ao "
            f"primeiro por ordem" + (f" ({patrimonio})." if patrimonio else ".")
            if multi
            else None
        ),
        lido_em=_data(snapshot.get("lido_em")),
    )


__all__ = [
    "INDICADORES",
    "ORDENACOES",
    "ROTULO_INDICADOR",
    "ROTULO_SEVERIDADE",
    "SEM_ANALISE",
    "VALIDACAO_PADRAO",
    "VALIDACOES",
    "VALIDACOES_FECHADAS",
    "ChecklistContadores",
    "ChecklistDetalhe",
    "ChecklistFacetas",
    "ChecklistFiltros",
    "ChecklistLinha",
    "ChecklistPagina",
    "EquipamentoDetalhe",
    "OpcaoValidacao",
    "OpcoesValidacao",
    "ValidacaoVista",
    "VistaDetalhe",
    "listar_checklists",
    "obter_checklist",
    "resolver_job",
    "opcoes_validacao",
    "url_da_foto",
    "validacao_de",
]
