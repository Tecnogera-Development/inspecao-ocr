"""Freios de gasto de LLM — ticket ``mvp-c54-c57/08``.

A chave da OpenAI é real e é da conta da Tecnogera. Até este ticket a esteira
não tinha teto nenhum: um backfill descuidado (18.338 checklists têm as quatro
vistas) viraria fatura de três dígitos sem ninguém autorizar. Este módulo é o
lugar único onde se decide **se a próxima chamada pode acontecer**.

Três freios, em ordem de severidade:

1. **Kill switch** (``LLM_DISPATCH_ENABLED``, default ``false``). Permite subir
   a esteira inteira ingerindo e materializando jobs sem gastar um centavo.
   Default fechado porque os dois modos de falha não são simétricos: subir sem
   despachar custa uma variável de ambiente; subir despachando por engano custa
   dinheiro que não volta.
2. **Orçamento do mês** (``LLM_MONTHLY_BUDGET_USD``). Somado do custo **medido
   e persistido** (``checklist_view_results.cost_usd``), não estimado, e
   comparado **antes de cada chamada** — não no fim da rodada. Estourou, para
   de despachar e loga em ``error``. É freio, não aviso.
3. **Teto por rodada** (``LLM_MAX_CALLS_PER_RUN``). Limita o estrago de um loop
   ou de um backlog acumulado a uma rodada. O que não coube fica ``pending``
   para a rodada seguinte, e o tamanho da fila vai para o log.

Um quarto guarda-corpo, de natureza diferente, vive aqui também: **provider
fake fora de desenvolvimento**. ``Settings`` já recusa o boot em produção sem
chave de LLM; esta é a segunda linha, no ponto de despacho, para o caso de
alguém subir em ``staging`` com a config errada. Laudo fictício na tela do
operador é indistinguível de um real — é o pior modo de falha do projeto.

O acumulado é lido do banco a cada rodada, não guardado em memória: o worker
reinicia, escala horizontalmente e o mês vira. A fonte de verdade é a tabela.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.core.config import AppEnv, Settings, get_settings
from app.core.logging import get_logger
from app.models.checklist_analysis import ChecklistViewResult

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_log = get_logger(__name__)

#: Motivos de bloqueio. São persistidos em ``pipeline_jobs.metrics`` e vão para
#: o log — nomes estáveis, não mensagens.
MOTIVO_DISPATCH_DESABILITADO = "dispatch_desabilitado"
MOTIVO_ORCAMENTO_ESTOURADO = "orcamento_mensal_estourado"
MOTIVO_TETO_DE_CHAMADAS = "teto_de_chamadas_por_rodada"
MOTIVO_PROVIDER_FAKE = "provider_fake_fora_de_desenvolvimento"

#: Ambientes em que um provider fake é aceitável.
_AMBIENTES_COM_FAKE = frozenset({AppEnv.DEVELOPMENT, AppEnv.TEST})


@dataclass(frozen=True, slots=True)
class Decisao:
    """Veredito do guarda sobre despachar (a rodada) ou chamar (uma vista)."""

    permitido: bool
    motivo: str | None = None

    def __bool__(self) -> bool:
        return self.permitido


PERMITIDO = Decisao(permitido=True)


def inicio_do_mes(agora: datetime | None = None) -> datetime:
    """Primeiro instante do mês corrente em UTC.

    UTC e não horário local: o worker roda em container, a fatura da OpenAI é
    em UTC, e um mês que começa em fuso diferente do provedor produz um
    acumulado que nunca fecha com o extrato.
    """
    ref = agora or datetime.now(UTC)
    return ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class LLMBudgetGuard:
    """Decide se a próxima chamada de LLM pode acontecer, e conta as que foram.

    Uma instância por rodada do cron. ``avaliar_rodada()`` roda uma vez, no
    início; ``antes_da_chamada()`` roda antes de **cada** vista.
    """

    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._chamadas = 0
        self._custo_da_rodada = 0.0
        self._gasto_no_mes_inicial = 0.0
        self._motivo_de_parada: str | None = None

    # ── contadores ───────────────────────────────────────────────────────────

    @property
    def chamadas(self) -> int:
        """Chamadas de LLM efetivamente despachadas nesta rodada."""
        return self._chamadas

    @property
    def custo_da_rodada(self) -> float:
        return round(self._custo_da_rodada, 6)

    @property
    def gasto_no_mes(self) -> float:
        """Acumulado do mês: o que já estava no banco + o desta rodada."""
        return round(self._gasto_no_mes_inicial + self._custo_da_rodada, 6)

    @property
    def motivo_de_parada(self) -> str | None:
        """Por que a rodada parou de despachar, se parou."""
        return self._motivo_de_parada

    @property
    def chamadas_restantes(self) -> int:
        return max(0, self._settings.llm_max_calls_per_run - self._chamadas)

    def gasto_persistido_no_mes(self) -> float:
        """Soma medida de ``cost_usd`` no mês corrente. Zero se não houver nada."""
        total = self._db.execute(
            select(func.coalesce(func.sum(ChecklistViewResult.cost_usd), 0.0)).where(
                ChecklistViewResult.created_at >= inicio_do_mes()
            )
        ).scalar_one()
        return float(total or 0.0)

    # ── decisões ─────────────────────────────────────────────────────────────

    def avaliar_rodada(self, *, provider_efetivo: str | None = None) -> Decisao:
        """Vale a pena abrir a rodada? Avaliada uma vez, antes de tocar no banco.

        Também é onde o acumulado do mês é lido — uma consulta por rodada, não
        uma por chamada.
        """
        if not self._settings.llm_dispatch_enabled:
            _log.warning(
                "llm_dispatch_desabilitado",
                consequencia="jobs ficam pending; ligue LLM_DISPATCH_ENABLED para gastar",
            )
            return self._parar(MOTIVO_DISPATCH_DESABILITADO)

        provider = provider_efetivo or self._settings.llm_provider_efetivo
        if provider == "fake" and self._settings.app_env not in _AMBIENTES_COM_FAKE:
            _log.error(
                "llm_provider_fake_fora_de_desenvolvimento",
                app_env=self._settings.app_env.value,
                consequencia=(
                    "laudo fictício seria persistido como se fosse real — despacho abortado"
                ),
                acao="configure OPENAI_API_KEY (ou ANTHROPIC_API_KEY)",
            )
            return self._parar(MOTIVO_PROVIDER_FAKE)

        self._gasto_no_mes_inicial = self.gasto_persistido_no_mes()
        if self._gasto_no_mes_inicial >= self._settings.llm_monthly_budget_usd:
            _log.error(
                "llm_orcamento_mensal_estourado",
                gasto_usd=round(self._gasto_no_mes_inicial, 4),
                teto_usd=self._settings.llm_monthly_budget_usd,
                consequencia="despacho parado até virar o mês ou subir LLM_MONTHLY_BUDGET_USD",
            )
            return self._parar(MOTIVO_ORCAMENTO_ESTOURADO)

        if self._settings.llm_max_calls_per_run <= 0:
            return self._parar(MOTIVO_TETO_DE_CHAMADAS)

        return PERMITIDO

    def antes_da_chamada(self) -> Decisao:
        """Pode gastar mais uma chamada? Checado por vista, não por checklist."""
        if self._chamadas >= self._settings.llm_max_calls_per_run:
            return self._parar(MOTIVO_TETO_DE_CHAMADAS)
        if self.gasto_no_mes >= self._settings.llm_monthly_budget_usd:
            _log.error(
                "llm_orcamento_mensal_estourado",
                gasto_usd=self.gasto_no_mes,
                teto_usd=self._settings.llm_monthly_budget_usd,
                consequencia="despacho parado no meio da rodada",
            )
            return self._parar(MOTIVO_ORCAMENTO_ESTOURADO)
        return PERMITIDO

    def registrar_chamada(self, custo_usd: float) -> None:
        """Contabiliza uma chamada já feita. O custo é o medido, não o estimado."""
        self._chamadas += 1
        self._custo_da_rodada += max(0.0, custo_usd)

    def _parar(self, motivo: str) -> Decisao:
        self._motivo_de_parada = motivo
        return Decisao(permitido=False, motivo=motivo)
