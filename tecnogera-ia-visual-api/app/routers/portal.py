"""Router do portal admin — autenticação, CSRF, jobs, stats, run, thumbs, avarias — IAVS-031..036, IAVS-068."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid as _uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ratelimit import (
    check_login_rate_limit,
    record_login_failure,
    record_login_success,
)
from app.db.session import get_db
from app.models.pipeline import JobCreatedResponse, JobDetailResponse, PipelineJob
from app.routers.pipeline import RunRequest, _run_pipeline_async
from app.services.auth import authenticate
from app.services.portal_query import (
    ClassificationItem,
    JobFilters,
    JobResult,
    compute_stats,
    get_job_result,
    list_jobs,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.models.user import User
    from app.services.checklist_query import ChecklistDetalhe, ChecklistFiltros

router = APIRouter(prefix="/api/v1/portal", tags=["portal"])
_log = get_logger(__name__)


# ── schemas ───────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    role: str


class CsrfResponse(BaseModel):
    token: str


class StatsResponse(BaseModel):
    total_done: int
    in_progress: int
    failed: int
    total_cost_usd: float
    accuracy_last_week: float | None


class ClassificationItemResponse(BaseModel):
    photo_id: str
    field_name: str | None
    confidence: float
    status: str
    label_display: str
    second_best_field: str | None = None
    second_best_confidence: float | None = None


class JobResultResponse(BaseModel):
    job_id: str
    checklist_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    estimated_cost_usd: float | None
    result_pdf_path: str | None
    error: str | None
    classifications: list[ClassificationItemResponse]
    inconclusivas: list[ClassificationItemResponse]


# ── dependencies ──────────────────────────────────────────────────────────────


def current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Não autenticado")
    from app.models.user import User as UserModel

    user = db.get(UserModel, UUID(user_id))
    if user is None or not user.is_active or user.password_hash is None:
        # password_hash nulo: usuário nunca definiu senha (não deveria ter
        # sessão — authenticate() já recusa login neste estado, ver
        # app/services/auth.py) OU um admin resetou a senha dele
        # (app/routers/usuarios.py::resetar_senha), que zera password_hash de
        # propósito para derrubar qualquer sessão em curso — mesma
        # revalidação a cada request que já existia para is_active, estendida
        # por este campo (ticket usuarios-portal/02, decisão sobre reset).
        raise HTTPException(status_code=401, detail="Sessão inválida")
    return user


def verify_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> None:
    expected = request.session.get("csrf_token")
    if not expected or x_csrf_token != expected:
        raise HTTPException(status_code=403, detail="CSRF token inválido ou ausente")


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.post("/login", response_model=UserResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    _rate_limit: None = Depends(check_login_rate_limit),
) -> UserResponse:
    user = authenticate(db, body.email, body.password)
    if user is None:
        # Falha soma nas duas dimensoes (identidade + origem) so aqui, depois
        # de confirmado que a credencial esta errada -- um sucesso nunca passa
        # por este caminho, entao login legitimo repetido nao esbarra no
        # limite (ticket usuarios-portal/03, ver app/core/ratelimit.py).
        record_login_failure(request, body.email)
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    record_login_success(request, body.email)

    user.last_login_at = datetime.now(UTC)
    db.commit()

    request.session["user_id"] = str(user.id)
    request.session["csrf_token"] = secrets.token_hex(32)

    _log.info("portal_login", user_id=str(user.id))
    return UserResponse(id=user.id, email=user.email, role=user.role)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    _: None = Depends(verify_csrf),
    __: User = Depends(current_user),
) -> Response:
    request.session.clear()
    _log.info("portal_logout")
    return Response(status_code=204)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(current_user)) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, role=user.role)


@router.get("/csrf", response_model=CsrfResponse)
def csrf(
    request: Request,
    _: User = Depends(current_user),
) -> CsrfResponse:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return CsrfResponse(token=token)


@router.get("/jobs", response_model=list[JobDetailResponse])
def portal_list_jobs(
    response: Response,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    if_none_match: str | None = Header(default=None),
) -> Response | list[JobDetailResponse]:
    status_filter = [s.strip() for s in status.split(",")] if status else []
    filters = JobFilters(
        limit=limit,
        offset=offset,
        status_filter=status_filter,
        date_from=date_from,
        date_to=date_to,
    )
    jobs, etag = list_jobs(db, filters)

    if if_none_match == etag:
        return Response(status_code=304)

    response.headers["ETag"] = etag
    return [JobDetailResponse.model_validate(j) for j in jobs]


@router.get("/stats", response_model=StatsResponse)
def portal_stats(
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    month: str | None = Query(default=None),
) -> StatsResponse:
    effective_month = month or datetime.now(UTC).strftime("%Y-%m")
    stats = compute_stats(db, effective_month)
    return StatsResponse(
        total_done=stats.total_done,
        in_progress=stats.in_progress,
        failed=stats.failed,
        total_cost_usd=stats.total_cost_usd,
        accuracy_last_week=stats.accuracy_last_week,
    )


@router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
def portal_job_result(
    job_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    if_none_match: str | None = Header(default=None),
) -> Response | JobResultResponse:
    result = get_job_result(db, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    if if_none_match == result.etag:
        return Response(status_code=304)

    response.headers["ETag"] = result.etag
    return JobResultResponse(
        job_id=result.job_id,
        checklist_id=result.checklist_id,
        status=result.status,
        started_at=result.started_at,
        finished_at=result.finished_at,
        estimated_cost_usd=result.estimated_cost_usd,
        result_pdf_path=result.result_pdf_path,
        error=result.error,
        classifications=[
            ClassificationItemResponse(
                photo_id=c.photo_id,
                field_name=c.field_name,
                confidence=c.confidence,
                status=c.status,
                label_display=c.label_display,
                second_best_field=c.second_best_field,
                second_best_confidence=c.second_best_confidence,
            )
            for c in result.classifications
        ],
        inconclusivas=[
            ClassificationItemResponse(
                photo_id=c.photo_id,
                field_name=c.field_name,
                confidence=c.confidence,
                status=c.status,
                label_display=c.label_display,
                second_best_field=c.second_best_field,
                second_best_confidence=c.second_best_confidence,
            )
            for c in result.inconclusivas
        ],
    )


@router.get("/jobs/{job_id}/pdf")
def portal_job_pdf(
    job_id: UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> FastAPIResponse:
    """Stream do PDF gerado para um job, baixando do Dropbox sob demanda."""
    from app.services.dropbox import DropboxService, ResourceNotFoundError

    job = db.get(PipelineJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    if job.status != "done" or not job.result_pdf_path:
        raise HTTPException(status_code=404, detail="PDF não disponível para este job")

    try:
        pdf_bytes = DropboxService(settings).download_image(job.result_pdf_path)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PDF não encontrado no Dropbox") from exc

    filename = Path(job.result_pdf_path).name
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _get_thumb_dirs(
    settings: Settings = Depends(get_settings),
) -> tuple[Path, Path]:
    """Retorna (work_dir, cache_dir) para o thumb cache."""
    work_dir = Path(settings.dropbox_local_cache_dir)
    cache_dir = Path("data/cache/thumbs")
    return work_dir, cache_dir


@router.get("/photos/{photo_id:path}/thumb")
def portal_thumb(
    photo_id: str,
    w: int = Query(default=240),
    _user: User = Depends(current_user),
    thumb_dirs: tuple[Path, Path] = Depends(_get_thumb_dirs),
) -> FastAPIResponse:
    from app.services.thumb_cache import ALLOWED_WIDTHS, get_thumb

    if w not in ALLOWED_WIDTHS:
        raise HTTPException(status_code=422, detail=f"w={w} inválido; use um de {sorted(ALLOWED_WIDTHS)}")

    work_dir, cache_dir = thumb_dirs
    try:
        data = get_thumb(photo_id, width=w, work_dir=work_dir, cache_dir=cache_dir)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Foto não encontrada")

    cache_path = cache_dir / f"{photo_id}_{w}.jpg"
    mtime = int(cache_path.stat().st_mtime) if cache_path.exists() else 0
    etag = hashlib.md5(f"{photo_id}:{w}:{mtime}".encode()).hexdigest()  # noqa: S324

    return FastAPIResponse(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=86400",
            "ETag": etag,
        },
    )


@router.post("/run", status_code=202, response_model=JobCreatedResponse)
def portal_run(
    body: RunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    _csrf: None = Depends(verify_csrf),
    settings: Settings = Depends(get_settings),
) -> JobCreatedResponse:
    job_id = _uuid.uuid4()
    job = PipelineJob(id=job_id, checklist_id=body.checklist_id, status="pending", mode="sync")
    db.add(job)
    db.commit()

    # NÃO passar `db` (Session da request) para a background task: ela fecha ao
    # fim do request. `_run_pipeline_async` abre a própria Session (IAVS-067).
    background_tasks.add_task(_run_pipeline_async, job_id, body.checklist_id, settings)

    _log.info("portal_run_job_created", job_id=str(job_id), checklist_id=body.checklist_id)
    return JobCreatedResponse(job_id=str(job_id), status="pending")


# ── mvp-c54-c57/09: tela de checklists (BFF read-only) ───────────────────────


class ChecklistItemResponse(BaseModel):
    """Uma linha da lista de checklists."""

    job_id: UUID
    checklist_id: str
    status: str
    #: ``conforme`` | ``nao_conforme`` | ``nao_processavel`` | ``sem_analise``.
    #: Os três primeiros são o veredito; o quarto é ausência dele.
    indicador: str
    indicador_rotulo: str
    #: 1 = crítica … 4 = baixa. ``None`` quando não há achado.
    severidade: int | None = None
    severidade_rotulo: str | None = None
    vista_determinante: str | None = None
    vista_determinante_rotulo: str | None = None
    #: Dimensão ORTOGONAL ao indicador. Constante ``pendente`` até o ticket 10.
    validacao: str
    patrimonio: str | None = None
    cliente: str | None = None
    filial: str | None = None
    formulario: str | None = None
    formulario_codigo: str | None = None
    #: Data de conclusão no Sisloc — é a "DATA" da lista.
    data: datetime | None = None
    criado_em: datetime
    n_linhas: int | None = None
    multi_ativo: bool
    vistas_recebidas: list[str]
    vistas_esperadas: list[str]
    vistas_ausentes: list[str]


class ChecklistCountersResponse(BaseModel):
    total: int
    nao_conformes: int
    nao_processaveis: int
    conformes: int
    sem_analise: int
    a_validar: int


class ChecklistFacetsResponse(BaseModel):
    filiais: list[str]
    formularios: list[str]


class ChecklistListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    contadores: ChecklistCountersResponse
    facetas: ChecklistFacetsResponse
    itens: list[ChecklistItemResponse]


class ChecklistViewValidationResponse(BaseModel):
    """Julgamento humano de UMA vista — ticket ``mvp-c54-c57/10``.

    ``null`` no lugar deste bloco = vista pendente. ``tipo_erro`` nulo com o
    bloco presente = confirmada; preenchido = o operador disse **o que** estava
    errado, que é o insumo de calibragem do prompt.
    """

    estado: str
    tipo_erro: str | None = None
    tipo_erro_rotulo: str | None = None
    classe: str | None = None
    classe_rotulo: str | None = None
    severidade: int | None = None
    severidade_rotulo: str | None = None
    observacao: str | None = None
    por: str | None = None
    em: datetime | None = None


class ChecklistViewResponse(BaseModel):
    """Uma moldura do grid — recebida ou apenas esperada."""

    campo: str
    rotulo: str
    esperada: bool
    recebida: bool
    status: str | None = None
    indicador: str | None = None
    indicador_rotulo: str | None = None
    motivo_nao_processavel: str | None = None
    motivo_rotulo: str | None = None
    classe: str | None = None
    classe_rotulo: str | None = None
    tipo_defeito: str | None = None
    tipo_defeito_rotulo: str | None = None
    severidade: int | None = None
    severidade_rotulo: str | None = None
    confianca: float | None = None
    observacao: str | None = None
    local: str | None = None
    conteudo_observado: str | None = None
    vista_confere: bool | None = None
    foto_path: str | None = None
    #: URL pronta do proxy autenticado (``/avarias/image``), já com o path escapado.
    foto_url: str | None = None
    achados: list[dict[str, Any]] = Field(default_factory=list)
    erro: str | None = None
    determinante: bool = False
    #: ``False`` = vista sem veredito comparável (falhou, não despachada). Não
    #: há julgamento a contestar, logo o botão "Corrigir" não se aplica.
    corrigivel: bool = False
    validacao: ChecklistViewValidationResponse | None = None


class ValidationOptionResponse(BaseModel):
    valor: str
    rotulo: str


class ChecklistValidationOptionsResponse(BaseModel):
    """Listas do formulário de correção — o front não monta enum próprio."""

    tipos_erro: list[ValidationOptionResponse]
    classes: list[ValidationOptionResponse]
    severidades: list[ValidationOptionResponse]


class ChecklistEquipmentResponse(BaseModel):
    codigo_checklist: str
    patrimonio: str | None = None
    cliente: str | None = None
    contrato: str | None = None
    projeto_bruto: str | None = None
    projeto_padrao_reconhecido: bool = False
    filial: str | None = None
    formulario: str | None = None
    formulario_codigo: str | None = None
    data_conclusao: datetime | None = None
    responsavel: str | None = None
    numero_om: int | None = None
    origem: str | None = None
    status_sisloc: str | None = None
    n_linhas: int | None = None
    multi_ativo: bool = False
    #: Preenchido só quando ``n_linhas > 1`` — o checklist cobre mais de um ativo.
    aviso: str | None = None
    lido_em: datetime | None = None


class ChecklistDetailResponse(BaseModel):
    job_id: UUID
    checklist_id: str
    status: str
    indicador: str
    indicador_rotulo: str
    severidade: int | None = None
    severidade_rotulo: str | None = None
    confianca: float | None = None
    vista_determinante: str | None = None
    vista_determinante_rotulo: str | None = None
    validacao: str
    validado_por: str | None = None
    validado_em: datetime | None = None
    #: ``False`` quando nenhuma vista produziu veredito — não há o que confirmar.
    validavel: bool = False
    opcoes_validacao: ChecklistValidationOptionsResponse
    criado_em: datetime
    iniciado_em: datetime | None = None
    finalizado_em: datetime | None = None
    erro: str | None = None
    equipamento: ChecklistEquipmentResponse
    vistas: list[ChecklistViewResponse]
    vistas_esperadas: list[str]
    vistas_recebidas: list[str]
    vistas_ausentes: list[str]
    #: Explica um grid de 3 molduras. ``None`` quando as 4 são esperadas.
    nota_vistas: str | None = None
    achados: list[dict[str, Any]] = Field(default_factory=list)
    custo_usd: float = 0.0
    chamadas_llm: int = 0


def _montar_filtros_checklist(
    *,
    indicador: str | None,
    validacao: str | None,
    filial: str | None,
    formulario: str | None,
    codigo_checklist: str | None,
    data_de: date | None,
    data_ate: date | None,
    ordenar: str,
    limit: int,
    offset: int,
) -> ChecklistFiltros:
    """Valida e monta os filtros compartilhados por lista e export.

    Mesma validação, mesmo 422 nos dois endpoints — só ``limit``/``offset``
    divergem (o export ignora paginação, ver ``checklist_export``).
    """
    from app.services import checklist_query as cq  # noqa: PLC0415

    if ordenar not in cq.ORDENACOES:
        raise HTTPException(
            status_code=422, detail=f"ordenar deve ser um de: {list(cq.ORDENACOES)}"
        )
    if validacao is not None and validacao not in cq.VALIDACOES:
        raise HTTPException(
            status_code=422, detail=f"validacao deve ser um de: {list(cq.VALIDACOES)}"
        )

    valores = tuple(v.strip() for v in indicador.split(",") if v.strip()) if indicador else ()
    permitidos = (*cq.INDICADORES, cq.SEM_ANALISE)
    invalidos = [v for v in valores if v not in permitidos]
    if invalidos:
        raise HTTPException(
            status_code=422,
            detail=f"indicador inválido: {invalidos}; use um de {list(permitidos)}",
        )

    return cq.ChecklistFiltros(
        limit=limit,
        offset=offset,
        indicador=valores,
        validacao=validacao,
        filial=filial,
        formulario=formulario,
        codigo_checklist=codigo_checklist,
        data_de=data_de,
        data_ate=data_ate,
        ordenar=ordenar,
    )


@router.get("/checklists", response_model=ChecklistListResponse)
def portal_list_checklists(
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    indicador: str | None = Query(
        default=None,
        description="CSV: conforme, nao_conforme, nao_processavel, sem_analise",
    ),
    validacao: str | None = Query(default=None, description="pendente | confirmado | corrigido"),
    filial: str | None = Query(default=None),
    formulario: str | None = Query(default=None, description="Código F0NN ou trecho do texto"),
    codigo_checklist: str | None = Query(default=None, description="codigo_checklist do Sisloc"),
    data_de: date | None = Query(default=None, description="Data de conclusão — início"),
    data_ate: date | None = Query(default=None, description="Data de conclusão — fim (inclusivo)"),
    ordenar: str = Query(default="severidade", description="severidade (padrão) | recente"),
) -> ChecklistListResponse:
    """Fila de trabalho do operador — ticket ``mvp-c54-c57/09``.

    Ordem padrão: pior indicador primeiro, severidade mais crítica dentro dele.
    O default da tela é o trabalho a fazer, não o histórico — se a validação
    humana não acontecer, o F1 do contrato fica sem fonte de dados.
    """
    from app.services import checklist_query as cq  # noqa: PLC0415

    filtros = _montar_filtros_checklist(
        indicador=indicador,
        validacao=validacao,
        filial=filial,
        formulario=formulario,
        codigo_checklist=codigo_checklist,
        data_de=data_de,
        data_ate=data_ate,
        ordenar=ordenar,
        limit=limit,
        offset=offset,
    )
    pagina = cq.listar_checklists(db, filtros)

    return ChecklistListResponse(
        total=pagina.total,
        limit=pagina.limit,
        offset=pagina.offset,
        contadores=ChecklistCountersResponse(
            total=pagina.contadores.total,
            nao_conformes=pagina.contadores.nao_conformes,
            nao_processaveis=pagina.contadores.nao_processaveis,
            conformes=pagina.contadores.conformes,
            sem_analise=pagina.contadores.sem_analise,
            a_validar=pagina.contadores.a_validar,
        ),
        facetas=ChecklistFacetsResponse(
            filiais=list(pagina.facetas.filiais),
            formularios=list(pagina.facetas.formularios),
        ),
        itens=[
            ChecklistItemResponse(
                job_id=item.job_id,
                checklist_id=item.checklist_id,
                status=item.status,
                indicador=item.indicador,
                indicador_rotulo=item.indicador_rotulo,
                severidade=item.severidade,
                severidade_rotulo=item.severidade_rotulo,
                vista_determinante=item.vista_determinante,
                vista_determinante_rotulo=item.vista_determinante_rotulo,
                validacao=item.validacao,
                patrimonio=item.patrimonio,
                cliente=item.cliente,
                filial=item.filial,
                formulario=item.formulario,
                formulario_codigo=item.formulario_codigo,
                data=item.data,
                criado_em=item.criado_em,
                n_linhas=item.n_linhas,
                multi_ativo=item.multi_ativo,
                vistas_recebidas=list(item.vistas_recebidas),
                vistas_esperadas=list(item.vistas_esperadas),
                vistas_ausentes=list(item.vistas_ausentes),
            )
            for item in pagina.itens
        ],
    )


def _opcoes_para_response(opcoes: Any) -> ChecklistValidationOptionsResponse:  # noqa: ANN401
    return ChecklistValidationOptionsResponse(
        tipos_erro=[
            ValidationOptionResponse(valor=o.valor, rotulo=o.rotulo) for o in opcoes.tipos_erro
        ],
        classes=[
            ValidationOptionResponse(valor=o.valor, rotulo=o.rotulo) for o in opcoes.classes
        ],
        severidades=[
            ValidationOptionResponse(valor=o.valor, rotulo=o.rotulo) for o in opcoes.severidades
        ],
    )


class ChecklistEvalResponse(BaseModel):
    """P/R/F1 sobre os checklists validados — a métrica de aceite do contrato."""

    #: Relatório do ``DamageEvaluator`` (mesmo formato de ``GET /events/eval``).
    relatorio: dict[str, Any]
    checklists_validados: int
    vistas_validadas: int
    #: Quantas correções de cada tipo — leitura de calibragem do prompt.
    por_tipo_erro: dict[str, int] = Field(default_factory=dict)


# ⚠️ ORDEM IMPORTA: esta rota precisa vir ANTES de `/checklists/{identificador}`,
# senão o path param — que aceita `codigo_checklist`, não só UUID — engole
# "eval" e o endpoint some. Há teste travando isso.
@router.get("/checklists/eval", response_model=ChecklistEvalResponse)
def portal_checklist_eval(
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> ChecklistEvalResponse:
    """P/R/F1 por classe a partir da validação humana — ticket ``mvp-c54-c57/10``.

    Sem gabarito não há métrica: devolve **422**, e não zeros. Um F1 de 0.0
    apresentado como resultado seria indistinguível de um modelo péssimo.
    """
    from app.services import checklist_validation as cv  # noqa: PLC0415

    resultado = cv.avaliar(db)
    if resultado.vistas_validadas == 0:
        raise HTTPException(
            status_code=422,
            detail="Nenhum checklist validado ainda — não há gabarito para medir",
        )
    return ChecklistEvalResponse(
        relatorio=resultado.relatorio.model_dump(mode="json"),
        checklists_validados=resultado.checklists_validados,
        vistas_validadas=resultado.vistas_validadas,
        por_tipo_erro=resultado.por_tipo_erro,
    )


_MEDIA_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ⚠️ ORDEM IMPORTA: mesma razão da rota `eval` acima — precisa vir ANTES de
# `/checklists/{identificador}`, senão "export.xlsx" é engolido pelo path
# param.
@router.get("/checklists/export.xlsx")
def portal_checklists_export_xlsx(
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    indicador: str | None = Query(
        default=None,
        description="CSV: conforme, nao_conforme, nao_processavel, sem_analise",
    ),
    validacao: str | None = Query(default=None, description="pendente | confirmado | corrigido"),
    filial: str | None = Query(default=None),
    formulario: str | None = Query(default=None, description="Código F0NN ou trecho do texto"),
    codigo_checklist: str | None = Query(default=None, description="codigo_checklist do Sisloc"),
    data_de: date | None = Query(default=None, description="Data de conclusão — início"),
    data_ate: date | None = Query(default=None, description="Data de conclusão — fim (inclusivo)"),
    ordenar: str = Query(default="severidade", description="severidade (padrão) | recente"),
) -> FastAPIResponse:
    """``.xlsx`` da lista — ticket ``v1-entregavel/06``.

    Mesmos filtros da lista, **sem** ``limit``/``offset``: exporta todo o
    conjunto filtrado, não a página que a tela mostra. Mesma sessão, mesmo
    401, mesmo 422 de valor fora do enum — é ``GET``, sem CSRF, igual à
    lista.
    """
    from app.services import checklist_export as ce  # noqa: PLC0415

    filtros = _montar_filtros_checklist(
        indicador=indicador,
        validacao=validacao,
        filial=filial,
        formulario=formulario,
        codigo_checklist=codigo_checklist,
        data_de=data_de,
        data_ate=data_ate,
        ordenar=ordenar,
        limit=1,  # substituído por `checklist_export.gerar_planilha`
        offset=0,
    )
    planilha = ce.gerar_planilha(db, filtros)
    return FastAPIResponse(
        content=planilha.getvalue(),
        media_type=_MEDIA_TYPE_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{ce.nome_arquivo()}"'},
    )


def _montar_detalhe_response(detalhe: ChecklistDetalhe) -> ChecklistDetailResponse:
    """Serializa ``ChecklistDetalhe`` -> ``ChecklistDetailResponse``.

    ÚNICO lugar que monta esta resposta — usado pela rota de detalhe (JSON) e
    pela rota de PDF (``/checklists/{identificador}/pdf``). O laudo em PDF
    parte deste MESMO ``model_dump()``, nunca de uma segunda consulta: se o
    JSON e o PDF discordassem sobre o veredito, o bug seria pior que qualquer
    problema de layout.
    """
    eq = detalhe.equipamento
    return ChecklistDetailResponse(
        job_id=detalhe.job_id,
        checklist_id=detalhe.checklist_id,
        status=detalhe.status,
        indicador=detalhe.indicador,
        indicador_rotulo=detalhe.indicador_rotulo,
        severidade=detalhe.severidade,
        severidade_rotulo=detalhe.severidade_rotulo,
        confianca=detalhe.confianca,
        vista_determinante=detalhe.vista_determinante,
        vista_determinante_rotulo=detalhe.vista_determinante_rotulo,
        validacao=detalhe.validacao,
        validado_por=detalhe.validado_por,
        validado_em=detalhe.validado_em,
        validavel=detalhe.validavel,
        opcoes_validacao=_opcoes_para_response(detalhe.opcoes_validacao),
        criado_em=detalhe.criado_em,
        iniciado_em=detalhe.iniciado_em,
        finalizado_em=detalhe.finalizado_em,
        erro=detalhe.erro,
        equipamento=ChecklistEquipmentResponse(
            codigo_checklist=eq.codigo_checklist,
            patrimonio=eq.patrimonio,
            cliente=eq.cliente,
            contrato=eq.contrato,
            projeto_bruto=eq.projeto_bruto,
            projeto_padrao_reconhecido=eq.projeto_padrao_reconhecido,
            filial=eq.filial,
            formulario=eq.formulario,
            formulario_codigo=eq.formulario_codigo,
            data_conclusao=eq.data_conclusao,
            responsavel=eq.responsavel,
            numero_om=eq.numero_om,
            origem=eq.origem,
            status_sisloc=eq.status_sisloc,
            n_linhas=eq.n_linhas,
            multi_ativo=eq.multi_ativo,
            aviso=eq.aviso,
            lido_em=eq.lido_em,
        ),
        vistas=[
            ChecklistViewResponse(
                campo=v.campo,
                rotulo=v.rotulo,
                esperada=v.esperada,
                recebida=v.recebida,
                status=v.status,
                indicador=v.indicador,
                indicador_rotulo=v.indicador_rotulo,
                motivo_nao_processavel=v.motivo_nao_processavel,
                motivo_rotulo=v.motivo_rotulo,
                classe=v.classe,
                classe_rotulo=v.classe_rotulo,
                tipo_defeito=v.tipo_defeito,
                tipo_defeito_rotulo=v.tipo_defeito_rotulo,
                severidade=v.severidade,
                severidade_rotulo=v.severidade_rotulo,
                confianca=v.confianca,
                observacao=v.observacao,
                local=v.local,
                conteudo_observado=v.conteudo_observado,
                vista_confere=v.vista_confere,
                foto_path=v.foto_path,
                foto_url=v.foto_url,
                achados=list(v.achados),
                erro=v.erro,
                determinante=v.determinante,
                corrigivel=v.corrigivel,
                validacao=(
                    ChecklistViewValidationResponse(
                        estado=v.validacao.estado,
                        tipo_erro=v.validacao.tipo_erro,
                        tipo_erro_rotulo=v.validacao.tipo_erro_rotulo,
                        classe=v.validacao.classe,
                        classe_rotulo=v.validacao.classe_rotulo,
                        severidade=v.validacao.severidade,
                        severidade_rotulo=v.validacao.severidade_rotulo,
                        observacao=v.validacao.observacao,
                        por=v.validacao.por,
                        em=v.validacao.em,
                    )
                    if v.validacao is not None
                    else None
                ),
            )
            for v in detalhe.vistas
        ],
        vistas_esperadas=list(detalhe.vistas_esperadas),
        vistas_recebidas=list(detalhe.vistas_recebidas),
        vistas_ausentes=list(detalhe.vistas_ausentes),
        nota_vistas=detalhe.nota_vistas,
        achados=list(detalhe.achados),
        custo_usd=detalhe.custo_usd,
        chamadas_llm=detalhe.chamadas_llm,
    )


@router.get("/checklists/{identificador}", response_model=ChecklistDetailResponse)
def portal_checklist_detail(
    identificador: str,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> ChecklistDetailResponse:
    """Relatório de um checklist — aceita ``job_id`` (UUID) ou ``codigo_checklist``."""
    from app.services import checklist_query as cq  # noqa: PLC0415

    detalhe = cq.obter_checklist(db, identificador)
    if detalhe is None:
        raise HTTPException(status_code=404, detail="Checklist não encontrado")
    return _montar_detalhe_response(detalhe)


# ⚠️ Path de DOIS segmentos (`{identificador}/pdf`): não conflita com a rota
# de UM segmento acima (`/checklists/{identificador}`) nem com `eval` /
# `export.xlsx`, que também são de um segmento só — o cuidado de ordem que
# aquelas duas precisaram (comentário acima, linha ~716) não se aplica aqui
# porque a forma do path já desambigua. Mesmo assim a rota fica DEPOIS do
# detalhe, agrupada com `confirmar`/`corrigir`: mesmo padrão de leitura.
@router.get("/checklists/{identificador}/pdf")
def portal_checklist_pdf(
    identificador: str,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> FastAPIResponse:
    """Laudo em PDF — ticket ``v1-entregavel/05``. Mesma sessão, mesmo 404. ``GET``, sem CSRF.

    As fotos são baixadas do Dropbox no servidor e embutidas como data-URI;
    falha em uma foto não derruba o documento (moldura "foto indisponível").
    """
    from app.services import checklist_query as cq  # noqa: PLC0415
    from app.services import laudo_pdf as lp  # noqa: PLC0415
    from app.services.dropbox import DropboxService  # noqa: PLC0415

    detalhe = cq.obter_checklist(db, identificador)
    if detalhe is None:
        raise HTTPException(status_code=404, detail="Checklist não encontrado")

    try:
        lp.garantir_pronto(detalhe)
    except lp.LaudoIndisponivelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    laudo = _montar_detalhe_response(detalhe).model_dump(mode="json")
    pdf_bytes = lp.gerar_pdf(laudo, dropbox=DropboxService(settings))
    nome = lp.nome_arquivo(laudo)

    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ── HITL: o operador fecha o julgamento — ticket mvp-c54-c57/10 ──────────────
#
# São rotas de ESCRITA: exigem sessão **e** CSRF, ao contrário das duas de
# leitura acima. O gabarito por vista alimenta o F1 do contrato (Anexo I §8).


class ChecklistValidationResponse(BaseModel):
    """Estado da validação depois da operação — o front recarrega o detalhe."""

    job_id: UUID
    checklist_id: str
    validacao: str
    validado_por: str | None = None
    validado_em: datetime | None = None
    vistas_validadas: int
    vistas_validaveis: int
    vistas_corrigidas: int


class ChecklistCorrecaoBody(BaseModel):
    """Correção de UMA vista. O tipo do erro é obrigatório de propósito.

    "Corrigido" sem dizer o quê só serve para contagem; com o tipo, vira insumo
    de calibragem do prompt.
    """

    campo: str
    tipo_erro: str
    #: Obrigatória em ``classe_errada``.
    classe: str | None = None
    #: Obrigatória em ``severidade_errada``; 1 (crítica) a 4 (baixa).
    severidade: int | None = None
    observacao: str | None = None


def _resolver_job_do_portal(db: Session, identificador: str) -> PipelineJob:
    """Mesma resolução da tela: ``job_id`` (UUID) ou ``codigo_checklist``.

    Reusa `checklist_query` para que confirmar **o que se está vendo** não possa
    resolver para outro job que a tela nunca mostrou.
    """
    from app.services.checklist_query import resolver_job  # noqa: PLC0415

    job = resolver_job(db, identificador)
    if job is None:
        raise HTTPException(status_code=404, detail="Checklist não encontrado")
    return job


def _resposta_validacao(job: PipelineJob, resultado: Any) -> ChecklistValidationResponse:  # noqa: ANN401
    return ChecklistValidationResponse(
        job_id=job.id,
        checklist_id=job.checklist_id,
        validacao=resultado.validacao,
        validado_por=resultado.validado_por,
        validado_em=resultado.validado_em,
        vistas_validadas=resultado.vistas_validadas,
        vistas_validaveis=resultado.vistas_validaveis,
        vistas_corrigidas=resultado.vistas_corrigidas,
    )


@router.post("/checklists/{identificador}/confirmar", response_model=ChecklistValidationResponse)
def portal_checklist_confirmar(
    identificador: str,
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    user: User = Depends(current_user),
) -> ChecklistValidationResponse:
    """Um clique confirma o checklist inteiro — ticket ``mvp-c54-c57/10``.

    Sem corpo: confirmar é dizer "sim" ao que está na tela. Se validar for caro,
    não acontece, e o F1 do contrato fica sem fonte.

    **Idempotente**: o gabarito mora na linha da vista, única por
    ``(job_id, campo)``. Confirmar duas vezes reescreve os mesmos valores — não
    duplica registro nem infla a métrica.
    """
    from app.services import checklist_validation as cv  # noqa: PLC0415

    job = _resolver_job_do_portal(db, identificador)
    try:
        resultado = cv.confirmar(db, job, por=user.email)
    except cv.ValidacaoInvalidaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log.info(
        "portal_checklist_confirmado",
        job_id=str(job.id),
        checklist_id=job.checklist_id,
        por=user.email,
    )
    return _resposta_validacao(job, resultado)


@router.post("/checklists/{identificador}/corrigir", response_model=ChecklistValidationResponse)
def portal_checklist_corrigir(
    identificador: str,
    body: ChecklistCorrecaoBody,
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    user: User = Depends(current_user),
) -> ChecklistValidationResponse:
    """Corrige UMA vista, dizendo o que estava errado.

    A correção é por vista porque os laudos são por vista. As demais vistas do
    checklist são confirmadas junto — o operador leu o relatório inteiro antes
    de contestar uma parte dele (ver ``checklist_validation``).
    """
    from app.services import checklist_validation as cv  # noqa: PLC0415

    job = _resolver_job_do_portal(db, identificador)
    try:
        resultado = cv.corrigir(
            db,
            job,
            campo=body.campo,
            tipo_erro=body.tipo_erro,
            classe=body.classe,
            severidade=body.severidade,
            observacao=body.observacao,
            por=user.email,
        )
    except cv.ValidacaoInvalidaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log.info(
        "portal_checklist_corrigido",
        job_id=str(job.id),
        checklist_id=job.checklist_id,
        campo=body.campo,
        tipo_erro=body.tipo_erro,
        por=user.email,
    )
    return _resposta_validacao(job, resultado)


# ── IAVS-068: Visualizador de avarias (read-only) ────────────────────────────


class PairSummaryResponse(BaseModel):
    id: UUID
    asset_code: str
    pair_date: date
    status: str
    saida_event_id: UUID | None = None
    retorno_event_id: UUID | None = None
    annotated_image_path: str | None = None
    saida_damage_class: str | None = None
    saida_damage_severity: str | None = None
    retorno_damage_class: str | None = None
    retorno_damage_severity: str | None = None
    checklist_id: str | None = None
    has_non_conformity: bool
    created_at: datetime


class PairsListResponse(BaseModel):
    total: int
    items: list[PairSummaryResponse]


class EventDetailResponse(BaseModel):
    id: UUID
    asset_code: str
    canonical_angle: str | None = None
    captured_at: datetime | None = None
    moment: str | None = None
    status: str
    damage_class: str | None = None
    damage_confidence: float | None = None
    damage_severity: str | None = None
    angle_class: str | None = None
    validation_reason: str | None = None
    result_json: dict[str, Any] | None = None
    source_path: str
    checklist_id: str | None = None
    ground_truth_class: str | None = None
    created_at: datetime


class PairDetailResponse(BaseModel):
    id: UUID
    asset_code: str
    pair_date: date
    status: str
    annotated_image_path: str | None = None
    saida: EventDetailResponse | None = None
    retorno: EventDetailResponse | None = None
    created_at: datetime
    updated_at: datetime


def _event_to_detail(ev: Any) -> EventDetailResponse | None:
    if ev is None:
        return None
    return EventDetailResponse(
        id=ev.id,
        asset_code=ev.asset_code,
        canonical_angle=ev.canonical_angle,
        captured_at=ev.captured_at,
        moment=ev.moment,
        status=ev.status,
        damage_class=ev.damage_class,
        damage_confidence=ev.damage_confidence,
        damage_severity=ev.damage_severity,
        angle_class=ev.angle_class,
        validation_reason=ev.validation_reason,
        result_json=ev.result_json,
        source_path=ev.source_path,
        checklist_id=ev.checklist_id,
        ground_truth_class=ev.ground_truth_class,
        created_at=ev.created_at,
    )


@router.get("/avarias/pairs", response_model=PairsListResponse)
def portal_avarias_pairs(
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    asset_code: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> PairsListResponse:
    """Lista pares saída×retorno com colunas de dano denormalizadas."""
    from app.services.avarias_query import PairListFilters, list_pairs  # noqa: PLC0415

    summaries, total = list_pairs(
        db,
        PairListFilters(
            limit=limit,
            offset=offset,
            status=status,
            asset_code=asset_code,
            date_from=date_from,
            date_to=date_to,
        ),
    )
    items = [
        PairSummaryResponse(
            id=s.id,
            asset_code=s.asset_code,
            pair_date=s.pair_date,
            status=s.status,
            saida_event_id=s.saida_event_id,
            retorno_event_id=s.retorno_event_id,
            annotated_image_path=s.annotated_image_path,
            saida_damage_class=s.saida_damage_class,
            saida_damage_severity=s.saida_damage_severity,
            retorno_damage_class=s.retorno_damage_class,
            retorno_damage_severity=s.retorno_damage_severity,
            checklist_id=s.checklist_id,
            has_non_conformity=bool(s.saida_damage_class or s.retorno_damage_class),
            created_at=s.created_at,
        )
        for s in summaries
    ]
    return PairsListResponse(total=total, items=items)


@router.get("/avarias/pairs/{pair_id}", response_model=PairDetailResponse)
def portal_avarias_pair_detail(
    pair_id: UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> PairDetailResponse:
    """Detalhe de um par: saída + retorno + JSON completo de classificação."""
    from app.services.avarias_query import get_pair_detail  # noqa: PLC0415

    detail = get_pair_detail(db, pair_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Par não encontrado")

    p = detail.pair
    return PairDetailResponse(
        id=p.id,
        asset_code=p.asset_code,
        pair_date=p.pair_date,
        status=p.status,
        annotated_image_path=p.annotated_image_path,
        saida=_event_to_detail(detail.saida_event),
        retorno=_event_to_detail(detail.retorno_event),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


# Prefixos de leitura permitidos no proxy: /Avarias (evidências) e /Sisloc
# (fotos do checklist de entrega, usadas como base de comparação). Ambos read-only.
_IMAGE_PROXY_PREFIXES = ("/Avarias/", "/Sisloc/")


@router.get("/avarias/image")
def portal_avarias_image(
    path: str = Query(..., description="Dropbox path — /Avarias/ ou /Sisloc/"),
    _user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> FastAPIResponse:
    """Proxy de imagem Dropbox para o visualizador. Requer sessão autenticada."""
    if not path.startswith(_IMAGE_PROXY_PREFIXES):
        raise HTTPException(
            status_code=422,
            detail="path deve começar com /Avarias/ ou /Sisloc/",
        )

    from app.services.dropbox import DropboxService, ResourceNotFoundError  # noqa: PLC0415

    try:
        image_bytes = DropboxService(settings).download_image(path)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Imagem não encontrada no Dropbox") from exc

    return FastAPIResponse(
        content=image_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


_VALID_GROUND_TRUTH = frozenset(
    {"ausencia_item", "fora_padrao_visual", "dano_visivel", "conforme"}
)


class GroundTruthBody(BaseModel):
    ground_truth_class: str


@router.patch("/avarias/events/{event_id}/ground-truth")
def portal_set_ground_truth(
    event_id: UUID,
    body: GroundTruthBody,
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    _user: User = Depends(current_user),
) -> dict[str, str]:
    """Validação humana (HITL) do operador: grava o gabarito do evento.

    Sessão + CSRF (não usa X-API-Key). Alimenta a métrica de aceite (F1).
    """
    if body.ground_truth_class not in _VALID_GROUND_TRUTH:
        raise HTTPException(
            status_code=422,
            detail=f"ground_truth_class deve ser um de: {sorted(_VALID_GROUND_TRUTH)}",
        )

    from app.models.event import Event  # noqa: PLC0415

    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Evento não encontrado")

    event.ground_truth_class = body.ground_truth_class
    db.commit()
    _log.info("portal_ground_truth_set", event_id=str(event_id), gt=body.ground_truth_class)
    return {"event_id": str(event_id), "ground_truth_class": body.ground_truth_class}


# ── upload de foto de avaria pelo portal (wizard) ──────────────────────────────

_ALNUM = re.compile(r"^[a-zA-Z0-9]+$")
# asset_code de frota pode ter hífen (ex.: FROTA-001); nada de '/', '..', espaços.
_ASSET_CODE = re.compile(r"^[a-zA-Z0-9-]+$")


class UploadResponse(BaseModel):
    event_id: str
    source_path: str
    status: str


@router.post("/avarias/upload", response_model=UploadResponse, status_code=202)
async def portal_avarias_upload(
    request: Request,
    foto: UploadFile = File(...),
    asset_code: str = Form(...),
    checklist_id: str = Form(...),
    moment: str = Form(...),
    angle: str = Form(...),
    uploader: str = Form("portal"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _csrf: None = Depends(verify_csrf),
    _user: User = Depends(current_user),
) -> UploadResponse:
    """Recebe a foto de retorno, sobe ao Dropbox e enfileira o processamento.

    A foto é comparada com o checklist de entrega (checklist_id). Sessão + CSRF.
    """
    if moment not in ("saida", "retorno"):
        raise HTTPException(status_code=422, detail="moment deve ser 'saida' ou 'retorno'")
    for nome, valor in (("angle", angle), ("uploader", uploader), ("checklist_id", checklist_id)):
        if not _ALNUM.match(valor):
            raise HTTPException(status_code=422, detail=f"{nome} deve ser alfanumérico sem espaços")
    if not _ASSET_CODE.match(asset_code):
        raise HTTPException(
            status_code=422,
            detail="asset_code deve ser alfanumérico (hífen permitido), sem '/', '..' ou espaços",
        )

    image_bytes = await foto.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="foto vazia")

    from datetime import datetime as _dt  # noqa: PLC0415

    from app.models.event import Event  # noqa: PLC0415
    from app.services.dropbox import DropboxService  # noqa: PLC0415
    from app.services.dropbox import parse_event_path  # noqa: PLC0415

    stamp = _dt.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{stamp}_{moment}_{angle}_{uploader}_{checklist_id}.jpg"

    dropbox = DropboxService(settings)
    source_path = dropbox.upload_avaria_image(asset_code, filename, image_bytes)

    parsed = parse_event_path(source_path, avarias_root=settings.dropbox_avarias_path)
    status = "queued" if parsed.has_complete_metadata else "metadata_missing"
    event = Event(
        id=_uuid.uuid4(),
        asset_code=parsed.asset_code,
        canonical_angle=parsed.canonical_angle,
        captured_at=parsed.captured_at,
        moment=parsed.moment,
        uploaded_by=parsed.uploaded_by,
        checklist_id=parsed.checklist_id,
        source_path=source_path,
        status=status,
    )
    db.add(event)
    db.commit()

    pool = getattr(request.app.state, "arq_pool", None)
    if pool is not None and status == "queued":
        await pool.enqueue_job("process_event", str(event.id))

    _log.info("portal_avaria_upload", event_id=str(event.id), asset_code=asset_code, status=status)
    return UploadResponse(event_id=str(event.id), source_path=source_path, status=status)


class EventStatusResponse(BaseModel):
    event_id: str
    status: str
    asset_code: str
    checklist_id: str | None = None
    moment: str | None = None
    damage_class: str | None = None
    damage_severity: str | None = None
    damage_confidence: float | None = None
    validation_reason: str | None = None
    source_path: str | None = None
    baseline_source_path: str | None = None
    observation: str | None = None
    pair_id: str | None = None


@router.get("/avarias/events/{event_id}", response_model=EventStatusResponse)
def portal_avarias_event_status(
    event_id: UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
) -> EventStatusResponse:
    """Status de um evento — usado pelo wizard para acompanhar o processamento."""
    from app.models.event import Event  # noqa: PLC0415
    from app.models.event_pair import EventPair  # noqa: PLC0415

    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Evento não encontrado")

    pair = (
        db.query(EventPair)
        .filter(
            (EventPair.saida_event_id == event_id) | (EventPair.retorno_event_id == event_id)
        )
        .first()
    )
    rj = event.result_json or {}
    baseline = rj.get("baseline_source_path") if isinstance(rj, dict) else None
    observation = None
    classes = rj.get("classes") if isinstance(rj, dict) else None
    if isinstance(classes, list) and classes:
        observation = classes[0].get("observation")
    return EventStatusResponse(
        event_id=str(event_id),
        status=event.status,
        asset_code=event.asset_code,
        checklist_id=event.checklist_id,
        moment=event.moment,
        damage_class=event.damage_class,
        damage_severity=event.damage_severity,
        damage_confidence=event.damage_confidence,
        validation_reason=event.validation_reason,
        source_path=event.source_path,
        baseline_source_path=baseline,
        observation=observation,
        pair_id=str(pair.id) if pair else None,
    )
