"""Testes do PdfRendererService (IAVS-012)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import AppEnv, Settings
from app.core.exceptions import IntegrationError
from app.services.pdf_renderer import PdfRendererService

_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN = _ROOT / "docs" / "relatorio" / "golden-sample-276800.md"


def _settings() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST)


@pytest.mark.unit
def test_render_simples_gera_pdf_valido() -> None:
    pdf = PdfRendererService(_settings()).render("# Olá\n\nUm parágrafo.")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 500


@pytest.mark.unit
def test_render_golden_sample() -> None:
    md = _GOLDEN.read_text(encoding="utf-8")
    pdf = PdfRendererService(_settings()).render(md, title="Relatório 276800")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) < 5 * 1024 * 1024, "PDF maior que 5MB"


@pytest.mark.unit
def test_render_to_file_grava_no_destino(tmp_path: Path) -> None:
    dest = tmp_path / "sub" / "out.pdf"
    out = PdfRendererService(_settings()).render_to_file("# x", dest)
    assert out == dest
    assert dest.exists()
    assert dest.read_bytes().startswith(b"%PDF-")


@pytest.mark.unit
def test_falha_no_weasyprint_vira_integration_error() -> None:
    svc = PdfRendererService(_settings())
    # HTML/CSS são importados dentro de render() (from weasyprint import ...),
    # então o alvo do patch é o módulo weasyprint, não o pdf_renderer.
    with patch("weasyprint.HTML") as fake:
        fake.return_value.write_pdf.side_effect = RuntimeError("kaboom")
        with pytest.raises(IntegrationError) as exc:
            svc.render("# x")
    assert "renderizar PDF" in exc.value.message
