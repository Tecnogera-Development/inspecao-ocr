"""Backfill de checklists antigos — ticket ``mvp-c54-c57/11``.

POST /api/v1/checklists/backfill — reprocessa checklists por ``checklist_id``,
ignorando o marco de corte da esteira agendada.

**Por que endpoint e não CLI.** Três razões, nesta ordem:

1. Quem precisa disso não tem shell. O backfill é pedido por quem opera o
   portal ("aquele checklist de junho não apareceu"); a alternativa seria
   alguém com acesso à VM `tng-brsdtcapp01` rodar `docker compose exec` — o que
   na prática vira "manda mensagem para o dev".
2. `X-API-Key` é o guarda-corpo que o ticket pede, e ele só existe no HTTP. Um
   comando de CLI é autenticado por quem já entrou no host, o que é uma
   fronteira mais grosseira do que a que o resto dos endpoints operacionais usa.
3. O `app.cli` roda num processo sem o pool do Arq e sem o ciclo de vida da
   aplicação. Nada aqui despacha job (isso é do ticket 08), mas o dia em que
   despachar, o endpoint já está no lugar certo.

Não há um segundo caminho de CLI de propósito: uma superfície a menos para
auditar, e nenhum risco de um script driblar o teto de lote.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.routers.pipeline import verify_api_key
from app.services.checklist_backfill import BackfillResult, ChecklistBackfillService
from app.services.dropbox import DropboxService
from app.services.sisloc import SislocService

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/checklists", tags=["checklists"])
_log = get_logger(__name__)

_CHECKLIST_ID_RE = re.compile(r"^\d+$")

_AVISO = (
    "Jobs criados em status 'pending'. A execução — e com ela o custo de LLM — "
    "acontece no despacho da análise, sob o teto de chamadas por rodada e o "
    "orçamento mensal. Nada foi enviado a nenhum modelo por esta chamada."
)


class BackfillRequest(BaseModel):
    """Lista explícita de ids. Sem intervalo de datas, por decisão de escopo."""

    checklist_ids: list[str] = Field(min_length=1)

    @field_validator("checklist_ids")
    @classmethod
    def _validar_ids(cls, v: list[str]) -> list[str]:
        limpos = [c.strip() for c in v if c and c.strip()]
        if not limpos:
            raise ValueError("checklist_ids não pode ser vazio")
        invalidos = [c for c in limpos if not _CHECKLIST_ID_RE.match(c)]
        if invalidos:
            # `codigo_checklist` é int na view: um id não numérico seria
            # descartado em silêncio pelo Sisloc e voltaria como "não existe no
            # ERP", que é a mensagem errada para um erro de digitação.
            raise ValueError(
                "checklist_id deve conter apenas dígitos (formato Sisloc); "
                f"inválidos: {invalidos}"
            )
        return limpos


class BackfillItemResponse(BaseModel):
    checklist_id: str
    aceito: bool
    job_id: str | None = None
    motivo: str | None = None
    detalhe: str = ""
    formulario: str | None = None
    campos: list[str] = Field(default_factory=list)
    campos_faltantes: list[str] = Field(default_factory=list)
    reprocessamento: bool = False
    tentativa: int = 0
    #: Enriquecimento vindo da mesma consulta que decidiu o filtro (ticket 17).
    patrimonio: str | None = None
    cliente: str | None = None
    #: ``> 1`` ⇒ a view tem N linhas para este checklist e o laudo é atribuído
    #: ao primeiro ativo por ``ordem``. Ver ``detalhe``.
    n_linhas: int | None = None


class BackfillResponse(BaseModel):
    solicitados: int
    aceitos: int
    recusados: int
    duplicados_na_requisicao: int
    teto_por_requisicao: int
    chamadas_visao_estimadas: int
    job_ids: list[str]
    itens: list[BackfillItemResponse]
    aviso: str = _AVISO


def _serializar(resultado: BackfillResult) -> BackfillResponse:
    return BackfillResponse(
        solicitados=resultado.solicitados,
        aceitos=resultado.aceitos,
        recusados=resultado.recusados,
        duplicados_na_requisicao=resultado.duplicados_na_requisicao,
        teto_por_requisicao=resultado.teto_por_requisicao,
        chamadas_visao_estimadas=resultado.chamadas_visao_estimadas,
        job_ids=[str(j) for j in resultado.job_ids],
        itens=[
            BackfillItemResponse(
                checklist_id=item.checklist_id,
                aceito=item.aceito,
                job_id=str(item.job_id) if item.job_id else None,
                motivo=item.motivo,
                detalhe=item.detalhe,
                formulario=item.formulario,
                campos=list(item.campos),
                campos_faltantes=list(item.campos_faltantes),
                reprocessamento=item.reprocessamento,
                tentativa=item.tentativa,
                patrimonio=item.patrimonio,
                cliente=item.cliente,
                n_linhas=item.n_linhas,
            )
            for item in resultado.itens
        ],
    )


@router.post("/backfill", status_code=202, response_model=BackfillResponse)
def backfill_checklists(
    body: BackfillRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
    settings: Settings = Depends(get_settings),
) -> BackfillResponse:
    """Reprocessa checklists antigos por id, **ignorando o marco de corte**.

    Devolve **202** com o desfecho de cada id quando ao menos um virou job, e
    **422** com o mesmo detalhamento quando nenhum qualificou — para que um id
    recusado não pareça sucesso silencioso na automação de quem chama.

    Um id que já rodou antes gera uma **execução nova**: o job anterior fica
    intacto em ``pipeline_jobs`` e ``tentativa`` diz qual passada é esta.
    """
    servico = ChecklistBackfillService(
        db=db,
        dropbox=DropboxService(settings),
        sisloc=SislocService(settings),
        settings=settings,
    )
    resultado = servico.backfill(body.checklist_ids)
    resposta = _serializar(resultado)

    _log.info("checklist_backfill_endpoint", **resultado.como_log())

    if resultado.aceitos == 0:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "nenhum checklist qualificou para reprocessamento",
                **resposta.model_dump(),
            },
        )
    return resposta
