"""Hierarquia de exceções da aplicação e handler global do FastAPI.

Toda exceção de domínio deriva de ``AppError`` e expõe ``status_code`` e
``error_code`` para serialização HTTP consistente. ``register_exception_handlers``
plugar handlers no FastAPI sem expor stacktrace em produção.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

_log = get_logger(__name__)


class AppError(Exception):
    """Exceção base do domínio.

    Subclasses devem definir ``status_code`` e ``error_code`` apropriados.
    """

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                "details": self.details,
            }
        }


class DomainError(AppError):
    """Regra de negócio violada."""

    status_code = 422
    error_code = "domain_error"


class ResourceNotFoundError(AppError):
    """Recurso solicitado não existe."""

    status_code = 404
    error_code = "not_found"


class IntegrationError(AppError):
    """Falha em integração externa (Dropbox, provedor de IA, etc)."""

    status_code = 502
    error_code = "integration_error"


class ConfigurationError(AppError):
    """Configuração ausente ou inválida em runtime."""

    status_code = 500
    error_code = "configuration_error"


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    log_method = _log.warning if exc.status_code < 500 else _log.error
    log_method(
        "app_error",
        error_code=exc.error_code,
        status_code=exc.status_code,
        message=exc.message,
        details=exc.details,
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    _log.exception("unhandled_exception", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Erro interno inesperado.",
                "details": {},
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
