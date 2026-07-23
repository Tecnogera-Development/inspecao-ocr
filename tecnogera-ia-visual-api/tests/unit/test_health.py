"""Testes do endpoint de healthcheck (IAVS-002)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
def test_health_responde_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


@pytest.mark.unit
def test_health_retorna_status_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.json()["status"] == "ok"


@pytest.mark.unit
def test_health_inclui_versao(client: TestClient) -> None:
    response = client.get("/health")
    body = response.json()
    assert "version" in body
    assert isinstance(body["version"], str)
    assert body["version"]
