"""Endpoints de meta-informação (health / info)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import AppEnv, Settings, get_settings

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    version: str


class InfoResponse(BaseModel):
    name: str
    version: str
    env: AppEnv


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=200,
    summary="Healthcheck",
    description="Indica que o processo da API está vivo.",
)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)


@router.get(
    "/info",
    response_model=InfoResponse,
    status_code=200,
    summary="Informações da aplicação",
    description="Retorna nome, versão e ambiente da aplicação.",
)
def info(settings: Settings = Depends(get_settings)) -> InfoResponse:
    return InfoResponse(
        name=settings.app_name,
        version=settings.app_version,
        env=settings.app_env,
    )
