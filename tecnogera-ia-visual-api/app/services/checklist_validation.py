"""Validação humana do laudo — HITL, ticket ``mvp-c54-c57/10``.

A validação humana é a **única fonte do F1 que o contrato exige** (Anexo I §8).
Sem ela o sistema produz laudos que ninguém confirma e o projeto não tem como
provar acurácia. Isso torna duas coisas requisito, não preferência:

**Confirmar é um clique, no checklist inteiro.** É o caso comum. Se validar for
caro, não acontece, e a métrica fica sem dado. O endpoint de confirmação não
pede nada além de "sim": copia a predição de cada vista julgada para o gabarito.

**Corrigir captura o QUÊ estava errado, por vista.** Os laudos são por vista, e
"corrigido" sem tipo só serve para contar. Com o tipo — falso positivo, classe
errada, severidade errada, foto não julgável — vira insumo de calibragem do
prompt.

Três decisões de desenho que quebram implementação ingênua
----------------------------------------------------------

**Idempotência vem da chave, não de código.** O gabarito mora na própria linha
de ``checklist_view_results``, que já é única por ``(job_id, campo)``. Validar
duas vezes é UPDATE da mesma linha: não há INSERT, logo não há como duplicar
registro nem inflar o eval. Nenhuma checagem de "já validou?" precisa existir —
e é bom que não exista, porque essa checagem é exatamente onde condição de
corrida costuma morar.

**Corrigir uma vista confirma as outras.** O operador leu o relatório inteiro
antes de clicar; o que ele não contestou, ele aceitou. Sem isso, corrigir uma
vista deixaria as demais sem gabarito e o eval mediria só as vistas erradas —
precisão artificialmente rasteira. Correção já existente **não** é sobrescrita
por confirmação: o julgamento mais específico ganha do mais genérico.

**O estado do checklist é DERIVADO das vistas.** ``pipeline_jobs.validacao`` é
cache de consulta (a lista filtra e conta em SQL), sempre recalculado por
``recalcular_validacao``. Escrevê-lo à mão em dois lugares é como ele passa a
mentir.

**Terceiro estado no gabarito.** As classes do eval são as três da taxonomia
mais ``conforme`` **e** ``nao_processavel``. Colapsar não processável em
conforme premiaria o modelo por acertar um "está tudo bem" apoiado em foto que
ninguém conseguiu julgar — é o mesmo erro que o indicador da tela evita.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.models.checklist_analysis import ChecklistViewResult
from app.services.damage_evaluator import (
    DamageEvalRecord,
    DamageEvalReport,
    DamageEvaluator,
)
from app.services.view_inspection import CLASSES, ROTULO_CLASSE

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.pipeline import PipelineJob

_log = get_logger(__name__)

# ── vocabulário ───────────────────────────────────────────────────────────────

#: Pseudo-classes do gabarito. Não são defeitos — são os dois desfechos em que
#: não há defeito a classificar, e precisam existir no eixo da métrica para o
#: modelo poder acertar ou errar cada um.
CLASSE_CONFORME = "conforme"
CLASSE_NAO_PROCESSAVEL = "nao_processavel"

#: Todo valor que ``gt_classe`` pode assumir.
CLASSES_GABARITO: tuple[str, ...] = (CLASSE_CONFORME, CLASSE_NAO_PROCESSAVEL, *CLASSES)

ROTULO_CLASSE_GABARITO: dict[str, str] = {
    CLASSE_CONFORME: "Conforme",
    CLASSE_NAO_PROCESSAVEL: "Não processável",
    **ROTULO_CLASSE,
}

#: Os quatro tipos de erro, exatamente como especificados no ticket 09.
#: ``None`` (ausência de tipo) é o quinto estado e significa "confirmado".
TIPO_FALSO_POSITIVO = "falso_positivo"
TIPO_CLASSE_ERRADA = "classe_errada"
TIPO_SEVERIDADE_ERRADA = "severidade_errada"
TIPO_NAO_JULGAVEL = "nao_julgavel"

TIPOS_ERRO: tuple[str, ...] = (
    TIPO_FALSO_POSITIVO,
    TIPO_CLASSE_ERRADA,
    TIPO_SEVERIDADE_ERRADA,
    TIPO_NAO_JULGAVEL,
)

ROTULO_TIPO_ERRO: dict[str, str] = {
    TIPO_FALSO_POSITIVO: "Falso positivo — não há defeito aqui",
    TIPO_CLASSE_ERRADA: "Classe errada",
    TIPO_SEVERIDADE_ERRADA: "Severidade errada",
    TIPO_NAO_JULGAVEL: "Foto não era julgável",
}

#: Estados da validação de UMA vista, derivados do gabarito.
VISTA_PENDENTE = "pendente"
VISTA_CONFIRMADA = "confirmado"
VISTA_CORRIGIDA = "corrigido"


class ValidacaoInvalidaError(ValueError):
    """Pedido de validação que o domínio recusa — vira 422 no router."""


# ── projeção predição → classe do eval ────────────────────────────────────────


def classe_predita(linha: ChecklistViewResult) -> str | None:
    """Classe que o MODELO afirmou para esta vista, no eixo do eval.

    ``None`` quando não há predição comparável: vista que falhou por
    infraestrutura ou que o freio de gasto nem despachou. Elas não entram no
    eval — um erro de download não é um erro de classificação, e contá-lo como
    tal faria o F1 medir a rede em vez do modelo.
    """
    conformidade = (linha.conformidade or "").strip()
    if conformidade == "nao_processavel":
        return CLASSE_NAO_PROCESSAVEL
    if conformidade == "conforme":
        return CLASSE_CONFORME
    if conformidade == "nao_conforme":
        # `classe` é denormalizada do achado principal; sem ela não há o que
        # comparar, e inventar uma classe padrão contaminaria a métrica.
        return linha.classe if linha.classe in CLASSES else None
    return None


def validacao_da_vista(linha: ChecklistViewResult) -> str:
    if not linha.gt_classe:
        return VISTA_PENDENTE
    return VISTA_CORRIGIDA if linha.gt_tipo_erro else VISTA_CONFIRMADA


# ── resultado ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResultadoValidacao:
    """O que uma confirmação ou correção deixou no banco."""

    validacao: str
    validado_por: str | None
    validado_em: datetime | None
    #: Vistas com gabarito depois da operação.
    vistas_validadas: int
    #: Vistas que tinham predição comparável — o denominador honesto.
    vistas_validaveis: int
    vistas_corrigidas: int


@dataclass(frozen=True)
class ResultadoEval:
    """Relatório do eval + o que só o HITL sabe dizer."""

    relatorio: DamageEvalReport
    checklists_validados: int
    vistas_validadas: int
    #: Contagem por tipo de erro — é a leitura de calibragem do prompt.
    por_tipo_erro: dict[str, int] = field(default_factory=dict)


# ── consultas internas ────────────────────────────────────────────────────────


def _utc(momento: datetime) -> datetime:
    """Datas do banco em ordem comparável.

    O Postgres devolve tudo com fuso; o SQLite dos testes devolve **naive** o
    que já estava gravado e aware o que acabou de ser atribuído na sessão.
    Comparar os dois levanta ``TypeError`` — e levantaria em produção também no
    dia em que uma linha antiga tivesse sido gravada sem fuso.
    """
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _linhas_do_job(db: Session, job: PipelineJob) -> list[ChecklistViewResult]:
    return (
        db.query(ChecklistViewResult)
        .filter(ChecklistViewResult.job_id == job.id)
        .order_by(ChecklistViewResult.campo.asc())
        .all()
    )


def _validaveis(linhas: list[ChecklistViewResult]) -> list[ChecklistViewResult]:
    return [linha for linha in linhas if classe_predita(linha) is not None]


# ── rollup ────────────────────────────────────────────────────────────────────


def recalcular_validacao(job: PipelineJob, linhas: list[ChecklistViewResult]) -> str:
    """Estado do checklist, derivado das vistas. Escreve em ``job`` e devolve.

    ``corrigido`` domina ``confirmado``: um checklist com uma vista corrigida e
    três confirmadas foi corrigido — é a informação que o operador procura na
    fila. E validação parcial volta a ``pendente``, porque meio checklist
    julgado ainda é trabalho a fazer.
    """
    validaveis = _validaveis(linhas)
    com_gabarito = [linha for linha in validaveis if linha.gt_classe]

    if not com_gabarito:
        estado = VISTA_PENDENTE
    elif any(linha.gt_tipo_erro for linha in com_gabarito):
        estado = VISTA_CORRIGIDA
    elif len(com_gabarito) == len(validaveis):
        estado = VISTA_CONFIRMADA
    else:
        estado = VISTA_PENDENTE

    job.validacao = estado
    if estado == VISTA_PENDENTE:
        job.validado_por = None
        job.validado_em = None
    else:
        # Quem validou é a última pessoa a tocar o checklist; a atribuição fina
        # continua por vista, em `checklist_view_results.validado_por`.
        carimbos = [
            (linha.validado_em, linha.validado_por)
            for linha in com_gabarito
            if linha.validado_em is not None
        ]
        if carimbos:
            job.validado_em, job.validado_por = max(carimbos, key=lambda par: _utc(par[0]))
    return estado


def _resultado(job: PipelineJob, linhas: list[ChecklistViewResult]) -> ResultadoValidacao:
    validaveis = _validaveis(linhas)
    return ResultadoValidacao(
        validacao=job.validacao or VISTA_PENDENTE,
        validado_por=job.validado_por,
        validado_em=job.validado_em,
        vistas_validadas=sum(1 for linha in validaveis if linha.gt_classe),
        vistas_validaveis=len(validaveis),
        vistas_corrigidas=sum(1 for linha in validaveis if linha.gt_tipo_erro),
    )


# ── operações ─────────────────────────────────────────────────────────────────


def _confirmar_linhas(
    validaveis: list[ChecklistViewResult], *, por: str | None, agora: datetime
) -> None:
    """Copia a predição para o gabarito das vistas ainda não corrigidas.

    Vista já corrigida à mão é preservada: confirmação é o julgamento genérico
    ("o relatório está certo"), correção é o específico, e o específico ganha.
    Sem essa regra, um clique em Confirmar apagaria em silêncio o trabalho de
    calibragem feito antes.
    """
    for linha in validaveis:
        if linha.gt_tipo_erro:
            continue
        linha.gt_classe = classe_predita(linha)
        linha.gt_severidade = linha.severidade_max
        linha.gt_tipo_erro = None
        linha.validado_por = por
        linha.validado_em = agora


def confirmar(db: Session, job: PipelineJob, *, por: str | None = None) -> ResultadoValidacao:
    """Um clique: o laudo do checklist inteiro vira gabarito.

    Idempotente por construção — reescreve as mesmas linhas com os mesmos
    valores. Chamar duas vezes não cria registro nem muda a métrica.
    """
    linhas = _linhas_do_job(db, job)
    validaveis = _validaveis(linhas)
    if not validaveis:
        raise ValidacaoInvalidaError(
            "Este checklist não tem laudo para validar — nenhuma vista produziu "
            "veredito comparável."
        )

    _confirmar_linhas(validaveis, por=por, agora=datetime.now(UTC))
    estado = recalcular_validacao(job, linhas)
    db.commit()

    _log.info(
        "checklist_validacao_confirmada",
        job_id=str(job.id),
        checklist_id=job.checklist_id,
        validacao=estado,
        vistas=len(validaveis),
        por=por,
    )
    return _resultado(job, linhas)


def corrigir(  # noqa: PLR0913 — o formulário do ticket 09 tem esses campos
    db: Session,
    job: PipelineJob,
    *,
    campo: str,
    tipo_erro: str,
    classe: str | None = None,
    severidade: int | None = None,
    observacao: str | None = None,
    por: str | None = None,
) -> ResultadoValidacao:
    """Correção de UMA vista, dizendo o que estava errado.

    As demais vistas do checklist são confirmadas junto (ver o docstring do
    módulo). Corrigir a mesma vista de novo sobrescreve — o gabarito é a última
    palavra, não um log.
    """
    if tipo_erro not in TIPOS_ERRO:
        raise ValidacaoInvalidaError(f"tipo_erro deve ser um de: {list(TIPOS_ERRO)}")

    alvo = campo.strip().lower()
    linhas = _linhas_do_job(db, job)
    validaveis = _validaveis(linhas)
    linha = next((item for item in validaveis if item.campo.strip().lower() == alvo), None)
    if linha is None:
        raise ValidacaoInvalidaError(
            f"A vista '{campo}' não tem laudo neste checklist — não há o que corrigir."
        )

    gt_classe, gt_severidade = _gabarito_da_correcao(
        linha, tipo_erro=tipo_erro, classe=classe, severidade=severidade
    )

    agora = datetime.now(UTC)
    _confirmar_linhas(validaveis, por=por, agora=agora)

    linha.gt_classe = gt_classe
    linha.gt_severidade = gt_severidade
    linha.gt_tipo_erro = tipo_erro
    linha.gt_observacao = (observacao or "").strip() or None
    linha.validado_por = por
    linha.validado_em = agora

    recalcular_validacao(job, linhas)
    db.commit()

    _log.info(
        "checklist_validacao_corrigida",
        job_id=str(job.id),
        checklist_id=job.checklist_id,
        campo=alvo,
        tipo_erro=tipo_erro,
        gt_classe=gt_classe,
        gt_severidade=gt_severidade,
        por=por,
    )
    return _resultado(job, linhas)


def _gabarito_da_correcao(
    linha: ChecklistViewResult,
    *,
    tipo_erro: str,
    classe: str | None,
    severidade: int | None,
) -> tuple[str, int | None]:
    """Traduz "o que estava errado" em gabarito ``(classe, severidade)``.

    Cada tipo de erro afirma uma verdade diferente, e é por isso que o tipo
    precisa ser capturado em vez de um "está errado" genérico.
    """
    if tipo_erro == TIPO_FALSO_POSITIVO:
        # "Não há defeito aqui" — a verdade é conforme, sem severidade.
        return CLASSE_CONFORME, None

    if tipo_erro == TIPO_NAO_JULGAVEL:
        # A foto não permitia veredito. Não vira conforme (ver docstring).
        return CLASSE_NAO_PROCESSAVEL, None

    if tipo_erro == TIPO_CLASSE_ERRADA:
        if classe not in CLASSES:
            raise ValidacaoInvalidaError(f"classe_errada exige 'classe' em: {list(CLASSES)}")
        # Aceita também o falso NEGATIVO: vista predita conforme cuja classe
        # certa o operador informa — é o quinto caso que a lista de quatro tipos
        # não nomeia, e que apareceria como buraco no recall se não coubesse
        # aqui. A severidade acompanha, se ele disser qual.
        if severidade is not None:
            return classe, _severidade_valida(severidade)
        return classe, linha.severidade_max

    # severidade_errada
    predita = classe_predita(linha)
    if predita not in CLASSES:
        raise ValidacaoInvalidaError(
            "severidade_errada só se aplica a vista com achado — esta vista foi "
            f"julgada '{predita}'. Use classe_errada ou falso_positivo."
        )
    if severidade is None:
        raise ValidacaoInvalidaError("severidade_errada exige 'severidade' entre 1 e 4")
    return predita, _severidade_valida(severidade)


def _severidade_valida(severidade: int) -> int:
    if severidade not in (1, 2, 3, 4):
        raise ValidacaoInvalidaError("severidade deve estar entre 1 (crítica) e 4 (baixa)")
    return severidade


# ── eval ──────────────────────────────────────────────────────────────────────


def registros_de_eval(db: Session) -> list[DamageEvalRecord]:
    """Pares (predito, verdadeiro) das vistas com gabarito humano.

    Uma linha de vista = um registro. ``angle`` recebe o campo (``c54``…), o que
    faz ``per_angle`` do relatório virar "acurácia por vista" de graça — a
    pergunta seguinte inevitável é qual vista o modelo mais erra.
    """
    linhas = (
        db.query(ChecklistViewResult)
        .filter(ChecklistViewResult.gt_classe.isnot(None))
        .order_by(ChecklistViewResult.created_at.asc())
        .all()
    )
    registros: list[DamageEvalRecord] = []
    for linha in linhas:
        predita = classe_predita(linha)
        if predita is None or linha.gt_classe not in CLASSES_GABARITO:
            continue
        registros.append(
            DamageEvalRecord(
                event_id=f"{linha.job_id}:{linha.campo}",
                predicted_class=predita,
                true_class=linha.gt_classe,
                moment=None,
                angle=linha.campo,
            )
        )
    return registros


def avaliar(db: Session) -> ResultadoEval:
    """P/R/F1 por classe sobre os checklists validados.

    A aritmética é a do ``DamageEvaluator`` (IAVS-066), reusada sem cópia: o que
    muda entre avarias e checklists é a unidade e a projeção de classe, não o
    cálculo.
    """
    registros = registros_de_eval(db)
    relatorio = DamageEvaluator.evaluate(registros)

    checklists = {registro.event_id.split(":", 1)[0] for registro in registros}
    por_tipo_erro: dict[str, int] = {}
    for (tipo,) in (
        db.query(ChecklistViewResult.gt_tipo_erro)
        .filter(ChecklistViewResult.gt_tipo_erro.isnot(None))
        .all()
    ):
        por_tipo_erro[tipo] = por_tipo_erro.get(tipo, 0) + 1

    return ResultadoEval(
        relatorio=relatorio,
        checklists_validados=len(checklists),
        vistas_validadas=len(registros),
        por_tipo_erro=por_tipo_erro,
    )


__all__ = [
    "CLASSES_GABARITO",
    "CLASSE_CONFORME",
    "CLASSE_NAO_PROCESSAVEL",
    "ROTULO_CLASSE_GABARITO",
    "ROTULO_TIPO_ERRO",
    "TIPOS_ERRO",
    "ResultadoEval",
    "ResultadoValidacao",
    "ValidacaoInvalidaError",
    "avaliar",
    "classe_predita",
    "confirmar",
    "corrigir",
    "recalcular_validacao",
    "registros_de_eval",
    "validacao_da_vista",
]
