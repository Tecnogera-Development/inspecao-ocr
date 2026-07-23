"""PartitionBuilder — partição estratificada com cota mínima por campo — IAVS-040.

Interface pública:
  FieldPartition  — partição de um campo (Pydantic)
  Partition       — partição completa com metadados (Pydantic)
  build_partition — função pura, stateless
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.core.logging import get_logger

_log = get_logger(__name__)


class FieldPartition(BaseModel):
    """Partição de um campo: shot_bank (few-shot) + eval (avaliação) + metadados."""

    shot_bank: list[str]
    eval: list[str]
    excluded: bool
    n_total: int


class Partition(BaseModel):
    """Partição estratificada completa com rastreabilidade."""

    profile: str
    generated_at: str
    dataset_hash: str
    per_field: dict[str, FieldPartition]

    def save(self, path: Path) -> None:
        """Persiste a partição em JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _log.info("partition_saved", path=str(path), n_fields=len(self.per_field))

    @staticmethod
    def load(path: Path) -> Partition:
        """Carrega partição de um arquivo JSON."""
        return Partition.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )


def build_partition(
    field_images: dict[str, list[str]],
    *,
    profile: str,
    min_quota: int = 3,
) -> Partition:
    """Constrói partição estratificada com cota mínima por campo.

    Algoritmo:
    - ≥6 imagens → 3 no shot_bank, restante no eval
    - min_quota–5 imagens → 2 no shot_bank, restante no eval
    - <min_quota imagens → excluído do eval (excluded=True), tudo no shot_bank
    """
    all_filenames = sorted(fn for fns in field_images.values() for fn in fns)
    dataset_hash = _compute_hash(all_filenames)

    per_field: dict[str, FieldPartition] = {}
    for field, images in sorted(field_images.items()):
        n = len(images)
        if n >= 6:
            n_shots = 3
        elif n >= min_quota:
            n_shots = 2
        else:
            _log.warning(
                "partition_field_excluded",
                field=field,
                n_images=n,
                min_quota=min_quota,
            )
            per_field[field] = FieldPartition(
                shot_bank=list(images),
                eval=[],
                excluded=True,
                n_total=n,
            )
            continue

        per_field[field] = FieldPartition(
            shot_bank=list(images[:n_shots]),
            eval=list(images[n_shots:]),
            excluded=False,
            n_total=n,
        )

    return Partition(
        profile=profile,
        generated_at=datetime.now(UTC).isoformat(),
        dataset_hash=dataset_hash,
        per_field=per_field,
    )


def _compute_hash(filenames: list[str]) -> str:
    payload = "\n".join(filenames).encode()
    return hashlib.sha256(payload).hexdigest()
