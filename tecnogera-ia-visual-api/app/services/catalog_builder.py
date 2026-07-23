"""Funções puras de construção do catálogo de checklists — IAVS-005.

Responsabilidades:
- Converter ``ImageMetadata`` em ``FieldEntry`` (com resolução via Pillow se
  o arquivo local estiver disponível).
- Calcular union / intersection / outliers entre checklists catalogados.
- Renderizar um relatório Markdown a partir de um ``CatalogReport``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.models.catalog import CatalogReport, ChecklistEntry, FieldEntry

if TYPE_CHECKING:  # pragma: no cover
    from app.models.dropbox import ImageMetadata

_log = get_logger(__name__)


def build_field_entry(
    img: ImageMetadata,
    local_path: Path | None = None,
) -> FieldEntry:
    """Constrói ``FieldEntry`` a partir de um ``ImageMetadata`` do Dropbox.

    Se ``local_path`` for fornecido e apontar para um arquivo existente, tenta
    abrir com Pillow para extrair a resolução ``(width, height)``. Erros de
    leitura da imagem são silenciados (resolução fica ``None``).
    """
    resolution: tuple[int, int] | None = None

    if local_path is not None and local_path.exists():
        try:
            from PIL import Image  # noqa: PLC0415  importação tardia para evitar dep obrigatória

            with Image.open(local_path) as im:
                resolution = (im.width, im.height)
        except Exception as exc:  # noqa: BLE001  Pillow pode levantar vários tipos
            _log.warning(
                "catalog_resolucao_falhou",
                filename=img.filename,
                reason=str(exc),
            )

    return FieldEntry(
        field_name=img.parsed.field_name,
        dropbox_path=img.dropbox_path,
        filename=img.filename,
        size_bytes=img.size_bytes,
        captured_at=img.parsed.captured_at,
        resolution=resolution,
        extension=img.parsed.extension,
    )


def compute_union(entries: list[ChecklistEntry]) -> set[str]:
    """Retorna a união de todos os ``field_name`` presentes em pelo menos um checklist."""
    result: set[str] = set()
    for entry in entries:
        for field in entry.fields:
            result.add(field.field_name)
    return result


def compute_intersection(entries: list[ChecklistEntry]) -> set[str]:
    """Retorna os ``field_name`` presentes em **todos** os checklists sem erro."""
    valid = [e for e in entries if e.error is None]
    if not valid:
        return set()

    sets = [frozenset(f.field_name for f in e.fields) for e in valid]
    result = sets[0]
    for s in sets[1:]:
        result = result & s
    return set(result)


def find_outliers(
    entries: list[ChecklistEntry],
    near_universal_threshold: float = 0.8,
) -> dict[str, list[str]]:
    """Identifica campos ausentes em checklists que deveriam tê-los.

    Para cada ``field_name`` presente em pelo menos ``near_universal_threshold``
    fraction dos checklists válidos, retorna quais checklists estão sem ele.

    Retorno: ``{checklist_id: [field_name, ...]}``.
    """
    valid = [e for e in entries if e.error is None]
    if not valid:
        return {}

    total = len(valid)
    threshold = math.ceil(near_universal_threshold * total)
    if threshold == 0:
        threshold = 1

    # Contagem de ocorrências por campo
    field_counts: dict[str, int] = {}
    for entry in valid:
        for field in entry.fields:
            field_counts[field.field_name] = field_counts.get(field.field_name, 0) + 1

    # Campos "quase universais"
    near_universal = {fn for fn, cnt in field_counts.items() if cnt >= threshold}

    outliers: dict[str, list[str]] = {}
    for entry in valid:
        present = {f.field_name for f in entry.fields}
        missing = sorted(near_universal - present)
        if missing:
            outliers[entry.checklist_id] = missing

    return outliers


def render_markdown_report(report: CatalogReport) -> str:
    """Renderiza ``CatalogReport`` como Markdown estruturado.

    Inclui sumário geral, tabelas por checklist e seção de análise
    (union / intersection / outliers).
    """
    lines: list[str] = []

    lines.append("# Catálogo de Checklists — IAVS-005")
    lines.append("")
    lines.append(f"Gerado em: `{report.generated_at.isoformat()}`")
    lines.append("")
    lines.append(f"Checklists analisados: {', '.join(report.checklist_ids)}")
    lines.append("")

    # Reconstruir ChecklistEntry a partir de entries (que pode ser dict ou obj)
    valid_entries: list[ChecklistEntry] = []
    error_ids: list[str] = []

    for cid in report.checklist_ids:
        raw = report.entries.get(cid)
        if raw is None:
            continue
        if isinstance(raw, ChecklistEntry):
            if raw.error:
                error_ids.append(cid)
            else:
                valid_entries.append(raw)
        elif isinstance(raw, dict):
            if "error" in raw and raw["error"]:
                error_ids.append(cid)
            else:
                entry = ChecklistEntry(**raw)
                valid_entries.append(entry)

    # Tabela por checklist
    lines.append("## Detalhes por Checklist")
    lines.append("")

    for entry in valid_entries:
        lines.append(f"### Checklist `{entry.checklist_id}`")
        lines.append("")
        lines.append(f"Total de imagens: {len(entry.fields)}")
        lines.append("")
        if entry.fields:
            lines.append("| Campo | Arquivo | Tamanho (bytes) | Resolução | Extensão |")
            lines.append("|-------|---------|-----------------|-----------|----------|")
            for f in entry.fields:
                res = f"{f.resolution[0]}x{f.resolution[1]}" if f.resolution else "—"
                lines.append(
                    f"| {f.field_name} | {f.filename} | {f.size_bytes} | {res} | {f.extension} |"
                )
        lines.append("")

    if error_ids:
        lines.append("### Checklists com Erro")
        lines.append("")
        for cid in error_ids:
            raw = report.entries.get(cid)
            err_msg = raw.get("error") if isinstance(raw, dict) else (raw.error if raw else "—")
            lines.append(f"- `{cid}`: {err_msg}")
        lines.append("")

    # Análise
    lines.append("## Análise de Campos")
    lines.append("")

    union_fields = compute_union(valid_entries)
    intersection_fields = compute_intersection(valid_entries)
    outlier_map = find_outliers(valid_entries)

    lines.append(f"**União** ({len(union_fields)} campos únicos):")
    lines.append("")
    if union_fields:
        lines.append(", ".join(f"`{f}`" for f in sorted(union_fields)))
    lines.append("")

    lines.append(f"**Intersecção** ({len(intersection_fields)} campos em todos):")
    lines.append("")
    if intersection_fields:
        lines.append(", ".join(f"`{f}`" for f in sorted(intersection_fields)))
    else:
        lines.append("_Nenhum campo presente em todos os checklists._")
    lines.append("")

    lines.append("**Outliers** (checklists com campos ausentes ≥ 80%):")
    lines.append("")
    if outlier_map:
        for cid, missing in sorted(outlier_map.items()):
            lines.append(f"- `{cid}`: faltando {', '.join(f'`{m}`' for m in missing)}")
    else:
        lines.append("_Nenhum outlier detectado._")
    lines.append("")

    return "\n".join(lines)
