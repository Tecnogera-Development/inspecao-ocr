"""Testes unit do DropboxService — SDK mockado (IAVS-004)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from dropbox.exceptions import ApiError, AuthError  # type: ignore[import-untyped]
from dropbox.files import FileMetadata  # type: ignore[import-untyped]
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
        "dropbox_root_path": "",
    }
    base.update(overrides)
    return Settings(**base)


def _file_meta(name: str, *, path: str | None = None, size: int = 1024) -> FileMetadata:
    full = path or f"/CHK/{name}"
    return FileMetadata(name=name, path_lower=full.lower(), path_display=full, size=size)


def _search_match(name: str, **kw: Any) -> Any:
    """Constrói um SearchMatchV2 fake que devolve FileMetadata em get_metadata()."""
    md_holder = MagicMock()
    md_holder.get_metadata.return_value = _file_meta(name, **kw)
    match = MagicMock()
    match.metadata = md_holder
    return match


@pytest.mark.unit
def test_falta_de_credenciais_levanta_configuration_error() -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST)
    with pytest.raises(ConfigurationError) as exc:
        DropboxService(cfg)
    assert "missing" in exc.value.details
    missing_str = " ".join(exc.value.details["missing"])
    assert "REFRESH_TOKEN" in missing_str
    assert "ACCESS_TOKEN" in missing_str


@pytest.mark.unit
def test_app_secret_opcional_pkce_flow() -> None:
    """Sem app_secret (PKCE flow) o serviço ainda inicializa."""
    cfg = Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        dropbox_app_key=SecretStr("ak"),
        dropbox_refresh_token=SecretStr("rt"),
    )
    svc = DropboxService(cfg)
    assert svc is not None


@pytest.mark.unit
def test_access_token_direto_basta() -> None:
    """Com DROPBOX_ACCESS_TOKEN setado, o serviço inicializa sem app_key."""
    cfg = Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        dropbox_access_token=SecretStr("sl.u.AGdotg..."),
    )
    svc = DropboxService(cfg)
    assert svc is not None


@pytest.mark.unit
def test_lista_imagens_filtra_por_checklist_e_extensao() -> None:
    client = MagicMock()
    client.files_search_v2.return_value = MagicMock(
        has_more=False,
        matches=[
            _search_match("276800_C0_painel_2026-04-15_14-32.jpg"),
            _search_match("276800_C12_nivel_oleo_2026-04-15_14-33.png"),
            _search_match("276800_relatorio.pdf"),  # extensão inválida
            _search_match("999999_C0_outro_2026-04-15_14-34.jpg"),  # checklist diferente
            _search_match("foracpadrao.jpg"),  # nome fora do padrão
        ]
    )
    svc = DropboxService(_make_settings(), client=client)

    imgs = svc.list_checklist_images("276800")

    assert [i.filename for i in imgs] == [
        "276800_C0_painel_2026-04-15_14-32.jpg",
        "276800_C12_nivel_oleo_2026-04-15_14-33.png",
    ]
    assert all(i.parsed.checklist_id == "276800" for i in imgs)


@pytest.mark.unit
def test_lista_imagens_respeita_root_path() -> None:
    client = MagicMock()
    client.files_search_v2.return_value = MagicMock(
        has_more=False,
        matches=[
            _search_match(
                "276800_C0_2026-04-15_14-32.jpg",
                path="/Filial-SP/CHK/276800_C0_2026-04-15_14-32.jpg",
            ),
            _search_match(
                "276800_C12_2026-04-15_14-33.jpg",
                path="/Outra/276800_C12_2026-04-15_14-33.jpg",
            ),
        ]
    )
    svc = DropboxService(_make_settings(dropbox_root_path="/Filial-SP"), client=client)

    imgs = svc.list_checklist_images("276800")

    assert len(imgs) == 1
    assert imgs[0].dropbox_path.startswith("/Filial-SP")


@pytest.mark.unit
def test_auth_error_vira_integration_error() -> None:
    client = MagicMock()
    client.files_search_v2.side_effect = AuthError(request_id="req-1", error="invalid_access_token")
    svc = DropboxService(_make_settings(), client=client)

    with pytest.raises(IntegrationError) as exc:
        svc.list_checklist_images("276800")
    assert "autenticação" in exc.value.message


@pytest.mark.unit
def test_api_error_na_busca_vira_integration_error() -> None:
    client = MagicMock()
    client.files_search_v2.side_effect = ApiError(
        request_id="req-1",
        error=MagicMock(),
        user_message_text=None,
        user_message_locale=None,
    )
    svc = DropboxService(_make_settings(), client=client)

    with pytest.raises(IntegrationError):
        svc.list_checklist_images("276800")


@pytest.mark.unit
def test_download_image_retorna_bytes() -> None:
    client = MagicMock()
    metadata = _file_meta("276800_C0_2026-04-15_14-32.jpg")
    response = MagicMock(content=b"\xff\xd8\xff\xe0PNGimg")
    client.files_download.return_value = (metadata, response)
    svc = DropboxService(_make_settings(), client=client)

    data = svc.download_image("/CHK/276800_C0_2026-04-15_14-32.jpg")

    assert data == b"\xff\xd8\xff\xe0PNGimg"
    assert isinstance(data, bytes)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    ["/Sisloc/foo.pdf", "/sisloc/bar.pdf", "/SISLOC/Relatorios/x.pdf"],
)
def test_assert_writable_recusa_pasta_read_only(path: str) -> None:
    with pytest.raises(ConfigurationError) as exc:
        DropboxService.assert_writable(path)
    assert "read-only" in exc.value.message


@pytest.mark.unit
def test_assert_writable_aceita_pastas_de_relatorio() -> None:
    DropboxService.assert_writable("/comparativo_de_imagem/276800.pdf")
    DropboxService.assert_writable("/Relatorios_IA/276800.pdf")


@pytest.mark.unit
def test_batch_baixa_e_grava_arquivos(tmp_path: Path) -> None:
    client = MagicMock()
    client.files_search_v2.return_value = MagicMock(
        has_more=False,
        matches=[
            _search_match(
                "276800_C0_2026-04-15_14-32.jpg",
                path="/CHK/276800_C0_2026-04-15_14-32.jpg",
            ),
            _search_match(
                "276800_C12_2026-04-15_14-33.jpg",
                path="/CHK/276800_C12_2026-04-15_14-33.jpg",
            ),
        ]
    )

    def _fake_download(path: str) -> tuple[Any, Any]:
        return _file_meta(Path(path).name), MagicMock(content=path.encode())

    client.files_download.side_effect = _fake_download
    svc = DropboxService(_make_settings(), client=client)

    baixadas = svc.download_checklist_batch("276800", dest_dir=tmp_path)

    assert len(baixadas) == 2
    for li in baixadas:
        assert li.local_path.exists()
        assert li.local_path.read_bytes() == li.metadata.dropbox_path.encode()
        assert li.local_path.parent == tmp_path


@pytest.mark.unit
def test_list_checklist_images_segue_paginacao() -> None:
    """Garante que has_more=True é seguido via files_search_continue_v2."""
    client = MagicMock()

    page1 = MagicMock(
        has_more=True,
        cursor="cursor-abc",
        matches=[
            _search_match("276800_C0_painel_2026-04-15_14-32.jpg"),
        ],
    )
    page2 = MagicMock(
        has_more=False,
        cursor=None,
        matches=[
            _search_match("276800_C1_motor_2026-04-15_14-33.jpg"),
        ],
    )

    client.files_search_v2.return_value = page1
    client.files_search_continue_v2.return_value = page2

    svc = DropboxService(_make_settings(), client=client)
    imgs = svc.list_checklist_images("276800")

    assert len(imgs) == 2
    assert imgs[0].filename == "276800_C0_painel_2026-04-15_14-32.jpg"
    assert imgs[1].filename == "276800_C1_motor_2026-04-15_14-33.jpg"
    client.files_search_continue_v2.assert_called_once_with("cursor-abc")
