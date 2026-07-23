"""Testes da hierarquia de exceções e handlers (IAVS-002)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import (
    AppError,
    ConfigurationError,
    DomainError,
    IntegrationError,
    ResourceNotFoundError,
)


@pytest.mark.unit
def test_app_error_tem_payload_estruturado() -> None:
    err = DomainError("regra X violada", details={"campo": "valor"})
    payload = err.to_payload()
    assert payload["error"]["code"] == "domain_error"
    assert payload["error"]["message"] == "regra X violada"
    assert payload["error"]["details"] == {"campo": "valor"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exc_cls", "expected_status"),
    [
        (DomainError, 422),
        (ResourceNotFoundError, 404),
        (IntegrationError, 502),
        (ConfigurationError, 500),
    ],
)
def test_subclasses_tem_status_correto(exc_cls: type[AppError], expected_status: int) -> None:
    assert exc_cls.status_code == expected_status


@pytest.mark.unit
def test_handler_app_error_retorna_payload(app: FastAPI, client: TestClient) -> None:
    @app.get("/_test/raise-domain")
    def _raise() -> None:
        raise DomainError("não permitido", details={"x": 1})

    response = client.get("/_test/raise-domain")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "domain_error"
    assert body["error"]["message"] == "não permitido"
    assert body["error"]["details"] == {"x": 1}


@pytest.mark.unit
def test_handler_excecao_inesperada_retorna_500(app: FastAPI, client: TestClient) -> None:
    @app.get("/_test/raise-boom")
    def _boom() -> None:
        raise RuntimeError("kaboom")

    response = client.get("/_test/raise-boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "kaboom" not in body["error"]["message"]
