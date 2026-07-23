"""Testes unitários do Evaluator — IAVS-007."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.evaluator import EvalReport, Evaluator
from app.services.llm_provider import ClassificationResult

# ── helpers ──────────────────────────────────────────────────────────────────

_MODEL = "claude-sonnet-4-6"
_HASH = "abc123"


def _result(
    filename: str,
    predicted: str | None,
    confidence: float,
    *,
    is_valid: bool | None = None,
    requires_human_review: bool = False,
) -> ClassificationResult:
    """Constrói um ClassificationResult de teste."""
    if is_valid is None:
        is_valid = confidence >= 0.70
    return ClassificationResult(
        image_filename=filename,
        field_name=predicted,
        confidence=confidence,
        is_valid=is_valid,
        observation="ok",
        detected_issues=[],
        requires_human_review=requires_human_review,
        model_version=_MODEL,
        shot_bank_hash=_HASH,
    )


def _make_partition(tmp_path: Path, shot_bank_filenames: list[str]) -> Path:
    """Cria partition.json com filenames no shot_bank de c0."""
    partition = {
        "c0": {
            "shot_bank": shot_bank_filenames,
            "eval_set": [],
        }
    }
    p = tmp_path / "partition.json"
    p.write_text(json.dumps(partition))
    return p


# Filenames no formato real Sisloc: {loc}_checklist_{id}_{campo}_{seq}_{dd}_{mm}_{yyyy} {HH}_{MM}_{SS}
_F0 = "100_checklist_276800_c0_0_10_04_2026 12_12_01.jpeg"
_F3 = "100_checklist_276800_c3_0_10_04_2026 12_13_00.jpeg"
_F6 = "100_checklist_276800_c6_0_10_04_2026 12_14_00.jpeg"
_F4 = "100_checklist_276800_c4_0_10_04_2026 12_15_00.jpeg"
_F0b = "100_checklist_276800_c0_1_10_04_2026 12_16_00.jpeg"  # segunda imagem de c0


# ── tracer bullet ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_accuracy_global_100_percent() -> None:
    """100% acerto → accuracy_global=1.0."""
    classifications = [
        _result(_F0, "c0", 0.95),
        _result(_F3, "c3", 0.92),
        _result(_F6, "c6", 0.88),
    ]
    report = Evaluator.evaluate(classifications)

    assert report.accuracy_global == pytest.approx(1.0)
    assert report.n_evaluated == 3


# ── accuracy 50/50 ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_accuracy_global_50_percent() -> None:
    """Metade acerta, metade erra → accuracy_global≈0.5."""
    classifications = [
        _result(_F0, "c0", 0.95),
        _result(_F3, "c0", 0.80),  # errou: prediz c0, GT é c3
        _result(_F6, "c6", 0.88),
        _result(_F4, "c0", 0.75),  # errou: prediz c0, GT é c4
    ]
    report = Evaluator.evaluate(classifications)

    assert report.accuracy_global == pytest.approx(0.5)


# ── anti-leakage ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_shot_bank_images_excluded(tmp_path: Path) -> None:
    """Imagens do shot_bank em partition.json são excluídas do cálculo."""
    partition_path = _make_partition(tmp_path, [_F0])

    classifications = [
        _result(_F0, "c0", 0.95),   # no shot bank → excluída
        _result(_F3, "c3", 0.92),   # avaliada
    ]
    report = Evaluator.evaluate(classifications, partition_path=partition_path)

    assert report.n_excluded_shot_bank == 1
    assert report.n_evaluated == 1
    assert report.accuracy_global == pytest.approx(1.0)


# ── confusion matrix serializável ────────────────────────────────────────────


@pytest.mark.unit
def test_confusion_matrix_serializable() -> None:
    """confusion_matrix serializa como list[dict] com keys true/pred/count."""
    classifications = [
        _result(_F0, "c3", 0.80),  # c0→c3
        _result(_F3, "c3", 0.92),  # c3→c3
    ]
    report = Evaluator.evaluate(classifications)

    cm = report.confusion_matrix_serialized
    json_str = json.dumps(cm)
    parsed = json.loads(json_str)

    assert isinstance(parsed, list)
    for entry in parsed:
        assert set(entry.keys()) == {"true", "pred", "count"}


# ── ECE ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ece_overconfident_wrong_greater_than_calibrated() -> None:
    """Modelo super-confiante e errado tem ECE maior que modelo calibrado."""
    # super-confiante e errado
    wrong_classifications = [
        _result(_F0, "c3", 0.99),
        _result(_F3, "c0", 0.99),
        _result(_F6, "c0", 0.99),
    ]
    # calibrado: acerta com conf alta
    calibrated_classifications = [
        _result(_F0, "c0", 0.90),
        _result(_F3, "c3", 0.85),
        _result(_F6, "c6", 0.80),
    ]

    wrong_report = Evaluator.evaluate(wrong_classifications)
    calibrated_report = Evaluator.evaluate(calibrated_classifications)

    assert wrong_report.ece > calibrated_report.ece


# ── coverage ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_coverage_is_valid_ratio() -> None:
    """Coverage = count(is_valid=True) / count(total)."""
    classifications = [
        _result(_F0, "c0", 0.90, is_valid=True),
        _result(_F3, "c3", 0.50, is_valid=False),
        _result(_F6, "c6", 0.30, is_valid=False),
        _result(_F4, "c4", 0.95, is_valid=True),
    ]
    report = Evaluator.evaluate(classifications)

    assert report.coverage == pytest.approx(0.5)


# ── accuracy by confidence bucket ────────────────────────────────────────────


@pytest.mark.unit
def test_accuracy_by_confidence_bucket() -> None:
    """Estratificação por bucket de confiança: <0.40 / 0.40-0.70 / >=0.70."""
    classifications = [
        _result(_F0, "c3", 0.30),   # <0.40: errou (GT=c0, pred=c3)
        _result(_F3, "c3", 0.55),   # 0.40-0.70: acertou
        _result(_F6, "c6", 0.80),   # >=0.70: acertou
        _result(_F4, "c4", 0.95),   # >=0.70: acertou
    ]
    report = Evaluator.evaluate(classifications)

    buckets = report.accuracy_by_confidence_bucket
    assert "<0.40" in buckets
    assert "0.40-0.70" in buckets
    assert ">=0.70" in buckets
    assert buckets["<0.40"] == pytest.approx(0.0)
    assert buckets["0.40-0.70"] == pytest.approx(1.0)
    assert buckets[">=0.70"] == pytest.approx(1.0)


# ── recall e precision por campo ─────────────────────────────────────────────


@pytest.mark.unit
def test_recall_precision_per_field() -> None:
    """recall e precision por campo estão presentes e corretos."""
    classifications = [
        _result(_F0, "c0", 0.90),   # TP para c0
        _result(_F0b, "c0", 0.85),  # TP para c0
        _result(_F3, "c0", 0.75),   # FN para c3, FP para c0
    ]
    report = Evaluator.evaluate(classifications)

    # c0: TP=2, FN=0 → recall=1.0; TP=2, FP=1 → precision=2/3
    assert "c0" in report.recall_per_field
    assert report.recall_per_field["c0"] == pytest.approx(1.0)
    assert report.precision_per_field["c0"] == pytest.approx(2 / 3, abs=0.01)

    # c3: TP=0, FN=1 → recall=0.0
    assert "c3" in report.recall_per_field
    assert report.recall_per_field["c3"] == pytest.approx(0.0)


# ── persistência ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_save_writes_json(tmp_path: Path) -> None:
    """save() persiste EvalReport em data/eval/run_<timestamp>.json."""
    classifications = [
        _result(_F0, "c0", 0.95),
        _result(_F3, "c3", 0.92),
    ]
    report = Evaluator.evaluate(classifications)
    path = Evaluator.save(report, output_dir=tmp_path)

    assert path.exists()
    assert path.suffix == ".json"
    assert path.name.startswith("run_")
    data = json.loads(path.read_text())
    assert data["accuracy_global"] == pytest.approx(1.0)
    assert data["n_evaluated"] == 2
