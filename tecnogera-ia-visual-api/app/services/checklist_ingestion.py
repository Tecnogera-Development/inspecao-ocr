"""Ingestão agendada de checklists do Sisloc — ticket ``mvp-c54-c57/07``.

Torna a esteira automática: a cada 30 min descobre no Dropbox os checklists de
gerador com as vistas obrigatórias e materializa um ``pipeline_job`` para cada
um, sem reprocessar o que já passou.

Ordem obrigatória (nunca inverter)::

    Dropbox → checklist_id → SELECT (11 colunas) FROM dbo.checklist_produto
            → formulário → status = 'Concluído'
            → só então avaliar os campos c54/c55/c56

Decisões estruturais deste módulo
---------------------------------

**Descoberta por delta de cursor.** Varrer ``/Sisloc`` inteiro custou 67 min na
medição do ticket 01 — não cabe num cron de 30. O bootstrap pega o cursor do
"agora" (``files_list_folder_get_latest_cursor``, zero entradas) e cada rodada
seguinte lê só o que mudou. Isso também **é** o marco de corte por ativação:
o histórico anterior nunca entra. ``CHECKLIST_INGEST_SINCE`` acrescenta um piso
explícito por data, e ``CHECKLIST_INGEST_BOOTSTRAP_FULL`` permite o backfill
deliberado (paginado entre rodadas, nunca numa tacada).

**Dedup em três camadas.** (1) o cursor só entrega arquivo novo; (2) a tabela
``checklist_ingest_state`` tem ``checklist_id`` como **chave primária** — é o
que impede duas rodadas sobrepostas de materializarem dois jobs, porque estado
e job nascem no mesmo SAVEPOINT; (3) diff contra ``pipeline_jobs`` cobre o que
foi processado à mão antes de a esteira existir.

**Acumulação de campos.** O delta entrega o que mudou desde a última rodada. Um
checklist cujas fotos caem em dois deltas apareceria incompleto nos dois — a
coluna ``campos`` acumula, e os ``pendente`` recentes são reavaliados a cada
rodada. Zero chamada extra ao Dropbox.

**O ERP é reconsultado a cada rodada, sempre** (ticket 17). Antes, quem já
tinha ``formulario`` no ledger era poupado da consulta — otimização que se
tornou um bug no instante em que o filtro passou a exigir
``status = 'Concluído'``: o formulário não muda, mas o **status muda**, e um
checklist ``A Conferir`` cacheado ficaria descartado para sempre. São 14,8% do
volume; o custo de reconsultar é uma query em lote por rodada, que já existia.

**Enriquecimento na mesma consulta.** As 11 colunas viajam no mesmo
ida-e-volta que decide o filtro. O job nasce com ``formulario``, ``patrimonio``
e ``projeto`` tipados e com o ``sisloc_snapshot`` congelado — o ERP muda, o
laudo não deveria.

**Degradação.** Sisloc fora do ar (VPN caída → ``HYT00 Login timeout expired``)
não derruba o worker e **não avança o cursor**: nada é perdido, a rodada
seguinte relê o mesmo delta.

Este módulo **não chama LLM**. Ele materializa jobs ``pending``; despachar a
execução é do ticket 08.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationError, IntegrationError
from app.core.logging import get_logger
from app.models.ingest import (
    CURSOR_CHECKLISTS,
    STATUS_DESCARTADO,
    STATUS_MATERIALIZADO,
    STATUS_PENDENTE,
    ChecklistIngestState,
    IngestCursor,
)
from app.models.pipeline import PipelineJob, novo_job_enriquecido
from app.services.checklist_filter import FORMULARIOS_ALVO, MotivoDescarte, avaliar

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

    from app.models.dropbox import ImageMetadata
    from app.models.sisloc import SislocChecklist
    from app.services.dropbox import DropboxService
    from app.services.sisloc import SislocService

_log = get_logger(__name__)

#: Rótulo de contagem para checklist que já tinha job criado fora da esteira.
MOTIVO_JOB_PREEXISTENTE = "job_preexistente"


@dataclass
class ChecklistIngestResult:
    """O que uma rodada do cron fez — e, sobretudo, o que descartou e por quê."""

    bootstrap: bool = False
    imagens: int = 0
    nomes_ignorados: int = 0
    candidatos: int = 0
    avaliados: int = 0
    jobs_criados: int = 0
    ja_processados: int = 0
    adiados: int = 0
    #: Contador por motivo de descarte, qualificado:
    #: ``campo_faltante:c55``, ``formulario_fora_whitelist:F013``,
    #: ``status_nao_concluido:A Conferir``, ``formulario_vazio``.
    descartes: Counter[str] = field(default_factory=Counter)
    job_ids: list[uuid.UUID] = field(default_factory=list)
    sisloc_indisponivel: bool = False
    cursor_resetado: bool = False
    has_more: bool = False
    #: Jobs criados a partir de um checklist que a view devolve em mais de uma
    #: linha — o operador vê UM patrimônio de N. Contado à parte porque é o
    #: número que diz se a tela precisa mesmo do aviso.
    multi_ativo: int = 0

    @property
    def status_nao_concluido(self) -> int:
        """Quantos foram adiados por checklist ainda aberto no ERP.

        Separado de ``campo_faltante`` de propósito: ali a Tecnogera cobra uma
        foto do técnico, aqui cobra o fechamento do checklist no Sisloc.
        """
        return sum(
            n
            for rotulo, n in self.descartes.items()
            if rotulo.split(":", 1)[0] == MotivoDescarte.STATUS_NAO_CONCLUIDO.value
        )

    def como_log(self) -> dict[str, object]:
        return {
            "bootstrap": self.bootstrap,
            "imagens": self.imagens,
            "candidatos": self.candidatos,
            "avaliados": self.avaliados,
            "jobs_criados": self.jobs_criados,
            "ja_processados": self.ja_processados,
            "adiados": self.adiados,
            "nomes_ignorados": self.nomes_ignorados,
            "descartes": dict(self.descartes),
            "status_nao_concluido": self.status_nao_concluido,
            "multi_ativo": self.multi_ativo,
            "sisloc_indisponivel": self.sisloc_indisponivel,
            "cursor_resetado": self.cursor_resetado,
            "has_more": self.has_more,
        }


class ChecklistIngestionService:
    """Varre ``/Sisloc`` por delta, filtra por formulário e cria ``pipeline_jobs``."""

    def __init__(
        self,
        db: Session,
        dropbox: DropboxService,
        sisloc: SislocService,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._dropbox = dropbox
        self._sisloc = sisloc
        self._settings = settings or get_settings()

    # ── entrada ──────────────────────────────────────────────────────────────

    def scan_and_ingest(self) -> ChecklistIngestResult:
        """Uma rodada completa. Nunca levanta por indisponibilidade externa."""
        cursor_row = self._db.get(IngestCursor, CURSOR_CHECKLISTS)
        if cursor_row is None:
            return self._bootstrap()

        delta = self._dropbox.list_checklist_delta(cursor_row.cursor)
        if delta.reset:
            # Dropbox invalidou o cursor: recomeça do estado atual. O ledger
            # sobrevive, então nada já materializado volta a ser processado.
            self._db.delete(cursor_row)
            self._db.commit()
            resultado = self._bootstrap()
            resultado.cursor_resetado = True
            return resultado

        imagens = self._aplicar_marco_de_corte(delta.images)
        return self._processar(
            imagens,
            novo_cursor=delta.cursor,
            cursor_row=cursor_row,
            nomes_ignorados=delta.ignorados,
            has_more=delta.has_more,
        )

    # ── bootstrap ────────────────────────────────────────────────────────────

    def _bootstrap(self) -> ChecklistIngestResult:
        """Primeira rodada: fixa o marco de corte na ativação.

        Default (``CHECKLIST_INGEST_BOOTSTRAP_FULL=false``): pega só o cursor do
        "agora", sem baixar entrada nenhuma — o histórico não entra na esteira.
        Com a flag ligada, começa uma listagem completa **paginada**, que
        avança um pedaço por rodada.
        """
        raiz = self._settings.checklist_ingest_root_efetiva
        if self._settings.checklist_ingest_bootstrap_full:
            delta = self._dropbox.iniciar_listagem_completa(raiz)
            imagens = self._aplicar_marco_de_corte(delta.images)
            resultado = self._processar(
                imagens,
                novo_cursor=delta.cursor,
                cursor_row=None,
                nomes_ignorados=delta.ignorados,
                has_more=delta.has_more,
            )
            resultado.bootstrap = True
            return resultado

        cursor = self._dropbox.latest_checklist_cursor(raiz)
        self._db.add(IngestCursor(name=CURSOR_CHECKLISTS, cursor=cursor))
        self._db.commit()
        _log.info("checklist_ingest_bootstrap", raiz=raiz, modo="cursor")
        return ChecklistIngestResult(bootstrap=True)

    # ── marco de corte ───────────────────────────────────────────────────────

    def _aplicar_marco_de_corte(self, imagens: Iterable[ImageMetadata]) -> list[ImageMetadata]:
        """Descarta arquivo anterior a ``CHECKLIST_INGEST_SINCE``.

        Sem a variável configurada, o corte já é o bootstrap do cursor e nada
        precisa ser filtrado. Arquivo sem ``server_modified`` é mantido — a
        ausência do metadado não é evidência de que seja antigo.
        """
        corte = self._settings.checklist_ingest_since
        if corte is None:
            return list(imagens)
        return [
            img
            for img in imagens
            if img.server_modified is None or img.server_modified.date() >= corte
        ]

    # ── rodada ───────────────────────────────────────────────────────────────

    def _processar(
        self,
        imagens: list[ImageMetadata],
        *,
        novo_cursor: str,
        cursor_row: IngestCursor | None,
        nomes_ignorados: int,
        has_more: bool,
    ) -> ChecklistIngestResult:
        resultado = ChecklistIngestResult(
            imagens=len(imagens), nomes_ignorados=nomes_ignorados, has_more=has_more
        )

        vistos = self._agrupar_por_checklist(imagens)
        estados = self._carregar_ou_criar_estados(vistos)
        pendentes = self._pendentes_para_reavaliar(exclui=set(vistos))
        estados.update(pendentes)
        resultado.candidatos = len(estados)

        avaliaveis, adiados = self._selecionar_avaliaveis(estados)
        resultado.adiados = adiados

        # SOMENTE SELECT, em lote, para TODOS os avaliáveis — inclusive os que
        # já têm `formulario` no ledger. O formulário não muda, mas o `status`
        # muda, e é ele que decide se o checklist entra: cachear a linha
        # congelaria um `A Conferir` como descarte permanente. Se o Sisloc cair,
        # a rodada inteira é abortada sem avançar o cursor — o delta é relido na
        # próxima.
        try:
            linhas = self._sisloc.fetch_checklists(sorted(avaliaveis))
        except (IntegrationError, ConfigurationError) as exc:
            self._db.rollback()
            resultado.sisloc_indisponivel = True
            _log.warning(
                "checklist_ingest_sisloc_indisponivel",
                ids=len(avaliaveis),
                error=getattr(exc, "message", str(exc)),
                consequencia="cursor não avança; nova tentativa na próxima rodada",
            )
            return resultado

        ja_com_job = self._checklists_com_job(set(avaliaveis))

        for checklist_id, estado in sorted(avaliaveis.items()):
            resultado.avaliados += 1
            linha = linhas.get(checklist_id)
            if checklist_id in ja_com_job:
                estado.formulario = (linha.formulario if linha else None) or estado.formulario
                estado.status = STATUS_MATERIALIZADO
                estado.motivo = MOTIVO_JOB_PREEXISTENTE
                estado.job_id = ja_com_job[checklist_id]
                resultado.ja_processados += 1
                continue

            veredito = avaliar(
                linha.formulario if linha else None,
                estado.campos_set,
                status=linha.status if linha else None,
                formularios_alvo=FORMULARIOS_ALVO,
                tem_linha_no_erp=linha is not None,
            )
            if linha is not None and linha.formulario:
                estado.formulario = linha.formulario

            if not veredito.aprovado:
                estado.status = STATUS_DESCARTADO if veredito.terminal else STATUS_PENDENTE
                estado.motivo = veredito.rotulo
                resultado.descartes[veredito.rotulo] += 1
                continue

            job_id = self._materializar(checklist_id, estado, linha)
            if job_id is None:
                # Outra rodada ganhou a corrida — comportamento correto do dedup.
                resultado.ja_processados += 1
                continue
            resultado.jobs_criados += 1
            resultado.job_ids.append(job_id)
            if linha is not None and linha.n_linhas > 1:
                resultado.multi_ativo += 1

        self._persistir_cursor(cursor_row, novo_cursor)
        self._db.commit()
        return resultado

    # ── passos ───────────────────────────────────────────────────────────────

    @staticmethod
    def _agrupar_por_checklist(imagens: Iterable[ImageMetadata]) -> dict[str, set[str]]:
        """``checklist_id -> {campos}``, com o parser real do Sisloc."""
        agrupado: dict[str, set[str]] = {}
        for img in imagens:
            agrupado.setdefault(img.parsed.checklist_id, set()).add(
                img.parsed.field_name.strip().lower()
            )
        return agrupado

    def _carregar_ou_criar_estados(
        self, vistos: dict[str, set[str]]
    ) -> dict[str, ChecklistIngestState]:
        """Acumula os campos no ledger; ignora o que já teve desfecho final."""
        if not vistos:
            return {}
        existentes = {
            st.checklist_id: st
            for st in self._db.query(ChecklistIngestState)
            .filter(ChecklistIngestState.checklist_id.in_(sorted(vistos)))
            .all()
        }
        agora = datetime.now(UTC)
        ativos: dict[str, ChecklistIngestState] = {}
        for checklist_id, campos in vistos.items():
            estado = existentes.get(checklist_id)
            if estado is None:
                novo = ChecklistIngestState(
                    checklist_id=checklist_id,
                    campos=",".join(sorted(campos)),
                    status=STATUS_PENDENTE,
                    first_seen_at=agora,
                    last_seen_at=agora,
                )
                # Um SAVEPOINT por linha: se outra rodada inseriu o mesmo
                # checklist_id no meio do caminho, só esta linha é desfeita — e
                # ela some junto, porque quem ganhou a corrida vai materializar.
                try:
                    with self._db.begin_nested():
                        self._db.add(novo)
                        self._db.flush()
                except IntegrityError:
                    _log.warning("checklist_ingest_dedup_race", checklist_id=checklist_id)
                    continue
                ativos[checklist_id] = novo
                continue
            if estado.status in (STATUS_MATERIALIZADO, STATUS_DESCARTADO):
                continue  # desfecho final: não reprocessa
            estado.campos = ",".join(sorted(estado.campos_set | campos))
            estado.last_seen_at = agora
            ativos[checklist_id] = estado
        return ativos

    def _pendentes_para_reavaliar(
        self, *, exclui: set[str]
    ) -> dict[str, ChecklistIngestState]:
        """Checklists incompletos recentes, revisitados sem custo de Dropbox.

        Cobre dois casos reais: fotos que chegam em deltas diferentes e a linha
        do ERP que aparece depois da foto. Passada a janela
        (``CHECKLIST_INGEST_RETRY_DAYS``), o checklist para de ser revisitado.
        """
        dias = self._settings.checklist_ingest_retry_days
        if dias <= 0:
            return {}
        limite = datetime.now(UTC) - timedelta(days=dias)
        linhas = (
            self._db.query(ChecklistIngestState)
            .filter(
                ChecklistIngestState.status == STATUS_PENDENTE,
                ChecklistIngestState.last_seen_at >= limite,
            )
            .all()
        )
        return {st.checklist_id: st for st in linhas if st.checklist_id not in exclui}

    def _selecionar_avaliaveis(
        self, estados: dict[str, ChecklistIngestState]
    ) -> tuple[dict[str, ChecklistIngestState], int]:
        """Aplica o teto por rodada; o excedente fica ``pendente`` para a próxima.

        Os campos já foram acumulados no ledger antes do corte, então adiar não
        perde informação — só adia a consulta ao SQL Server.
        """
        teto = self._settings.checklist_ingest_max_checklists
        if len(estados) <= teto:
            return estados, 0
        escolhidos = sorted(estados)[:teto]
        return {cid: estados[cid] for cid in escolhidos}, len(estados) - teto

    def _checklists_com_job(self, checklist_ids: set[str]) -> dict[str, uuid.UUID]:
        """Diff contra ``pipeline_jobs`` — cobre o que foi rodado à mão."""
        if not checklist_ids:
            return {}
        linhas = (
            self._db.query(PipelineJob.checklist_id, PipelineJob.id)
            .filter(PipelineJob.checklist_id.in_(sorted(checklist_ids)))
            .all()
        )
        return {str(checklist_id): job_id for checklist_id, job_id in linhas}

    def _materializar(
        self,
        checklist_id: str,
        estado: ChecklistIngestState,
        linha: SislocChecklist | None,
    ) -> uuid.UUID | None:
        """Cria o ``pipeline_job`` enriquecido e fecha o estado, num SAVEPOINT.

        Job e estado nascem juntos: se a PK de ``checklist_ingest_state`` colidir
        (rodada sobreposta), os dois somem no rollback do savepoint e nenhum job
        duplicado sobra. Devolve ``None`` quando perdeu a corrida.

        O job nasce ``pending`` e **não é despachado aqui** — a execução (e com
        ela o custo de LLM) é do ticket 08.
        """
        job_id = uuid.uuid4()
        try:
            with self._db.begin_nested():
                self._db.add(
                    novo_job_enriquecido(
                        job_id=job_id, checklist_id=checklist_id, linha=linha
                    )
                )
                estado.status = STATUS_MATERIALIZADO
                estado.motivo = None
                estado.job_id = job_id
                self._db.flush()
        except IntegrityError:
            _log.warning("checklist_ingest_dedup_race", checklist_id=checklist_id)
            return None
        _log.info(
            "checklist_job_materializado",
            checklist_id=checklist_id,
            job_id=str(job_id),
            formulario=estado.formulario,
            campos=estado.campos,
            patrimonio=linha.patrimonio if linha else None,
            cliente=linha.projeto_parseado.cliente if linha else None,
            n_linhas=linha.n_linhas if linha else None,
        )
        if linha is not None and linha.n_linhas > 1:
            # Não é ruído: o operador vai ver UM patrimônio de N, e este log é a
            # única trilha de que os outros existiam.
            _log.warning(
                "checklist_multi_ativo",
                checklist_id=checklist_id,
                job_id=str(job_id),
                n_linhas=linha.n_linhas,
                patrimonio_exibido=linha.patrimonio,
                consequencia="a tela mostra o primeiro ativo por `ordem` e avisa",
            )
        return job_id

    def _persistir_cursor(self, cursor_row: IngestCursor | None, novo_cursor: str) -> None:
        """Avança o cursor. Só é chamado quando a rodada foi até o fim."""
        if not novo_cursor:
            return
        if cursor_row is None:
            self._db.add(IngestCursor(name=CURSOR_CHECKLISTS, cursor=novo_cursor))
            return
        cursor_row.cursor = novo_cursor
        cursor_row.updated_at = datetime.now(UTC)
