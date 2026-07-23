"""Testes unitários do PartitionBuilder — IAVS-040."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.partition_builder import Partition, build_partition


# ── helpers ───────────────────────────────────────────────────────────────────


def _imgs(field: str, n: int) -> list[str]:
    """Gera n filenames fictícios para um campo."""
    return [f"100_checklist_276800_{field}_{i}_10_04_2026 12_00_00.jpeg" for i in range(n)]


# ── tracer bullet ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_field_with_6_images_gets_3_shots_and_rest_in_eval() -> None:
    """Campo com ≥6 imagens → 3 no shot_bank, restante no eval."""
    field_images = {"c0": _imgs("c0", 6)}
    partition = build_partition(field_images, profile="F013")

    fp = partition.per_field["c0"]
    assert len(fp.shot_bank) == 3
    assert len(fp.eval) == 3
    assert fp.excluded is False
    assert fp.n_total == 6


# ── quotas ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_field_with_3_to_5_images_gets_2_shots_and_rest_in_eval() -> None:
    """Campo com 3-5 imagens → 2 no shot_bank, restante no eval."""
    field_images = {"c3": _imgs("c3", 4)}
    partition = build_partition(field_images, profile="F013")

    fp = partition.per_field["c3"]
    assert len(fp.shot_bank) == 2
    assert len(fp.eval) == 2
    assert fp.excluded is False
    assert fp.n_total == 4


@pytest.mark.unit
def test_field_with_exactly_3_images_gets_2_shots_and_1_eval() -> None:
    """Campo com exatamente 3 imagens → 2 shot + 1 eval."""
    field_images = {"c5": _imgs("c5", 3)}
    partition = build_partition(field_images, profile="F013")

    fp = partition.per_field["c5"]
    assert len(fp.shot_bank) == 2
    assert len(fp.eval) == 1
    assert fp.excluded is False


@pytest.mark.unit
def test_field_with_exactly_6_images_gets_3_shots_and_3_eval() -> None:
    """Campo com exatamente 6 imagens → 3 shot + 3 eval (boundary)."""
    field_images = {"c6": _imgs("c6", 6)}
    partition = build_partition(field_images, profile="F013")

    fp = partition.per_field["c6"]
    assert len(fp.shot_bank) == 3
    assert len(fp.eval) == 3


# ── exclusão ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_field_with_fewer_than_3_images_is_excluded_from_eval() -> None:
    """Campo com <3 imagens é excluído do eval; todas ficam no shot_bank."""
    field_images = {"c9": _imgs("c9", 2)}
    partition = build_partition(field_images, profile="F013")

    fp = partition.per_field["c9"]
    assert fp.excluded is True
    assert len(fp.shot_bank) == 2
    assert fp.eval == []
    assert fp.n_total == 2


@pytest.mark.unit
def test_field_with_1_image_is_excluded() -> None:
    """Campo com 1 imagem também é excluído."""
    field_images = {"c45": _imgs("c45", 1)}
    partition = build_partition(field_images, profile="F013")

    assert partition.per_field["c45"].excluded is True


# ── dataset_hash ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_dataset_hash_is_deterministic() -> None:
    """Mesmos inputs → mesmo dataset_hash."""
    field_images = {"c0": _imgs("c0", 4), "c3": _imgs("c3", 5)}
    p1 = build_partition(field_images, profile="F013")
    p2 = build_partition(field_images, profile="F013")
    assert p1.dataset_hash == p2.dataset_hash


@pytest.mark.unit
def test_dataset_hash_changes_when_images_change() -> None:
    """Dataset diferente → hash diferente."""
    p1 = build_partition({"c0": _imgs("c0", 4)}, profile="F013")
    p2 = build_partition({"c0": _imgs("c0", 5)}, profile="F013")
    assert p1.dataset_hash != p2.dataset_hash


# ── persistência ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_partition_saves_and_loads_from_json(tmp_path: Path) -> None:
    """Partition serializa para JSON e carrega de volta com fidelidade."""
    field_images = {"c0": _imgs("c0", 6), "c9": _imgs("c9", 2)}
    partition = build_partition(field_images, profile="F013")
    path = tmp_path / "partition_v2.json"
    partition.save(path)

    loaded = Partition.load(path)
    assert loaded.profile == "F013"
    assert loaded.dataset_hash == partition.dataset_hash
    assert loaded.per_field["c0"].shot_bank == partition.per_field["c0"].shot_bank
    assert loaded.per_field["c9"].excluded is True


@pytest.mark.unit
def test_saved_json_has_expected_top_level_keys(tmp_path: Path) -> None:
    """JSON salvo contém profile, generated_at, dataset_hash, per_field."""
    partition = build_partition({"c0": _imgs("c0", 4)}, profile="F013")
    path = tmp_path / "partition_v2.json"
    partition.save(path)

    data = json.loads(path.read_text())
    assert set(data.keys()) >= {"profile", "generated_at", "dataset_hash", "per_field"}


# ── multifield ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_mixed_fields_each_get_correct_quota() -> None:
    """Partição com campos de diferentes tamanhos é correta para cada um."""
    field_images = {
        "c_big": _imgs("c_big", 10),   # ≥6 → 3 shot + 7 eval
        "c_mid": _imgs("c_mid", 5),    # 3-5 → 2 shot + 3 eval
        "c_small": _imgs("c_small", 1), # <3 → excluded
    }
    partition = build_partition(field_images, profile="F013")

    assert len(partition.per_field["c_big"].shot_bank) == 3
    assert len(partition.per_field["c_big"].eval) == 7
    assert len(partition.per_field["c_mid"].shot_bank) == 2
    assert len(partition.per_field["c_mid"].eval) == 3
    assert partition.per_field["c_small"].excluded is True
