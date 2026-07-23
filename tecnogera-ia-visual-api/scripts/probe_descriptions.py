#!/usr/bin/env python3
"""probe_descriptions.py — gera descricao_auto_gerada para campos cN via Vision LLM.

Para cada campo com `descricao: "TODO"` em equipment_profiles.yaml, envia 4-6
imagens do shot_bank ao Claude (Sonnet) via tool_use `emit_descricao` e salva
o resultado como `descricao_auto_gerada:` (chave distinta).

Uso:
    python scripts/probe_descriptions.py
    python scripts/probe_descriptions.py --profile F013_liberacao_gerador
    python scripts/probe_descriptions.py --dry-run
    python scripts/probe_descriptions.py --min-imgs 2 --max-imgs 4

Requer: ANTHROPIC_API_KEY no ambiente.

Custo estimado: ~$0.60 total (4-6 imgs × ~39 campos TODO).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Permitir imports de app/ mesmo rodando fora do pacote instalado
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from app.services.description_prober import DescriptionProber, ProbeResult

_PROFILES_PATH = Path("app/profiles/equipment_profiles.yaml")
_SHOT_BANK_DIR = Path("data/shot_bank")
_CHECKLISTS_DIR = Path("data/checklists")
_PARTITION_V2 = Path("data/eval/partition_v2.json")
_DEFAULT_MIN_IMGS = 2
_DEFAULT_MAX_IMGS = 6


def _load_shot_bank_manifest(profile_id: str) -> dict:
    manifest_path = _SHOT_BANK_DIR / profile_id / "manifest.json"
    if not manifest_path.exists():
        print(f"[WARN] shot_bank manifest não encontrado: {manifest_path}", file=sys.stderr)
        return {}
    with manifest_path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_partition_v2() -> dict:
    if not _PARTITION_V2.exists():
        return {}
    with _PARTITION_V2.open(encoding="utf-8") as f:
        return json.load(f)


def _resolve_image(filename: str) -> Path | None:
    """Resolve o path completo de uma imagem a partir do filename usando checklist_id."""
    import re as _re

    m = _re.search(r"checklist_(\d+)_", filename)
    if not m:
        return None
    checklist_id = m.group(1)
    candidate = _CHECKLISTS_DIR / checklist_id / filename
    return candidate if candidate.exists() else None


def _collect_todo_fields(
    profiles_data: dict,
    manifest: dict,
    partition: dict,
    *,
    profile_id: str,
    min_imgs: int,
    max_imgs: int,
) -> dict[str, list[tuple[str, bytes]]]:
    """Retorna {field_name: [(filename, bytes), ...]} para campos com descricao: TODO."""
    shots_map: dict = manifest.get("shots", {})
    partition_fields: dict = partition.get("per_field", {})
    campos = profiles_data.get("profiles", {}).get(profile_id, {}).get("campos", [])

    result: dict[str, list[tuple[str, bytes]]] = {}
    for campo in campos:
        if campo.get("descricao") != "TODO":
            continue
        if campo.get("descricao_auto_gerada"):
            continue  # já probed

        field_name = campo["field_name"]
        images: list[tuple[str, bytes]] = []

        # 1. Shots do manifest (paths completos disponíveis)
        for shot in shots_map.get(field_name, []):
            if len(images) >= max_imgs:
                break
            img_path = Path(shot["path"])
            if img_path.exists():
                images.append((shot["filename"], img_path.read_bytes()))

        # 2. Complementar com shot_bank da partition_v2 se necessário
        if len(images) < max_imgs and field_name in partition_fields:
            for fname in partition_fields[field_name].get("shot_bank", []):
                if len(images) >= max_imgs:
                    break
                if any(existing_fn == fname for existing_fn, _ in images):
                    continue  # já incluída
                img_path = _resolve_image(fname)
                if img_path:
                    images.append((fname, img_path.read_bytes()))

        # 3. Complementar com imagens de eval se ainda não atingiu min
        if len(images) < min_imgs and field_name in partition_fields:
            for fname in partition_fields[field_name].get("eval", []):
                if len(images) >= max_imgs:
                    break
                img_path = _resolve_image(fname)
                if img_path:
                    images.append((fname, img_path.read_bytes()))

        if len(images) < min_imgs:
            print(
                f"[SKIP] {field_name}: apenas {len(images)} imagem(ns) disponível(is) "
                f"(mínimo {min_imgs})",
                file=sys.stderr,
            )
            continue

        result[field_name] = images[:max_imgs]

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default="F013_liberacao_gerador",
        help="Chave do perfil em equipment_profiles.yaml",
    )
    parser.add_argument(
        "--min-imgs",
        type=int,
        default=_DEFAULT_MIN_IMGS,
        help="Número mínimo de imagens por campo (default: %(default)s)",
    )
    parser.add_argument(
        "--max-imgs",
        type=int,
        default=_DEFAULT_MAX_IMGS,
        help="Número máximo de imagens por campo (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista campos elegíveis sem chamar o LLM ou modificar o YAML",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Modelo Anthropic (default: %(default)s)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not args.dry_run:
        print("ERRO: ANTHROPIC_API_KEY não configurada.", file=sys.stderr)
        sys.exit(1)

    with _PROFILES_PATH.open(encoding="utf-8") as f:
        profiles_data = yaml.safe_load(f)

    manifest = _load_shot_bank_manifest(args.profile)
    partition = _load_partition_v2()
    todo_fields = _collect_todo_fields(
        profiles_data,
        manifest,
        partition,
        profile_id=args.profile,
        min_imgs=args.min_imgs,
        max_imgs=args.max_imgs,
    )

    print(f"Campos elegíveis para probing em '{args.profile}': {len(todo_fields)}")
    for fn, imgs in todo_fields.items():
        print(f"  {fn}: {len(imgs)} imagem(ns)")

    if args.dry_run:
        print("\n[DRY-RUN] Nenhuma chamada ao LLM ou modificação ao YAML.")
        return

    prober = DescriptionProber(api_key=api_key, model=args.model)
    results: dict[str, ProbeResult] = {}

    for field_name, images in todo_fields.items():
        print(f"\nProbing {field_name} ({len(images)} imgs)...", end=" ", flush=True)
        try:
            result = prober.probe_field(field_name, images)
            results[field_name] = result
            print(f'→ "{result.descricao}" (conf={result.confidence:.2f})')
        except Exception as exc:  # noqa: BLE001
            print(f"ERRO: {exc}", file=sys.stderr)

    if results:
        print(f"\nAtualizando {_PROFILES_PATH} com {len(results)} descricao_auto_gerada...")
        prober.update_yaml(_PROFILES_PATH, results)
        print("Concluído.")

    print(f"\nResumo: {len(results)}/{len(todo_fields)} campos atualizados.")


if __name__ == "__main__":
    main()
