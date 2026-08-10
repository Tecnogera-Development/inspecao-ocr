"""Leitura incremental de /Sisloc por cursor — ticket mvp-c54-c57/07.

Nenhum teste aqui toca o Dropbox de verdade: o cliente é mock. As chamadas
exercitadas são só de leitura (`files_list_folder*`) — o Dropbox da Tecnogera é
somente leitura, por princípio permanente do projeto.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from dropbox.exceptions import ApiError
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata

from app.core.config import AppEnv, Settings
from app.core.exceptions import IntegrationError
from app.services.dropbox import DropboxService

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST, dropbox_root_path="/Sisloc")


def _service(client: MagicMock, cfg: Settings) -> DropboxService:
    return DropboxService(cfg, client=client)


def _arquivo(path: str, *, quando: datetime | None = None) -> FileMetadata:
    return FileMetadata(
        name=path.rsplit("/", 1)[-1],
        path_display=path,
        path_lower=path.lower(),
        size=4096,
        server_modified=quando or datetime(2026, 8, 1, 9, 0, 0),
    )


def _pagina(entries: list[Any], cursor: str, has_more: bool = False) -> MagicMock:
    page = MagicMock()
    page.entries = entries
    page.cursor = cursor
    page.has_more = has_more
    return page


_BASE = "/Sisloc/MG - CGE/Checklist/Chk"
_NOME_OK = "153269005_checklist_278749_c54_0_01_08_2026 09_00_00.jpeg"


# ── bootstrap ─────────────────────────────────────────────────────────────────


def test_latest_cursor_nao_lista_nada(cfg: Settings) -> None:
    """É o que evita a varredura de 67 min e fixa o marco de corte."""
    client = MagicMock()
    client.files_list_folder_get_latest_cursor.return_value = MagicMock(cursor="c0")

    assert _service(client, cfg).latest_checklist_cursor() == "c0"
    client.files_list_folder_get_latest_cursor.assert_called_once_with(
        "/Sisloc", recursive=True, include_deleted=False
    )
    client.files_list_folder.assert_not_called()


# ── delta ─────────────────────────────────────────────────────────────────────


def test_delta_parseia_nome_real_do_sisloc(cfg: Settings) -> None:
    client = MagicMock()
    client.files_list_folder_continue.return_value = _pagina(
        [_arquivo(f"{_BASE}/{_NOME_OK}")], "c1"
    )

    delta = _service(client, cfg).list_checklist_delta("c0")

    assert delta.cursor == "c1"
    assert len(delta.images) == 1
    img = delta.images[0]
    assert img.parsed.checklist_id == "278749"
    assert img.parsed.field_name == "c54"
    assert img.server_modified == datetime(2026, 8, 1, 9, 0, 0)


def test_delta_ignora_pasta_de_sistema(cfg: Settings) -> None:
    """Bug conhecido: `_anotados`/`_gabaritos` são artefato do pipeline."""
    client = MagicMock()
    client.files_list_folder_continue.return_value = _pagina(
        [
            _arquivo(f"/Sisloc/MG - CGE/_anotados/{_NOME_OK}"),
            _arquivo(f"/Sisloc/_lixeira/Checklist/{_NOME_OK}"),
            _arquivo(f"{_BASE}/{_NOME_OK}"),
        ],
        "c1",
    )

    delta = _service(client, cfg).list_checklist_delta("c0")

    assert [i.dropbox_path for i in delta.images] == [f"{_BASE}/{_NOME_OK}"]


def test_delta_ignora_extensao_e_entrada_nao_arquivo(cfg: Settings) -> None:
    client = MagicMock()
    client.files_list_folder_continue.return_value = _pagina(
        [
            _arquivo(f"{_BASE}/relatorio.pdf"),
            FolderMetadata(name="Chk", path_display=_BASE, path_lower=_BASE.lower()),
            DeletedMetadata(name=_NOME_OK, path_display=f"{_BASE}/{_NOME_OK}"),
            _arquivo(f"{_BASE}/{_NOME_OK}"),
        ],
        "c1",
    )

    delta = _service(client, cfg).list_checklist_delta("c0")

    assert len(delta.images) == 1
    assert delta.ignorados == 0


def test_delta_conta_nome_fora_do_padrao(cfg: Settings) -> None:
    client = MagicMock()
    client.files_list_folder_continue.return_value = _pagina(
        [_arquivo(f"{_BASE}/foto-solta.jpg"), _arquivo(f"{_BASE}/{_NOME_OK}")], "c1"
    )

    delta = _service(client, cfg).list_checklist_delta("c0")

    assert len(delta.images) == 1
    assert delta.ignorados == 1


def test_delta_pagina_ate_o_fim(cfg: Settings) -> None:
    client = MagicMock()
    client.files_list_folder_continue.side_effect = [
        _pagina([_arquivo(f"{_BASE}/{_NOME_OK}")], "c1", has_more=True),
        _pagina([_arquivo(f"{_BASE}/{_NOME_OK}")], "c2", has_more=False),
    ]

    delta = _service(client, cfg).list_checklist_delta("c0")

    assert delta.cursor == "c2"
    assert len(delta.images) == 2
    assert not delta.has_more


def test_delta_respeita_teto_de_paginas(cfg: Settings) -> None:
    """Limita o trabalho de uma rodada; o cursor guarda o progresso parcial."""
    client = MagicMock()
    client.files_list_folder_continue.side_effect = [
        _pagina([_arquivo(f"{_BASE}/{_NOME_OK}")], f"c{i}", has_more=True) for i in range(1, 4)
    ]

    delta = _service(client, cfg).list_checklist_delta("c0", max_pages=2)

    assert delta.has_more
    assert delta.cursor == "c2"


def test_cursor_invalidado_devolve_reset_em_vez_de_estourar(cfg: Settings) -> None:
    erro = MagicMock()
    erro.is_reset.return_value = True
    client = MagicMock()
    client.files_list_folder_continue.side_effect = ApiError("req", erro, "msg", None)

    delta = _service(client, cfg).list_checklist_delta("c0")

    assert delta.reset
    assert delta.images == []


def test_falha_de_api_vira_integration_error(cfg: Settings) -> None:
    erro = MagicMock()
    erro.is_reset.return_value = False
    client = MagicMock()
    client.files_list_folder_continue.side_effect = ApiError("req", erro, "msg", None)

    with pytest.raises(IntegrationError):
        _service(client, cfg).list_checklist_delta("c0")


# ── backfill deliberado ───────────────────────────────────────────────────────


def test_listagem_completa_e_paginada_entre_rodadas(cfg: Settings) -> None:
    client = MagicMock()
    client.files_list_folder.return_value = _pagina(
        [_arquivo(f"{_BASE}/{_NOME_OK}")], "c1", has_more=True
    )
    client.files_list_folder_continue.return_value = _pagina(
        [_arquivo(f"{_BASE}/{_NOME_OK}")], "c2", has_more=False
    )

    delta = _service(client, cfg).iniciar_listagem_completa()

    assert len(delta.images) == 2
    assert delta.cursor == "c2"
    client.files_list_folder.assert_called_once_with("/Sisloc", recursive=True, limit=2000)
