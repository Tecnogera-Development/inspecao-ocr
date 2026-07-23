"""Testes unitários para catalog_builder — IAVS-005."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.catalog import CatalogReport, ChecklistEntry, FieldEntry
from app.models.dropbox import ImageMetadata, ParsedFilename
from app.services.catalog_builder import (
    build_field_entry,
    compute_intersection,
    compute_union,
    find_outliers,
    render_markdown_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed(
    checklist_id: str,
    field_name: str,
    *,
    captured_at: datetime | None = None,
    extension: str = ".jpg",
) -> ParsedFilename:
    return ParsedFilename(
        raw=f"{checklist_id}_{field_name}.jpg",
        checklist_id=checklist_id,
        field_name=field_name,
        captured_at=captured_at,
        extension=extension,
    )


def _make_img(
    checklist_id: str,
    field_name: str,
    *,
    size_bytes: int = 1024,
    dropbox_path: str | None = None,
) -> ImageMetadata:
    parsed = _make_parsed(checklist_id, field_name)
    return ImageMetadata(
        dropbox_path=dropbox_path or f"/Sisloc/CHK/{checklist_id}_{field_name}.jpg",
        filename=f"{checklist_id}_{field_name}.jpg",
        size_bytes=size_bytes,
        parsed=parsed,
    )


def _make_entry(checklist_id: str, field_names: list[str]) -> ChecklistEntry:
    fields = [
        FieldEntry(
            field_name=fn,
            dropbox_path=f"/Sisloc/{checklist_id}/{fn}.jpg",
            filename=f"{fn}.jpg",
            size_bytes=512,
            captured_at=None,
            resolution=None,
            extension=".jpg",
        )
        for fn in field_names
    ]
    return ChecklistEntry(checklist_id=checklist_id, fields=fields)


# ---------------------------------------------------------------------------
# build_field_entry
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_field_entry_sem_local_path() -> None:
    img = _make_img("276800", "c33")
    entry = build_field_entry(img)

    assert entry.field_name == "c33"
    assert entry.dropbox_path == img.dropbox_path
    assert entry.resolution is None
    assert entry.size_bytes == 1024
    assert entry.extension == ".jpg"


@pytest.mark.unit
def test_build_field_entry_com_local_path_extrai_resolucao(tmp_path: Path) -> None:
    img = _make_img("276800", "c33")
    fake_file = tmp_path / "c33.jpg"
    fake_file.write_bytes(b"fake-image")

    mock_img = MagicMock()
    mock_img.__enter__ = MagicMock(return_value=mock_img)
    mock_img.__exit__ = MagicMock(return_value=False)
    mock_img.width = 1920
    mock_img.height = 1080

    with patch("PIL.Image.open", return_value=mock_img):
        # local_path já existe; patch direto no Image.open
        entry = build_field_entry(img, local_path=fake_file)

    assert entry.resolution == (1920, 1080)


@pytest.mark.unit
def test_build_field_entry_local_path_inexistente() -> None:
    img = _make_img("276800", "c33")
    entry = build_field_entry(img, local_path=Path("/nao/existe.jpg"))

    assert entry.resolution is None


@pytest.mark.unit
def test_build_field_entry_pillow_erro_nao_propaga(tmp_path: Path) -> None:
    img = _make_img("276800", "c33")
    fake_file = tmp_path / "corrupted.jpg"
    fake_file.write_bytes(b"not an image")

    # Pillow vai levantar ao tentar abrir bytes inválidos
    entry = build_field_entry(img, local_path=fake_file)
    # Erro silenciado — resolução fica None
    assert entry.resolution is None


# ---------------------------------------------------------------------------
# compute_union
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compute_union_retorna_todos_os_campos() -> None:
    entries = [
        _make_entry("A", ["c1", "c2", "c3"]),
        _make_entry("B", ["c2", "c4"]),
        _make_entry("C", ["c1", "c5"]),
    ]
    result = compute_union(entries)
    assert result == {"c1", "c2", "c3", "c4", "c5"}


@pytest.mark.unit
def test_compute_union_lista_vazia() -> None:
    assert compute_union([]) == set()


# ---------------------------------------------------------------------------
# compute_intersection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compute_intersection_campos_comuns() -> None:
    entries = [
        _make_entry("A", ["c1", "c2", "c3"]),
        _make_entry("B", ["c1", "c2", "c4"]),
        _make_entry("C", ["c1", "c2", "c5"]),
    ]
    result = compute_intersection(entries)
    assert result == {"c1", "c2"}


@pytest.mark.unit
def test_compute_intersection_sem_campos_comuns() -> None:
    entries = [
        _make_entry("A", ["c1", "c2"]),
        _make_entry("B", ["c3", "c4"]),
    ]
    assert compute_intersection(entries) == set()


@pytest.mark.unit
def test_compute_intersection_ignora_entries_com_erro() -> None:
    entries = [
        _make_entry("A", ["c1", "c2"]),
        ChecklistEntry(checklist_id="ERR", error="timeout"),
    ]
    result = compute_intersection(entries)
    assert result == {"c1", "c2"}


@pytest.mark.unit
def test_compute_intersection_lista_vazia() -> None:
    assert compute_intersection([]) == set()


# ---------------------------------------------------------------------------
# find_outliers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_outliers_detecta_checklist_faltando_campo() -> None:
    entries = [
        _make_entry("A", ["c1", "c2", "c3"]),
        _make_entry("B", ["c1", "c2", "c3"]),
        _make_entry("C", ["c1", "c2", "c3"]),
        _make_entry("D", ["c1", "c2"]),  # falta c3 (presente em 3/4 = 75% >= 0.8? não)
        _make_entry("E", ["c1", "c2"]),  # falta c3 (idem)
    ]
    # c3 aparece em 3/5 = 60% — abaixo do threshold de 0.8
    result = find_outliers(entries, near_universal_threshold=0.8)
    assert result == {}


@pytest.mark.unit
def test_find_outliers_com_threshold_50() -> None:
    entries = [
        _make_entry("A", ["c1", "c2", "c3"]),
        _make_entry("B", ["c1", "c2", "c3"]),
        _make_entry("C", ["c1", "c2", "c3"]),
        _make_entry("D", ["c1", "c2"]),  # falta c3 — 3/4 = 75% >= 0.5
    ]
    result = find_outliers(entries, near_universal_threshold=0.5)
    assert "D" in result
    assert "c3" in result["D"]


@pytest.mark.unit
def test_find_outliers_ignora_entries_com_erro() -> None:
    entries = [
        _make_entry("A", ["c1", "c2"]),
        _make_entry("B", ["c1", "c2"]),
        ChecklistEntry(checklist_id="ERR", error="timeout"),
    ]
    result = find_outliers(entries, near_universal_threshold=0.8)
    assert "ERR" not in result


@pytest.mark.unit
def test_find_outliers_lista_vazia() -> None:
    assert find_outliers([]) == {}


# ---------------------------------------------------------------------------
# render_markdown_report
# ---------------------------------------------------------------------------


def _make_report(entries_map: dict[str, ChecklistEntry]) -> CatalogReport:
    return CatalogReport(
        generated_at=datetime(2026, 5, 17, 10, 0, 0),
        checklist_ids=list(entries_map.keys()),
        entries=entries_map,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_render_markdown_contem_header_e_checklist_ids() -> None:
    report = _make_report(
        {
            "A": _make_entry("A", ["c1", "c2"]),
            "B": _make_entry("B", ["c1", "c3"]),
        }
    )
    md = render_markdown_report(report)

    assert "# Catálogo de Checklists" in md
    assert "2026-05-17" in md
    assert "A" in md
    assert "B" in md


@pytest.mark.unit
def test_render_markdown_contem_tabela_de_campos() -> None:
    report = _make_report({"A": _make_entry("A", ["c1", "c2"])})
    md = render_markdown_report(report)

    assert "| Campo |" in md
    assert "c1" in md
    assert "c2" in md


@pytest.mark.unit
def test_render_markdown_com_erro_no_checklist() -> None:
    entries: dict[str, ChecklistEntry] = {
        "A": _make_entry("A", ["c1"]),
        "ERR": ChecklistEntry(checklist_id="ERR", error="timeout ao listar"),
    }
    report = CatalogReport(
        generated_at=datetime(2026, 5, 17, 10, 0, 0),
        checklist_ids=["A", "ERR"],
        entries=entries,  # type: ignore[arg-type]
    )
    md = render_markdown_report(report)

    assert "Checklists com Erro" in md
    assert "timeout ao listar" in md


@pytest.mark.unit
def test_render_markdown_secao_analise() -> None:
    report = _make_report(
        {
            "A": _make_entry("A", ["c1", "c2", "c3"]),
            "B": _make_entry("B", ["c1", "c2"]),
        }
    )
    md = render_markdown_report(report)

    assert "União" in md
    assert "Intersecção" in md
    assert "Outliers" in md


@pytest.mark.unit
def test_find_outliers_boundary_dois_checklists() -> None:
    """ceil(0.8 * 2) = 2, logo B precisa estar em ambos para ser near-universal.
    Com B em apenas 1/2, B não é near-universal e nenhum checklist é outlier.
    Valida math.ceil vs int (int(0.8*2)=1 daria falso positivo).
    """
    entries = [
        _make_entry("A", ["A", "B"]),
        _make_entry("B", ["A"]),  # B ausente aqui
    ]
    result = find_outliers(entries, near_universal_threshold=0.8)
    # B aparece em 1/2 checklists; ceil(0.8*2)=2, threshold não atingido
    # portanto B não é near-universal e não há outliers
    assert result == {}
