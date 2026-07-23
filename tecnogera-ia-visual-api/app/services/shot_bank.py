"""ShotBank — constrói e gerencia o banco de shots few-shot para o Modelo 1.

Pipeline:
  1. Varre ``data/checklists/`` buscando imagens com padrão ``cN`` no filename.
  2. Filtra por qualidade (resolução ≥ 800px, Laplacian variance > 100).
  3. Seleciona deterministicamente 2 shots por campo para o bank.
  4. Persiste manifest em ``data/shot_bank/<perfil>/manifest.json``.
  5. Persiste partição anti-leakage em ``data/eval/partition.json``.

Uso:
    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", Path("data/checklists"))
    shots = bank.select_shots("c0")          # list[ImageRef]
    h = bank.compute_hash()                  # SHA256 estável
    bank.save_manifest(manifest_path)
    bank.save_partition(partition_path)

CLI:
    python -m app.services.shot_bank build --profile F013_liberacao_gerador
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.core.logging import get_logger
from app.services.dropbox import parse_filename

_log = get_logger(__name__)

# ── Constantes de qualidade ───────────────────────────────────────────────────
_MIN_RESOLUTION_PX = 800          # menor dimensão (width ou height) em pixels
_MIN_LAPLACIAN_VARIANCE = 100.0   # variância do filtro de bordas — descarta blur

# Extensões aceitas (mesmas do DropboxService)
_VALID_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp"})

# N padrão de shots por campo no shot bank
_DEFAULT_N_SHOTS = 2


class ImageRef(BaseModel):
    """Referência a uma imagem do dataset."""

    path: Path
    filename: str
    field_name: str
    checklist_id: str
    quality_score: float


class ShotBank:
    """Banco de shots few-shot por campo, com partição anti-leakage."""

    def __init__(
        self,
        profile_id: str,
        shots: dict[str, list[ImageRef]],
        eval_sets: dict[str, list[ImageRef]],
    ) -> None:
        self._profile_id = profile_id
        self._shots = shots          # campo → shots do bank
        self._eval_sets = eval_sets  # campo → imagens de avaliação

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def build_from_data_dir(
        cls,
        profile_id: str,
        data_dir: Path,
        *,
        n_shots: int = _DEFAULT_N_SHOTS,
    ) -> ShotBank:
        """Constrói um ShotBank a partir do diretório de checklists.

        Parâmetros
        ----------
        profile_id:
            ID do perfil de equipamento (ex: ``"F013_liberacao_gerador"``).
        data_dir:
            Diretório raiz contendo subpastas por checklist_id.
        n_shots:
            Número de shots por campo no bank (default: 2).
        """
        field_images: dict[str, list[ImageRef]] = {}

        for img_path in sorted(_iter_images(data_dir)):
            parsed = parse_filename(img_path.name)
            if parsed is None or parsed.field_name is None:
                continue

            score = _quality_score(img_path)
            width, height = _image_size(img_path)
            max_dim = max(width, height)

            if max_dim < _MIN_RESOLUTION_PX:
                _log.debug(
                    "shot_bank_quality_rejected_resolution",
                    filename=img_path.name,
                    max_dim=max_dim,
                )
                continue

            if score < _MIN_LAPLACIAN_VARIANCE:
                _log.debug(
                    "shot_bank_quality_rejected_blur",
                    filename=img_path.name,
                    score=score,
                )
                continue

            ref = ImageRef(
                path=img_path,
                filename=img_path.name,
                field_name=parsed.field_name,
                checklist_id=parsed.checklist_id or "",
                quality_score=score,
            )
            field_images.setdefault(parsed.field_name, []).append(ref)

        shots: dict[str, list[ImageRef]] = {}
        eval_sets: dict[str, list[ImageRef]] = {}

        for field, refs in field_images.items():
            shots[field] = refs[:n_shots]
            eval_sets[field] = refs[n_shots:]
            if len(refs) < n_shots:
                _log.warning(
                    "shot_bank_insufficient_shots",
                    field=field,
                    available=len(refs),
                    required=n_shots,
                )

        return cls(profile_id, shots, eval_sets)

    # ── Interface pública ────────────────────────────────────────────────────

    def select_shots(
        self,
        field_name: str,
        n: int = _DEFAULT_N_SHOTS,
        exclude: list[str] | None = None,
    ) -> list[ImageRef]:
        """Retorna até ``n`` shots para o campo, excluindo filenames em ``exclude``."""
        candidates = self._shots.get(field_name, [])
        if exclude:
            excluded = set(exclude)
            candidates = [r for r in candidates if r.filename not in excluded]
        return candidates[:n]

    def eval_set(self, field_name: str) -> list[ImageRef]:
        """Retorna as imagens do eval set para o campo."""
        return self._eval_sets.get(field_name, [])

    def compute_hash(self) -> str:
        """SHA256 estável dos shots ordenados — para rastreabilidade."""
        all_filenames = sorted(
            ref.filename
            for refs in self._shots.values()
            for ref in refs
        )
        payload = "\n".join(all_filenames).encode()
        return hashlib.sha256(payload).hexdigest()

    # ── Persistência ─────────────────────────────────────────────────────────

    def save_manifest(self, path: Path) -> None:
        """Persiste o manifest em JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "profile_id": self._profile_id,
            "hash": self.compute_hash(),
            "shots": {
                field: [_ref_to_dict(r) for r in refs]
                for field, refs in self._shots.items()
            },
        }
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        _log.info("shot_bank_manifest_saved", path=str(path))

    def save_partition(self, path: Path) -> None:
        """Persiste a partição anti-leakage em JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        all_fields = set(self._shots) | set(self._eval_sets)
        partition: dict[str, Any] = {
            field: {
                "shot_bank": [r.filename for r in self._shots.get(field, [])],
                "eval_set": [r.filename for r in self._eval_sets.get(field, [])],
            }
            for field in sorted(all_fields)
        }
        path.write_text(json.dumps(partition, indent=2), encoding="utf-8")
        _log.info("shot_bank_partition_saved", path=str(path))


# ── Helpers internos ──────────────────────────────────────────────────────────

def _iter_images(data_dir: Path) -> list[Path]:
    """Itera recursivamente por imagens no diretório de checklists."""
    images: list[Path] = []
    if not data_dir.exists():
        return images
    for child in data_dir.iterdir():
        if child.is_dir():
            for img in child.iterdir():
                if img.suffix.lower() in _VALID_EXTENSIONS:
                    images.append(img)
        elif child.suffix.lower() in _VALID_EXTENSIONS:
            images.append(child)
    return images


def _image_size(path: Path) -> tuple[int, int]:
    """Retorna (width, height) da imagem."""
    from PIL import Image  # lazy import — evita custo em módulos que não usam imagens

    with Image.open(path) as img:
        return img.size  # (width, height)


def _quality_score(path: Path) -> float:
    """Variância do filtro de bordas — proxy para nitidez da imagem.

    Uma imagem totalmente sólida tem score ≈ 0 (sem bordas → borrada/inútil).
    Uma imagem com detalhes visuais tem score alto.
    """
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(path) as img:
        gray = img.convert("L")
        edges = gray.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(edges)
        return float(stat.var[0])


def _ref_to_dict(ref: ImageRef) -> dict[str, Any]:
    return {
        "filename": ref.filename,
        "field_name": ref.field_name,
        "checklist_id": ref.checklist_id,
        "quality_score": ref.quality_score,
        "path": str(ref.path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_build(profile_id: str, data_dir: Path, output_dir: Path) -> None:
    bank = ShotBank.build_from_data_dir(profile_id, data_dir)

    manifest_path = output_dir / "shot_bank" / profile_id / "manifest.json"
    partition_path = output_dir / "eval" / "partition.json"

    bank.save_manifest(manifest_path)
    bank.save_partition(partition_path)

    total_shots = sum(len(v) for v in bank._shots.values())
    total_eval = sum(len(v) for v in bank._eval_sets.values())
    fields = sorted(bank._shots)

    print(f"ShotBank — perfil: {profile_id}")
    print(f"  Campos com shots: {len(fields)}")
    print(f"  Total shots (bank): {total_shots}")
    print(f"  Total shots (eval): {total_eval}")
    print(f"  Hash: {bank.compute_hash()}")
    print(f"  Manifest: {manifest_path}")
    print(f"  Partition: {partition_path}")

    for field in fields:
        n_bank = len(bank._shots[field])
        n_eval = len(bank._eval_sets.get(field, []))
        print(f"    {field}: {n_bank} shots, {n_eval} eval")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ShotBank CLI")
    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser("build", help="Constrói o ShotBank")
    build_parser.add_argument("--profile", required=True, help="ID do perfil")
    build_parser.add_argument(
        "--data-dir",
        default="data/checklists",
        help="Diretório de checklists (default: data/checklists)",
    )
    build_parser.add_argument(
        "--output-dir",
        default="data",
        help="Diretório de saída (default: data)",
    )

    args = parser.parse_args()
    if args.command == "build":
        _cli_build(
            profile_id=args.profile,
            data_dir=Path(args.data_dir),
            output_dir=Path(args.output_dir),
        )
    else:
        parser.print_help()
