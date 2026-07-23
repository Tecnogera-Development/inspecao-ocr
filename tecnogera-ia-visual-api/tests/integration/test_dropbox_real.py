"""Integração real com a API do Dropbox (IAVS-004).

Estes testes só rodam se as credenciais estiverem presentes no ambiente
(``DROPBOX_APP_KEY``, ``DROPBOX_APP_SECRET``, ``DROPBOX_REFRESH_TOKEN``).
Em CI/dev sem credenciais, são automaticamente pulados.

Para rodar localmente:

    docker compose exec -T api pytest tests/integration/test_dropbox_real.py -m integration
"""

from __future__ import annotations

import os

import pytest

from app.services.dropbox import DropboxService

_TEM_ACCESS_TOKEN = bool(os.getenv("DROPBOX_ACCESS_TOKEN"))
_TEM_REFRESH = bool(os.getenv("DROPBOX_APP_KEY") and os.getenv("DROPBOX_REFRESH_TOKEN"))
_CREDENCIAIS_AUSENTES = not (_TEM_ACCESS_TOKEN or _TEM_REFRESH)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_CREDENCIAIS_AUSENTES, reason="credenciais Dropbox não configuradas"),
]


@pytest.fixture
def service() -> DropboxService:
    return DropboxService()


# Os 9 IDs catalogados no sprint-planning. Pelo menos um deles deve devolver
# imagens; testar todos seria excessivo para uma suíte de smoke.
CHECKLIST_DE_TESTE = "276800"


def test_lista_imagens_do_checklist_276800(service: DropboxService) -> None:
    imgs = service.list_checklist_images(CHECKLIST_DE_TESTE)
    assert imgs, f"nenhuma imagem encontrada para {CHECKLIST_DE_TESTE}"
    for i in imgs:
        assert i.parsed.checklist_id == CHECKLIST_DE_TESTE
        assert i.size_bytes > 0


def test_download_de_uma_imagem(service: DropboxService) -> None:
    imgs = service.list_checklist_images(CHECKLIST_DE_TESTE)
    if not imgs:
        pytest.skip("checklist de teste sem imagens")
    primeira = imgs[0]
    conteudo = service.download_image(primeira.dropbox_path)
    assert len(conteudo) == primeira.size_bytes


_PDF_MINIMO = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000054 00000 n \n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n98\n%%EOF\n"
)


def test_upload_relatorio_real_e_remove(service: DropboxService) -> None:
    out = service.upload_report("000000_smoke", _PDF_MINIMO)
    try:
        assert out.size_bytes == len(_PDF_MINIMO)
        assert out.dropbox_path.endswith(".pdf")
    finally:
        service._client.files_delete_v2(out.dropbox_path)
