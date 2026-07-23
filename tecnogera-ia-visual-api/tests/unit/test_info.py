"""Testes do endpoint /info (IAVS-002)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import AppEnv


@pytest.mark.unit
def test_info_responde_200(client: TestClient) -> None:
    response = client.get("/info")
    assert response.status_code == 200


@pytest.mark.unit
def test_info_inclui_nome_versao_env(client: TestClient) -> None:
    body = client.get("/info").json()
    assert body["name"] == "tecnogera-ia-visual-api"
    assert body["version"]
    assert body["env"] == AppEnv.TEST.value
