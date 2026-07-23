"""Testes unit do upload de relatório PDF (IAVS-006)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from dropbox.exceptions import ApiError, AuthError  # type: ignore[import-untyped]
from pydantic import SecretStr

from app.core.config import AppEnv, Settings
from app.core.exceptions import ConfigurationError, IntegrationError
from app.services.dropbox import DropboxService


def _make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "app_env": AppEnv.TEST,
        "dropbox_app_key": SecretStr("ak"),
        "dropbox_app_secret": SecretStr("as"),
        "dropbox_refresh_token": SecretStr("rt"),
        "dropbox_root_path": "/Sisloc",
        "dropbox_reports_path": "/comparativo_de_imagem",
    }
    base.update(overrides)
    return Settings(**base)


def _file_meta(path: str, size: int) -> Any:
    md = MagicMock()
    md.path_display = path
    md.path_lower = path.lower()
    md.size = size
    return md


def _client_para_upload(*, with_shared_link: str | None = "https://share/x") -> MagicMock:
    client = MagicMock()
    client.files_upload.return_value = _file_meta("/comparativo_de_imagem/file.pdf", 4096)
    if with_shared_link is not None:
        link = MagicMock(url=with_shared_link)
        client.sharing_create_shared_link_with_settings.return_value = link
    return client


@pytest.mark.unit
def test_upload_retorna_uploaded_report() -> None:
    client = _client_para_upload()
    svc = DropboxService(_make_settings(), client=client)

    out = svc.upload_report(
        "276800",
        b"%PDF-1.4 fake",
        captured_at=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
    )

    assert out.size_bytes == 4096
    assert out.shared_url == "https://share/x"
    assert out.dropbox_path == "/comparativo_de_imagem/file.pdf"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("captured_at", "esperado_sufixo"),
    [
        (datetime(2026, 5, 6, 14, 30, 22, tzinfo=UTC), "20260506_143022"),
        (datetime(2026, 12, 1, 0, 0, 5, tzinfo=UTC), "20261201_000005"),
    ],
)
def test_path_segue_formato_esperado(captured_at: datetime, esperado_sufixo: str) -> None:
    client = _client_para_upload()
    svc = DropboxService(_make_settings(), client=client)

    svc.upload_report("276800", b"data", captured_at=captured_at)

    args, kwargs = client.files_upload.call_args
    target = args[1] if len(args) > 1 else kwargs["path"]
    assert target == f"/comparativo_de_imagem/276800_{esperado_sufixo}.pdf"


@pytest.mark.unit
def test_upload_sob_sisloc_recusado() -> None:
    client = _client_para_upload()
    svc = DropboxService(
        _make_settings(dropbox_reports_path="/Sisloc/relatorios"),
        client=client,
    )

    with pytest.raises(ConfigurationError) as exc:
        svc.upload_report("276800", b"data")
    assert "read-only" in exc.value.message
    client.files_upload.assert_not_called()


@pytest.mark.unit
def test_auth_error_no_upload_vira_integration_error() -> None:
    client = MagicMock()
    client.files_upload.side_effect = AuthError(request_id="r", error="invalid_access_token")
    svc = DropboxService(_make_settings(), client=client)

    with pytest.raises(IntegrationError) as exc:
        svc.upload_report("276800", b"data")
    assert "enviar relatório" in exc.value.message


@pytest.mark.unit
def test_api_error_no_upload_vira_integration_error() -> None:
    client = MagicMock()
    client.files_upload.side_effect = ApiError(
        request_id="r",
        error=MagicMock(),
        user_message_text=None,
        user_message_locale=None,
    )
    svc = DropboxService(_make_settings(), client=client)

    with pytest.raises(IntegrationError):
        svc.upload_report("276800", b"data")


@pytest.mark.unit
def test_falha_no_share_link_nao_quebra_upload() -> None:
    client = _client_para_upload(with_shared_link=None)
    client.sharing_create_shared_link_with_settings.side_effect = AuthError(
        request_id="r", error="x"
    )
    svc = DropboxService(_make_settings(), client=client)

    out = svc.upload_report("276800", b"data")

    assert out.shared_url is None
    assert out.size_bytes == 4096


@pytest.mark.unit
def test_shared_link_ja_existente_recupera_url() -> None:
    client = _client_para_upload(with_shared_link=None)
    error = MagicMock()
    error.is_shared_link_already_exists.return_value = True
    client.sharing_create_shared_link_with_settings.side_effect = ApiError(
        request_id="r",
        error=error,
        user_message_text=None,
        user_message_locale=None,
    )
    existentes = MagicMock(links=[MagicMock(url="https://share/existente")])
    client.sharing_list_shared_links.return_value = existentes
    svc = DropboxService(_make_settings(), client=client)

    out = svc.upload_report("276800", b"data")

    assert out.shared_url == "https://share/existente"
