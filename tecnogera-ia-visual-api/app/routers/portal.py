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
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.logging import get_logger
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

router = APIRouter(prefix="/api/v1/portal", tags=["portal"])
_log = get_logger(__name__)


# ── schemas ───────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: UUID
    email: str


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
    if user is None or not user.is_active:
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
) -> UserResponse:
    user = authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    user.last_login_at = datetime.now(UTC)
    db.commit()

    request.session["user_id"] = str(user.id)
    request.session["csrf_token"] = secrets.token_hex(32)

    _log.info("portal_login", user_id=str(user.id))
    return UserResponse(id=user.id, email=user.email)


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
    return UserResponse(id=user.id, email=user.email)


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
