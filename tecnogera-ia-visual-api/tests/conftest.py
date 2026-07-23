"""Fixtures compartilhadas entre todos os níveis de teste."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import AppEnv, Settings, get_settings
from app.main import create_app

_DROPBOX_ENV_VARS = (
    "DROPBOX_APP_KEY",
    "DROPBOX_APP_SECRET",
    "DROPBOX_REFRESH_TOKEN",
    "DROPBOX_ACCESS_TOKEN",
)


@pytest.fixture(autouse=True)
def _isolar_env_dropbox(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove DROPBOX_* do OS env em testes unit.

    Testes marcados com ``@pytest.mark.integration`` mantêm as variáveis
    intactas — eles validam o fluxo real contra o Dropbox.
    """
    if request.node.get_closest_marker("integration"):
        return
    for var in _DROPBOX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST, log_level="DEBUG")


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)
