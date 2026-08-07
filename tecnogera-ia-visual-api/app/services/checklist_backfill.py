"""Backfill sob demanda de **um** checklist antigo — ticket ``mvp-c54-c57/11``.

A esteira agendada (ticket 07) descobre por **delta de cursor**: o cursor do
"agora" é o marco de corte por ativação, e nada anterior a ele entra. Isso é
deliberado — varrer ``/Sisloc`` inteiro custou 67 min na medição do ticket 01.
A consequência é que "quero rodar aquele checklist de junho" não tem caminho.

Este módulo é esse caminho, e **só** ele: entra por ``checklist_id`` explícito,
via ``files_search_v2`` (não via delta), e por construção ignora o marco de
corte — não lê nem escreve ``ingest_cursors`` e não aplica
``CHECKLIST_INGEST_SINCE``. Fora isso o caminho feliz é idêntico ao do cron:
mesmo filtro, mesmo ledger, mesmo ``pipeline_job``.

Decisões deste módulo
---------------------

**Reprocessamento cria execução nova.** ``pipeline_jobs`` não tem unicidade por
``checklist_id`` (só índice), então a segunda passada nasce como uma linha nova
e a anterior fica intacta — que é justamente o motivo de reprocessar: comparar
com o resultado de antes. A PK de ``checklist_ingest_state`` **não** impede
isso: aquela tabela é o livro-razão *da esteira automática* (uma linha por
checklist, "o cron já resolveu este id?"), não o histórico de execuções. O
backfill atualiza a linha existente para apontar para o job mais recente; o
histórico vive em ``pipeline_jobs``.

**Um desfecho terminal do ledger não veta o backfill.** ``descartado`` é a
palavra final para o *cron*; aqui um humano pediu explicitamente. O filtro é
reavaliado do zero, com os dados de hoje.

**Mas o filtro em si não é negociável, nem mesmo aqui.** O recorte
``status = 'Concluído'`` (ticket 17) vale igual: um checklist aberto tem fotos
possivelmente parciais, e pedir o backfill não fecha o checklist no ERP. A
recusa diz isso e diz que **não é preciso fazer nada** — a esteira o pega
sozinha quando alguém conferir. A saída de exceção continua sendo
``POST /pipeline/run``, que não aplica filtro nenhum e é assumidamente manual.

**Teto de lote.** Cada checklist aceito vira 3–4 chamadas de visão quando o
despacho rodar. Aceitar uma lista sem teto transforma um curl distraído em
fatura. O teto é ``CHECKLIST_BACKFILL_MAX_IDS`` (default 20) e é imposto
**aqui**, no serviço, não no router: qualquer chamador futuro (CLI, script de
suporte) herda o freio.

**Este módulo não chama LLM e não despacha nada.** Ele materializa jobs
``pending``, exatamente como o cron; a execução — e com ela o custo de token,
o teto de chamadas por rodada e o orçamento mensal — é do ticket 08. Passar por
lá de propósito: um caminho de backfill que despachasse direto driblaria o
único lugar do sistema que mede gasto real.

**Dropbox somente leitura**: ``files_search_v2``/``files_get_metadata``, nada
mais. **SQL Server somente SELECT**, em uma única consulta em lote para a
requisição inteira.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.models.ingest import (
    STATUS_MATERIALIZADO,
    ChecklistIngestState,
)
from app.models.pipeline import PipelineJob, novo_job_enriquecido
from app.services.checklist_filter import (
    FORMULARIOS_ALVO,
    STATUS_CONCLUIDO,
    MotivoDescarte,
    Veredito,
    avaliar,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from app.models.sisloc import SislocChecklist
    from app.services.dropbox import DropboxService
    from app.services.sisloc import SislocService

_log = get_logger(__name__)

#: Motivo próprio do backfill: o id não existe (ou não tem foto) no Dropbox.
#: Distinto de ``formulario_ausente``, que é o id não existir no ERP.
MOTIVO_SEM_IMAGENS = "sem_imagens"

#: Marca no ledger que a linha foi fechada por backfill manual, não pelo cron.
MOTIVO_BACKFILL = "backfill"

#: Frase por motivo. Um "não qualificou" mudo faz o operador achar que o sistema
#: está quebrado — cada recusa diz o que falta e qual é a saída.
_EXPLICACAO: dict[str, str] = {
    MOTIVO_SEM_IMAGENS: (
        "Nenhuma imagem com este checklist_id foi encontrada no Dropbox. "
        "Confira o número (é o que aparece em '..._checklist_<id>_cNN_...' no "
        "nome do arquivo) e se a filial está sincronizada."
    ),
    MotivoDescarte.FORMULARIO_AUSENTE.value: (
        "Este checklist não existe em dbo.checklist_produto. Não é atraso do "
        "ERP: ~1,1% dos checklists com foto no Dropbox nunca aparecem na view "
        "(medido, 291 de 26.365). Sem a linha não dá para saber o formulário, e "
        "sem formulário o cN não tem significado — a inspeção sairia com "
        "taxonomia de gerador sobre um equipamento possivelmente diferente. "
        "Se ainda assim for para processar, use POST /api/v1/pipeline/run, que "
        "não aplica o filtro."
    ),
    MotivoDescarte.FORMULARIO_VAZIO.value: (
        "A linha existe em dbo.checklist_produto mas a coluna 'formulario' está "
        "vazia — acontece em ~36% do parque. Sem formulário o filtro não pode "
        "ser aplicado."
    ),
    MotivoDescarte.FORMULARIO_FORA_WHITELIST.value: (
        "O formulário deste checklist está fora da whitelist do MVP "
        "({alvo}). Formulário lido: {formulario}. Só gerador entra: F277 é "
        "plataforma elevatória e exigiria outra taxonomia; no F013 os mesmos "
        "códigos cN significam plaqueta e carregador de bateria."
    ),
    MotivoDescarte.STATUS_NAO_CONCLUIDO.value: (
        "O checklist está '{status}' no Sisloc, não '" + STATUS_CONCLUIDO + "'. "
        "Checklist aberto tem fotos possivelmente parciais e data de conclusão "
        "vazia — 14,8% dos F180/F038 estão nesse estado (medido). Analisá-lo "
        "gastaria chave paga sobre evidência incompleta. **Não precisa fazer "
        "nada**: assim que alguém fechar o checklist no ERP, a esteira "
        "automática o pega sozinha na rodada seguinte. Se for urgente, feche o "
        "checklist no Sisloc e repita este backfill."
    ),
    MotivoDescarte.CAMPO_FALTANTE.value: (
        "Formulário {formulario} aceito, mas faltam as vistas obrigatórias: "
        "{faltantes}. Presentes no Dropbox: {presentes}. As três obrigatórias "
        "são c54 (lateral direita), c55 (lateral esquerda) e c56 (frontal, face "
        "do painel de comando); c57 (traseira) é opcional."
    ),
}

#: Rótulo humano de cada vista, para a mensagem de erro fazer sentido a quem
#: nunca viu o dicionário de campos.
_ROTULO_VISTA: dict[str, str] = {
    "c54": "lateral direita",
    "c55": "lateral esquerda",
    "c56": "frontal/painel",
    "c57": "traseira",
}


def _com_rotulo(campos: Sequence[str]) -> str:
    if not campos:
        return "nenhuma"
    return ", ".join(f"{c} ({_ROTULO_VISTA[c]})" if c in _ROTULO_VISTA else c for c in campos)


@dataclass(frozen=True, slots=True)
class BackfillItem:
    """O desfecho de **um** ``checklist_id`` dentro da requisição."""

    checklist_id: str
    aceito: bool
    job_id: uuid.UUID | None = None
    motivo: str | None = None
    detalhe: str = ""
    formulario: str | None = None
    campos: tuple[str, ...] = ()
    campos_faltantes: tuple[str, ...] = ()
    #: Já existia job para este checklist antes desta chamada.
    reprocessamento: bool = False
    #: Ordinal desta execução (1 = primeira vez que o checklist roda).
    tentativa: int = 0
    #: Quantas vistas irão para a IA — uma chamada de visão por vista.
    vistas_para_analise: int = 0
    #: Enriquecimento vindo da mesma consulta que decidiu o filtro.
    patrimonio: str | None = None
    cliente: str | None = None
    #: ``> 1`` ⇒ a view tem várias linhas para este checklist e o operador verá
    #: só a primeira por ``ordem``.
    n_linhas: int | None = None


@dataclass
class BackfillResult:
    """O que a requisição inteira fez. Serializado tal e qual na resposta."""

    itens: list[BackfillItem] = field(default_factory=list)
    duplicados_na_requisicao: int = 0
    teto_por_requisicao: int = 0

    @property
    def solicitados(self) -> int:
        return len(self.itens)

    @property
    def aceitos(self) -> int:
        return sum(1 for i in self.itens if i.aceito)

    @property
    def recusados(self) -> int:
        return self.solicitados - self.aceitos

    @property
    def job_ids(self) -> list[uuid.UUID]:
        return [i.job_id for i in self.itens if i.job_id is not None]

    @property
    def chamadas_visao_estimadas(self) -> int:
        """Uma chamada de visão por vista aceita — o número que vira dinheiro."""
        return sum(i.vistas_para_analise for i in self.itens if i.aceito)

    def como_log(self) -> dict[str, object]:
        return {
            "solicitados": self.solicitados,
            "aceitos": self.aceitos,
            "recusados": self.recusados,
            "duplicados_na_requisicao": self.duplicados_na_requisicao,
            "chamadas_visao_estimadas": self.chamadas_visao_estimadas,
            "motivos": sorted({i.motivo for i in self.itens if i.motivo}),
        }


class ChecklistBackfillService:
    """Reprocessa checklists antigos por id, sob demanda e com teto de lote."""

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

    @property
    def teto(self) -> int:
        return self._settings.checklist_backfill_max_ids

    # ── entrada ──────────────────────────────────────────────────────────────

    def backfill(self, checklist_ids: Sequence[str]) -> BackfillResult:
        """Avalia e materializa cada id. **Ignora o marco de corte.**

        Levanta ``DomainError`` (HTTP 422) com a lista vazia ou acima do teto —
        antes de tocar Dropbox ou SQL Server. As duas integrações levantam
        ``IntegrationError``/``ConfigurationError``, que sobem para o chamador:
        aqui, ao contrário do cron, não existe "próxima rodada" para corrigir em
        silêncio, e quem pediu o backfill precisa saber que ele não aconteceu.
        """
        ids, duplicados = self._normalizar(checklist_ids)
        resultado = BackfillResult(
            duplicados_na_requisicao=duplicados, teto_por_requisicao=self.teto
        )

        # Descoberta por id, não por delta: é isto que ignora o marco de corte.
        campos_por_id = {cid: self._campos_no_dropbox(cid) for cid in ids}

        # SOMENTE SELECT, e uma única consulta em lote para a requisição toda —
        # um round-trip por id atravessando a VPN é o erro clássico aqui. A
        # mesma consulta decide o filtro e enriquece o job.
        com_foto = [cid for cid, campos in campos_por_id.items() if campos]
        linhas = self._sisloc.fetch_checklists(com_foto) if com_foto else {}

        for checklist_id in ids:
            resultado.itens.append(
                self._processar_um(checklist_id, campos_por_id[checklist_id], linhas)
            )

        if resultado.aceitos:
            self._db.commit()
        else:
            self._db.rollback()

        _log.info("checklist_backfill", **resultado.como_log())
        return resultado

    # ── passos ───────────────────────────────────────────────────────────────

    def _normalizar(self, checklist_ids: Sequence[str]) -> tuple[list[str], int]:
        """Limpa, deduplica preservando a ordem e aplica o teto de lote.

        O teto é medido na lista **crua**: o payload em si precisa ser limitado,
        senão 500 ids repetidos passariam pela porta antes de qualquer checagem.
        """
        crus = [c.strip() for c in checklist_ids if c and c.strip()]
        if not crus:
            raise DomainError("informe ao menos um checklist_id")
        if len(crus) > self.teto:
            raise DomainError(
                f"{len(crus)} checklists numa requisição excedem o teto de {self.teto}. "
                "Cada checklist aceito vira 3–4 chamadas de visão quando for despachado; "
                "o teto existe para que um backfill grande seja uma decisão consciente, "
                "tomada em lotes. Divida a lista ou ajuste CHECKLIST_BACKFILL_MAX_IDS.",
                details={"solicitados": len(crus), "teto": self.teto},
            )
        vistos: list[str] = []
        for cid in crus:
            if cid not in vistos:
                vistos.append(cid)
        return vistos, len(crus) - len(vistos)

    def _campos_no_dropbox(self, checklist_id: str) -> set[str]:
        """Vistas presentes hoje no Dropbox para este id. Somente leitura."""
        imagens = self._dropbox.list_checklist_images(checklist_id)
        return {img.parsed.field_name.strip().lower() for img in imagens}

    def _processar_um(
        self,
        checklist_id: str,
        campos: set[str],
        linhas: dict[str, SislocChecklist],
    ) -> BackfillItem:
        anteriores = self._jobs_anteriores(checklist_id)

        if not campos:
            return BackfillItem(
                checklist_id=checklist_id,
                aceito=False,
                motivo=MOTIVO_SEM_IMAGENS,
                detalhe=_EXPLICACAO[MOTIVO_SEM_IMAGENS],
                reprocessamento=bool(anteriores),
            )

        linha = linhas.get(checklist_id)
        formulario = linha.formulario if linha else None
        veredito = avaliar(
            formulario,
            campos,
            status=linha.status if linha else None,
            formularios_alvo=FORMULARIOS_ALVO,
            tem_linha_no_erp=linha is not None,
        )

        if not veredito.aprovado:
            return BackfillItem(
                checklist_id=checklist_id,
                aceito=False,
                motivo=veredito.rotulo,
                detalhe=self._explicar(veredito, campos),
                formulario=formulario,
                campos=tuple(sorted(campos)),
                campos_faltantes=veredito.campos_faltantes,
                reprocessamento=bool(anteriores),
                patrimonio=linha.patrimonio if linha else None,
                cliente=linha.projeto_parseado.cliente if linha else None,
                n_linhas=linha.n_linhas if linha else None,
            )

        job_id = self._materializar(checklist_id, campos, linha)
        _log.info(
            "checklist_backfill_job_criado",
            checklist_id=checklist_id,
            job_id=str(job_id),
            formulario=formulario,
            patrimonio=linha.patrimonio if linha else None,
            n_linhas=linha.n_linhas if linha else None,
            vistas=len(veredito.campos_utilizados),
            tentativa=anteriores + 1,
        )
        return BackfillItem(
            checklist_id=checklist_id,
            aceito=True,
            job_id=job_id,
            detalhe=(
                f"Job criado em 'pending' com {len(veredito.campos_utilizados)} vista(s): "
                f"{_com_rotulo(veredito.campos_utilizados)}. A execução (e o custo de LLM) "
                "acontece no despacho, sob o teto de chamadas e o orçamento mensal."
                + self._aviso_multi_ativo(linha)
            ),
            formulario=formulario,
            campos=tuple(sorted(campos)),
            reprocessamento=anteriores > 0,
            tentativa=anteriores + 1,
            vistas_para_analise=len(veredito.campos_utilizados),
            patrimonio=linha.patrimonio if linha else None,
            cliente=linha.projeto_parseado.cliente if linha else None,
            n_linhas=linha.n_linhas if linha else None,
        )

    @staticmethod
    def _aviso_multi_ativo(linha: SislocChecklist | None) -> str:
        """Avisa quando a view tem mais de uma linha para o mesmo checklist.

        78 dos 321 códigos repetidos em F180/F038 divergem em ``patrimonio``:
        são geradores gêmeos em paralelo (``TECG00466A`` × ``TECG00466B``) e há
        casos de ativos sem relação nenhuma. As fotos no Dropbox são do
        *checklist*, não do patrimônio, então não há como atribuí-las a um ativo
        — o que resta é dizer isso em voz alta em vez de nomear o errado calado.
        """
        if linha is None or linha.n_linhas <= 1:
            return ""
        return (
            f" ⚠️ Este checklist cobre {linha.n_linhas} linhas no Sisloc "
            f"(possivelmente mais de um equipamento); o laudo é atribuído ao "
            f"patrimônio {linha.patrimonio or '(vazio)'}, o primeiro por 'ordem'."
        )

    def _explicar(self, veredito: Veredito, campos: set[str]) -> str:
        modelo = _EXPLICACAO.get(veredito.motivo.value if veredito.motivo else "", "")
        if not modelo:
            return "Checklist não qualificado pelo filtro."
        alvo = ", ".join(sorted(FORMULARIOS_ALVO))
        return modelo.format(
            alvo=alvo,
            formulario=veredito.formulario_codigo or "(desconhecido)",
            faltantes=_com_rotulo(veredito.campos_faltantes),
            presentes=_com_rotulo(sorted(campos)),
            status=veredito.status_bruto or "(vazio)",
        )

    def _jobs_anteriores(self, checklist_id: str) -> int:
        """Quantas execuções este checklist já teve. Define ``tentativa``."""
        return (
            self._db.query(PipelineJob)
            .filter(PipelineJob.checklist_id == checklist_id)
            .count()
        )

    def _materializar(
        self, checklist_id: str, campos: set[str], linha: SislocChecklist | None
    ) -> uuid.UUID:
        """Cria a execução nova enriquecida e sincroniza o ledger da esteira.

        O job novo **não** substitui o anterior: ``pipeline_jobs`` guarda o
        histórico e é onde a comparação entre execuções acontece. O ledger, que
        tem uma linha só por checklist, passa a apontar para o job mais recente
        e fica ``materializado`` — o que também impede o cron de criar um
        terceiro job para o mesmo id na rodada seguinte.

        O snapshot é **desta** execução, não o da anterior: reprocessar é
        justamente comparar o que o ERP dizia antes com o que diz agora.
        """
        formulario = linha.formulario if linha else None
        job_id = uuid.uuid4()
        self._db.add(
            novo_job_enriquecido(job_id=job_id, checklist_id=checklist_id, linha=linha)
        )
        # Flush ANTES do SAVEPOINT: o INSERT do job precisa estar fora dele,
        # senão um conflito na linha do ledger levaria o job junto no rollback.
        self._db.flush()
        agora = datetime.now(UTC)
        estado = self._db.get(ChecklistIngestState, checklist_id)
        if estado is None:
            novo = ChecklistIngestState(
                checklist_id=checklist_id,
                campos=",".join(sorted(campos)),
                formulario=formulario,
                status=STATUS_MATERIALIZADO,
                motivo=MOTIVO_BACKFILL,
                job_id=job_id,
                first_seen_at=agora,
                last_seen_at=agora,
            )
            try:
                # SAVEPOINT: uma rodada do cron pode ter inserido a mesma linha
                # entre o `get` e o `flush`. Só a linha do ledger é desfeita —
                # o job novo permanece, que é o que o operador pediu.
                with self._db.begin_nested():
                    self._db.add(novo)
                    self._db.flush()
            except IntegrityError:
                _log.warning("checklist_backfill_ledger_race", checklist_id=checklist_id)
                estado = self._db.get(ChecklistIngestState, checklist_id)
            else:
                return job_id
        if estado is not None:
            estado.campos = ",".join(sorted(estado.campos_set | campos))
            estado.formulario = formulario or estado.formulario
            estado.status = STATUS_MATERIALIZADO
            estado.motivo = MOTIVO_BACKFILL
            estado.job_id = job_id
            estado.last_seen_at = agora
        return job_id
