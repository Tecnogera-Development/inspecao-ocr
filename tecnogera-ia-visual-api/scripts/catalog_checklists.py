#!/usr/bin/env python3
"""Cataloga as imagens dos 9 checklists da Tecnogera via Dropbox — IAVS-005.

Uso rápido (sem download de imagens):
    python scripts/catalog_checklists.py

Com download (extrai resolução via Pillow):
    python scripts/catalog_checklists.py --download

Forçar re-fetch (ignora catalog.json existente):
    python scripts/catalog_checklists.py --force

Flags completas:
    --force           Re-faz todos os checklists independente de catalog.json
    --resume          (padrão) Pula checklists já presentes em catalog.json sem erro
    --download        Baixa cada imagem para extração de resolução via Pillow
    --cache-dir DIR   Pasta local para cache de imagens (default: ./data/checklists)
    --output-dir DIR  Pasta onde salvar catalog.json e catalog-report.md
                      (default: docs/exploracao)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Garante que o pacote `app` é encontrado quando executado de qualquer dir
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.logging import configure_logging, get_logger
from app.models.catalog import CatalogReport, ChecklistEntry
from app.services.catalog_builder import (
    build_field_entry,
    render_markdown_report,
)
from app.services.dropbox import DropboxService

# IDs dos 9 checklists definidos no IAVS-005
CHECKLIST_IDS: list[str] = [
    "276800",
    "267699",
    "278749",
    "278724",
    "277861",
    "278139",
    "278154",
    "269762",
    "278365",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cataloga imagens dos 9 checklists Tecnogera via Dropbox.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-faz todos os checklists, ignorando catalog.json existente.",
    )
    mode.add_argument(
        "--resume",
        action="store_true",
        help="(padrão) Pula checklists já catalogados sem erro em catalog.json.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        default=False,
        help="Baixa imagens para extrair resolução via Pillow (mais lento).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("./data/checklists"),
        metavar="DIR",
        help="Diretório local para cache de imagens baixadas.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/exploracao"),
        metavar="DIR",
        help="Diretório onde salvar catalog.json e catalog-report.md.",
    )
    return parser.parse_args()


def _load_existing_catalog(catalog_path: Path) -> dict[str, ChecklistEntry]:
    """Carrega catalog.json existente; retorna dict vazio se ausente ou inválido."""
    if not catalog_path.exists():
        return {}
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        entries_raw: dict = raw.get("entries", {})
        loaded: dict[str, ChecklistEntry] = {}
        for cid, data in entries_raw.items():
            if isinstance(data, dict):
                entry = ChecklistEntry(**data)
                # Só considera como "feito" se não teve erro
                if entry.error is None:
                    loaded[cid] = entry
        return loaded
    except Exception:  # noqa: BLE001
        return {}


def _catalog_single(
    svc: DropboxService,
    checklist_id: str,
    *,
    download: bool,
    cache_dir: Path,
    log,
) -> ChecklistEntry:
    """Cataloga um único checklist; propaga erros para o chamador tratar."""
    if download:
        # Baixa o batch (já chama list internamente) — evita chamada dupla à API
        dest = cache_dir / checklist_id
        local_images = svc.download_checklist_batch(checklist_id, dest_dir=dest)
        log.info(
            "catalog_checklist_listado",
            checklist_id=checklist_id,
            total_imagens=len(local_images),
        )
        fields = [
            build_field_entry(li.metadata, local_path=li.local_path)
            for li in local_images
        ]
        return ChecklistEntry(checklist_id=checklist_id, fields=fields)

    images = svc.list_checklist_images(checklist_id)
    log.info(
        "catalog_checklist_listado",
        checklist_id=checklist_id,
        total_imagens=len(images),
    )
    fields = [build_field_entry(img) for img in images]
    return ChecklistEntry(checklist_id=checklist_id, fields=fields)


def main() -> int:
    configure_logging()
    args = _parse_args()
    log = get_logger(__name__)

    output_dir: Path = args.output_dir.resolve()
    cache_dir: Path = args.cache_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog_path = output_dir / "catalog.json"
    report_path = output_dir / "catalog-report.md"

    # ------ carrega entradas já concluídas (modo resume) ----------------------
    done: dict[str, ChecklistEntry] = {}
    if not args.force:
        done = _load_existing_catalog(catalog_path)
        if done:
            log.info(
                "catalog_resumindo",
                ja_prontos=sorted(done.keys()),
            )

    # ------ inicializa serviço Dropbox ----------------------------------------
    try:
        svc = DropboxService()
    except Exception as exc:  # noqa: BLE001
        log.error("catalog_dropbox_init_falhou", reason=str(exc))
        return 1

    # ------ itera os 9 checklists ---------------------------------------------
    results: dict[str, ChecklistEntry] = dict(done)

    for cid in CHECKLIST_IDS:
        if cid in results:
            log.info("catalog_checklist_pulado", checklist_id=cid, motivo="resume")
            continue

        log.info("catalog_checklist_inicio", checklist_id=cid, download=args.download)
        try:
            entry = _catalog_single(
                svc,
                cid,
                download=args.download,
                cache_dir=cache_dir,
                log=log,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "catalog_checklist_erro",
                checklist_id=cid,
                reason=str(exc),
            )
            entry = ChecklistEntry(checklist_id=cid, fields=[], error=str(exc))

        results[cid] = entry
        log.info(
            "catalog_checklist_concluido",
            checklist_id=cid,
            campos=len(entry.fields),
            erro=entry.error,
        )

    # ------ monta e persiste CatalogReport -----------------------------------
    report = CatalogReport(
        generated_at=datetime.now(UTC),
        checklist_ids=CHECKLIST_IDS,
        entries=results,  # type: ignore[arg-type]
    )

    # Serializa: converte ChecklistEntry -> dict para JSON
    serializable_entries = {
        cid: entry.model_dump(mode="json") for cid, entry in results.items()
    }
    catalog_data = {
        "generated_at": report.generated_at.isoformat(),
        "checklist_ids": report.checklist_ids,
        "entries": serializable_entries,
    }
    catalog_path.write_text(
        json.dumps(catalog_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("catalog_json_salvo", path=str(catalog_path))

    # Markdown report
    markdown = render_markdown_report(report)
    report_path.write_text(markdown, encoding="utf-8")
    log.info("catalog_report_salvo", path=str(report_path))

    # ------ sumário final ----------------------------------------------------
    errors = [cid for cid, e in results.items() if e.error is not None]
    ok = [cid for cid in CHECKLIST_IDS if cid in results and results[cid].error is None]
    log.info(
        "catalog_concluido",
        checklists_ok=len(ok),
        checklists_com_erro=len(errors),
        ids_com_erro=errors or None,
    )

    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
