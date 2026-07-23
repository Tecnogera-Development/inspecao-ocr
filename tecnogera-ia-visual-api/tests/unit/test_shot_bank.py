"""Testes unitários do ShotBank — IAVS-003.

Red-green-refactor incremental. Cada test verifica um comportamento
observável via interface pública do ShotBank.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.services.shot_bank import ImageRef, ShotBank


def _make_image(path: Path, *, size: tuple[int, int] = (1024, 768), solid: bool = False) -> None:
    """Cria um arquivo JPEG sintético.

    solid=True  → imagem de cor sólida (Laplacian variance ≈ 0, borrada/inútil)
    solid=False → padrão xadrez (alta variância → imagem nítida)
    """
    if solid:
        img = Image.new("RGB", size, color=(128, 128, 128))
    else:
        img = Image.new("RGB", size)
        pixels = img.load()
        assert pixels is not None
        for x in range(size[0]):
            for y in range(size[1]):
                pixels[x, y] = (255, 255, 255) if (x + y) % 2 == 0 else (0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG")


def _checklist_filename(checklist_id: str, field: str, seq: int = 0) -> str:
    return f"153000000_checklist_{checklist_id}_{field}_{seq}_01_01_2026 10_00_00.jpeg"


# ── Tracer bullet ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_build_descobre_imagens_por_campo(tmp_path: Path) -> None:
    """build_from_data_dir agrupa imagens pelo campo cN no filename."""
    checklist_dir = tmp_path / "111"
    for field in ("c0", "c3", "c4"):
        fn = _checklist_filename("111", field)
        _make_image(checklist_dir / fn)

    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)

    shots_c0 = bank.select_shots("c0")
    assert len(shots_c0) >= 1
    assert shots_c0[0].field_name == "c0"


# ── Partição 2-shot ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_partition_seleciona_dois_shots_e_resto_vai_para_eval(tmp_path: Path) -> None:
    """Com 7 imagens para um campo, 2 vão para shot bank e 5 para eval."""
    checklist_dir = tmp_path / "222"
    for seq in range(7):
        fn = _checklist_filename("222", "c0", seq)
        _make_image(checklist_dir / fn)

    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)

    shots = bank.select_shots("c0")
    eval_set = bank.eval_set("c0")

    assert len(shots) == 2
    assert len(eval_set) == 5

    shot_filenames = {ref.filename for ref in shots}
    eval_filenames = {ref.filename for ref in eval_set}
    assert shot_filenames.isdisjoint(eval_filenames)


# ── Determinismo ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_mesmo_dataset_produz_mesmo_hash(tmp_path: Path) -> None:
    """Dado o mesmo conjunto de arquivos, compute_hash() é estável."""
    checklist_dir = tmp_path / "333"
    for seq in range(3):
        fn = _checklist_filename("333", "c0", seq)
        _make_image(checklist_dir / fn)

    bank1 = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)
    bank2 = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)

    assert bank1.compute_hash() == bank2.compute_hash()
    assert len(bank1.compute_hash()) == 64  # SHA256 hex


# ── Filtro de qualidade ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_filtro_descarta_imagem_solida_borrada(tmp_path: Path) -> None:
    """Imagem sólida (var Laplacian ≈ 0) é descartada; imagem nítida passa."""
    checklist_dir = tmp_path / "444"
    # imagem nítida — deve passar
    _make_image(checklist_dir / _checklist_filename("444", "c0", 0), solid=False)
    # imagem sólida (borrada) — deve ser descartada
    _make_image(checklist_dir / _checklist_filename("444", "c0", 1), solid=True)

    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)

    # Apenas 1 imagem passou pelo filtro
    shots = bank.select_shots("c0", n=2)
    assert len(shots) == 1
    assert shots[0].filename == _checklist_filename("444", "c0", 0)


@pytest.mark.unit
def test_filtro_descarta_imagem_pequena(tmp_path: Path) -> None:
    """Imagem com resolução < 800px é descartada."""
    checklist_dir = tmp_path / "555"
    # imagem pequena — deve ser descartada
    _make_image(
        checklist_dir / _checklist_filename("555", "c0", 0),
        size=(400, 300),
        solid=False,
    )
    # imagem grande e nítida — deve passar
    _make_image(
        checklist_dir / _checklist_filename("555", "c0", 1),
        size=(1024, 768),
        solid=False,
    )

    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)

    shots = bank.select_shots("c0", n=2)
    assert len(shots) == 1
    assert shots[0].filename == _checklist_filename("555", "c0", 1)


# ── select_shots com exclude ─────────────────────────────────────────────────

@pytest.mark.unit
def test_select_shots_respeita_exclude(tmp_path: Path) -> None:
    """select_shots não retorna imagens cujo filename está em exclude."""
    checklist_dir = tmp_path / "666"
    filenames = [_checklist_filename("666", "c0", seq) for seq in range(4)]
    for fn in filenames:
        _make_image(checklist_dir / fn)

    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)

    # Pega os 2 shots padrão
    all_shots = bank.select_shots("c0", n=2)
    to_exclude = [all_shots[0].filename]

    new_shots = bank.select_shots("c0", n=2, exclude=to_exclude)
    returned_filenames = [s.filename for s in new_shots]
    assert all_shots[0].filename not in returned_filenames


# ── Persistência do manifest ─────────────────────────────────────────────────

@pytest.mark.unit
def test_save_manifest_persiste_json(tmp_path: Path) -> None:
    """save_manifest cria manifest.json com estrutura correta."""
    checklist_dir = tmp_path / "777"
    for seq in range(3):
        _make_image(checklist_dir / _checklist_filename("777", "c0", seq))

    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)
    manifest_path = tmp_path / "shot_bank" / "F013_liberacao_gerador" / "manifest.json"
    bank.save_manifest(manifest_path)

    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert data["profile_id"] == "F013_liberacao_gerador"
    assert "hash" in data
    assert "shots" in data


# ── Partição eval persistida ─────────────────────────────────────────────────

@pytest.mark.unit
def test_save_partition_cria_partition_json(tmp_path: Path) -> None:
    """save_partition cria partition.json com shot_bank e eval_set disjuntos."""
    checklist_dir = tmp_path / "888"
    for seq in range(5):
        _make_image(checklist_dir / _checklist_filename("888", "c0", seq))

    bank = ShotBank.build_from_data_dir("F013_liberacao_gerador", tmp_path)
    partition_path = tmp_path / "eval" / "partition.json"
    bank.save_partition(partition_path)

    assert partition_path.exists()
    data = json.loads(partition_path.read_text())
    assert "c0" in data
    assert "shot_bank" in data["c0"]
    assert "eval_set" in data["c0"]

    # Listas disjuntas
    shot_set = set(data["c0"]["shot_bank"])
    eval_set = set(data["c0"]["eval_set"])
    assert shot_set.isdisjoint(eval_set)
