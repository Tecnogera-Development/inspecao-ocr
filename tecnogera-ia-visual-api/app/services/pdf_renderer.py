"""Conversão de relatório Markdown para PDF (IAVS-012).

Usa ``markdown`` para gerar HTML e ``WeasyPrint`` para renderizar PDF com
estilo profissional (cabeçalho/rodapé, numeração de páginas, tipografia
legível). Falhas viram ``IntegrationError``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import markdown

from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logging.getLogger("weasyprint").setLevel(logging.ERROR)
logging.getLogger("fontTools").setLevel(logging.ERROR)

_log = get_logger(__name__)

_DEFAULT_CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm 2.2cm 1.8cm;
    @bottom-center {
        content: "Página " counter(page) " de " counter(pages);
        font-family: "Liberation Sans", "Helvetica", sans-serif;
        font-size: 9pt;
        color: #555;
    }
}
body {
    font-family: "Liberation Sans", "Helvetica", sans-serif;
    font-size: 11pt;
    line-height: 1.45;
    color: #1a1a1a;
}
h1 { font-size: 20pt; margin: 0 0 0.6em; border-bottom: 2px solid #1a1a1a; padding-bottom: 0.2em; }
h2 { font-size: 14pt; margin: 1.4em 0 0.5em; color: #1a1a1a; }
h3 { font-size: 12pt; margin: 1em 0 0.4em; color: #2a2a2a; }
table { width: 100%; border-collapse: collapse; margin: 0.6em 0 1em; font-size: 10pt; }
th, td { border: 1px solid #cfcfcf; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f1f1f1; font-weight: 600; }
code {
    font-family: "Liberation Mono", "Menlo", monospace;
    font-size: 9.5pt;
    background: #f5f5f5;
    padding: 1px 4px;
    border-radius: 3px;
}
pre {
    background: #f5f5f5;
    padding: 8px 10px;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 9.5pt;
}
hr { border: none; border-top: 1px solid #d4d4d4; margin: 1em 0; }
small { color: #666; }
img { max-width: 100%; height: auto; }
"""


class PdfRendererService:
    """Renderiza Markdown em PDF com estilo padronizado."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._md = markdown.Markdown(
            extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
            output_format="html",
        )

    def render(self, markdown_text: str, *, title: str | None = None) -> bytes:
        """Converte ``markdown_text`` em bytes PDF.

        ``title`` é opcional e vai para o ``<title>`` do HTML, usado por
        leitores de PDF como nome do documento.
        """
        try:
            from weasyprint import CSS, HTML  # type: ignore[import-untyped]

            self._md.reset()
            html_body = self._md.convert(markdown_text)
            html_doc = self._wrap_html(html_body, title=title)
            pdf_bytes = HTML(string=html_doc).write_pdf(stylesheets=[CSS(string=_DEFAULT_CSS)])
        except Exception as exc:  # noqa: BLE001  fronteira com lib externa
            raise IntegrationError(
                "falha ao renderizar PDF",
                details={"reason": str(exc)},
            ) from exc

        if pdf_bytes is None:
            raise IntegrationError("renderização não produziu bytes", details={})

        _log.info("pdf_renderizado", bytes=len(pdf_bytes), title=title or "")
        return bytes(pdf_bytes)

    def render_to_file(
        self,
        markdown_text: str,
        dest: Path,
        *,
        title: str | None = None,
    ) -> Path:
        """Renderiza e grava em ``dest``. Cria diretórios pais se faltarem."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.render(markdown_text, title=title))
        return dest

    @staticmethod
    def _wrap_html(body: str, *, title: str | None) -> str:
        title_tag = f"<title>{title}</title>" if title else ""
        return (
            "<!DOCTYPE html><html lang='pt-BR'><head>"
            "<meta charset='utf-8'>"
            f"{title_tag}"
            "</head><body>"
            f"{body}"
            "</body></html>"
        )
