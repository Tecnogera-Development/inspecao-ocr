"""Endpoints do pipeline de inspeção visual — IAVS-001.

POST /api/v1/pipeline/run       — dispara pipeline assíncrono
GET  /api/v1/pipeline/jobs/{id} — consulta estado de um job
GET  /api/v1/pipeline/jobs      — lista todos os jobs (paginada)
"""

from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel, field_validator

from app.core.config import AppEnv, Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.pipeline import JobCreatedResponse, JobDetailResponse, PipelineJob
from app.services.dropbox import DropboxService

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])
_log = get_logger(__name__)

_CHECKLIST_ID_RE = re.compile(r"^\d+$")


def verify_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Valida X-API-Key. Fail-closed: sem chave configurada, só libera em dev/test.

    Em staging/produção a ausência de PIPELINE_API_KEY é erro de configuração
    (503) — nunca acesso liberado. Em produção o boot já falha sem a chave.
    """
    if settings.pipeline_api_key is None:
        if settings.app_env in (AppEnv.DEVELOPMENT, AppEnv.TEST):
            return
        raise HTTPException(
            status_code=503,
            detail="Autenticação da API não configurada (PIPELINE_API_KEY ausente)",
        )
    expected = settings.pipeline_api_key.get_secret_value()
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="X-API-Key inválida ou ausente")


class RunRequest(BaseModel):
    checklist_id: str

    @field_validator("checklist_id")
    @classmethod
    def _validar_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("checklist_id não pode ser vazio")
        v = v.strip()
        if not _CHECKLIST_ID_RE.match(v):
            raise ValueError("checklist_id deve conter apenas dígitos (formato Sisloc)")
        return v


async def _run_batch_async(
    job_id: uuid.UUID,
    checklist_id: str,
    settings: Settings,
) -> None:
    """Executa pipeline em modo batch em background com Session própria."""
    from app.db.session import get_session_factory
    from app.services.orchestrator import Orchestrator

    db = get_session_factory()()
    try:
        dropbox = DropboxService(settings)
        orch = Orchestrator(db=db, dropbox=dropbox, settings=settings)
        await asyncio.to_thread(orch.run_batch, job_id=job_id, checklist_id=checklist_id)
    finally:
        db.close()


async def _run_pipeline_async(
    job_id: uuid.UUID,
    checklist_id: str,
    settings: Settings,
) -> None:
    """Executa pipeline em background com timeout configurável e Session própria."""
    from app.db.session import get_session_factory
    from app.services.dropbox import DropboxService
    from app.services.orchestrator import Orchestrator

    db = get_session_factory()()
    try:
        dropbox = DropboxService(settings)
        orch = Orchestrator(db=db, dropbox=dropbox, settings=settings)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(orch.run, job_id=job_id, checklist_id=checklist_id),
                timeout=settings.pipeline_timeout_seconds,
            )
        except TimeoutError:
            job = db.get(PipelineJob, job_id)
            if job is not None and job.status not in ("done", "failed"):
                job.status = "failed"
                job.error = "pipeline_timeout"
                job.finished_at = datetime.now(UTC)
                db.commit()
            _log.error("pipeline_timeout", job_id=str(job_id))
    finally:
        db.close()


@router.post("/run", status_code=202, response_model=JobCreatedResponse)
def run_pipeline(
    body: RunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
    settings: Settings = Depends(get_settings),
    mode: str = Query(default="sync"),
) -> JobCreatedResponse:
    if mode == "batch":
        dropbox = DropboxService(settings)
        images = dropbox.list_checklist_images(body.checklist_id)
        if len(images) < settings.batch_min_images:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"checklist com {len(images)}<{settings.batch_min_images} imagens; "
                    "use mode=sync"
                ),
            )

    job_id = uuid.uuid4()
    job = PipelineJob(id=job_id, checklist_id=body.checklist_id, status="pending", mode=mode)
    db.add(job)
    db.commit()

    if mode == "batch":
        background_tasks.add_task(
            _run_batch_async, job_id, body.checklist_id, settings
        )
    else:
        background_tasks.add_task(
            _run_pipeline_async, job_id, body.checklist_id, settings
        )

    _log.info("pipeline_job_created", job_id=str(job_id), checklist_id=body.checklist_id, mode=mode)
    return JobCreatedResponse(job_id=str(job_id), status="pending")


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> JobDetailResponse:
    job = db.get(PipelineJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return JobDetailResponse.model_validate(job)


@router.get("/jobs", response_model=list[JobDetailResponse])
def list_jobs(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
    limit: int = 50,
    offset: int = 0,
) -> list[JobDetailResponse]:
    jobs = (
        db.query(PipelineJob)
        .order_by(PipelineJob.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [JobDetailResponse.model_validate(j) for j in jobs]
