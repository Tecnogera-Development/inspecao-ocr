"""Fixtures E2E — mocka WeasyPrint antes que qualquer import aconteça."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock


def _mock_weasyprint() -> None:
    """Insere weasyprint falso no sys.modules para rodar sem libgobject."""
    if "weasyprint" in sys.modules:
        return
    mock_wp = ModuleType("weasyprint")
    mock_css = MagicMock(name="CSS")
    mock_html_cls = MagicMock(name="HTML")
    mock_html_cls.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
    mock_wp.CSS = mock_css
    mock_wp.HTML = mock_html_cls
    sys.modules["weasyprint"] = mock_wp


_mock_weasyprint()
