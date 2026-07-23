"""Evaluator: matriz de confusão e métricas por rodada — IAVS-007.

Interface pública:
  EvalReport   — resultados de avaliação (Pydantic)
  Evaluator    — stateless, método evaluate() principal
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.dropbox import parse_filename
from app.services.llm_provider import ClassificationResult

_log = get_logger(__name__)

_N_ECE_BINS = 10


class EvalReport(BaseModel):
    """Resultado de uma rodada de avaliação."""

    accuracy_global: float
    accuracy_per_field: dict[str, float]
    confusion_matrix_serialized: list[dict[str, Any]] = Field(default_factory=list)
    coverage: float
    ece: float
    recall_per_field: dict[str, float] = Field(default_factory=dict)
    precision_per_field: dict[str, float] = Field(default_factory=dict)
    accuracy_by_confidence_bucket: dict[str, float] = Field(default_factory=dict)
    n_evaluated: int
    n_excluded_shot_bank: int = 0


class Evaluator:
    """Avaliador stateless: compara classificações com ground truth do filename."""

    @staticmethod
    def evaluate(
        classifications: list[ClassificationResult],
        partition_path: Path | None = None,
    ) -> EvalReport:
        """Avalia classificações contra ground truth extraído do filename.

        Parâmetros
        ----------
        classifications:
            Resultados do Classifier para um checklist.
        partition_path:
            Caminho para partition.json do ShotBank. Se fornecido, imagens
            listadas em shot_bank são excluídas do cálculo (anti-leakage).
        """
        shot_bank_set = _load_shot_bank_set(partition_path)

        included: list[ClassificationResult] = []
        n_excluded = 0
        for c in classifications:
            if c.image_filename in shot_bank_set:
                n_excluded += 1
            else:
                included.append(c)

        if not included:
            return EvalReport(
                accuracy_global=0.0,
                accuracy_per_field={},
                coverage=0.0,
                ece=0.0,
                n_evaluated=0,
                n_excluded_shot_bank=n_excluded,
            )

        pairs = _extract_ground_truth_pairs(included)
        accuracy_global = _compute_accuracy(pairs)
        accuracy_per_field = _compute_accuracy_per_field(pairs)
        confusion_matrix = _compute_confusion_matrix(pairs)
        confusion_serialized = _serialize_confusion_matrix(confusion_matrix)
        coverage = _compute_coverage(included)
        ece = _compute_ece(pairs, _N_ECE_BINS)
        recall_per_field, precision_per_field = _compute_recall_precision(pairs)
        accuracy_by_bucket = _compute_accuracy_by_bucket(pairs)

        return EvalReport(
            accuracy_global=accuracy_global,
            accuracy_per_field=accuracy_per_field,
            confusion_matrix_serialized=confusion_serialized,
            coverage=coverage,
            ece=ece,
            recall_per_field=recall_per_field,
            precision_per_field=precision_per_field,
            accuracy_by_confidence_bucket=accuracy_by_bucket,
            n_evaluated=len(included),
            n_excluded_shot_bank=n_excluded,
        )

    @staticmethod
    def save(report: EvalReport, *, output_dir: Path) -> Path:
        """Persiste EvalReport em output_dir/run_<timestamp>.json."""
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        path = output_dir / f"run_{ts}.json"
        path.write_text(
            json.dumps(report.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _log.info("eval_report_saved", path=str(path))
        return path


# ── helpers internos ──────────────────────────────────────────────────────────

def _load_shot_bank_set(partition_path: Path | None) -> set[str]:
    if partition_path is None or not partition_path.exists():
        return set()
    data = json.loads(partition_path.read_text(encoding="utf-8"))
    # Suporta partition_v2 (com chave "per_field") e o formato antigo (flat dict).
    field_map: dict[str, object] = data.get("per_field", data)
    result: set[str] = set()
    for field_data in field_map.values():
        if isinstance(field_data, dict):
            result.update(field_data.get("shot_bank", []))
    return result


def _extract_ground_truth_pairs(
    classifications: list[ClassificationResult],
) -> list[tuple[str, str, float]]:
    """Retorna lista de (ground_truth, predicted, confidence) para classificações com GT válido."""
    pairs: list[tuple[str, str, float]] = []
    for c in classifications:
        if c.field_name is None:
            continue
        try:
            parsed = parse_filename(c.image_filename)
            gt = parsed.field_name
        except (ValueError, AttributeError):
            continue
        if gt:
            pairs.append((gt, c.field_name, c.confidence))
    return pairs


def _compute_accuracy(pairs: list[tuple[str, str, float]]) -> float:
    if not pairs:
        return 0.0
    correct = sum(1 for gt, pred, _ in pairs if gt == pred)
    return correct / len(pairs)


def _compute_accuracy_per_field(
    pairs: list[tuple[str, str, float]],
) -> dict[str, float]:
    per_field: dict[str, list[bool]] = defaultdict(list)
    for gt, pred, _ in pairs:
        per_field[gt].append(gt == pred)
    return {field: sum(hits) / len(hits) for field, hits in per_field.items()}


def _compute_confusion_matrix(
    pairs: list[tuple[str, str, float]],
) -> dict[tuple[str, str], int]:
    cm: dict[tuple[str, str], int] = defaultdict(int)
    for gt, pred, _ in pairs:
        cm[(gt, pred)] += 1
    return dict(cm)


def _serialize_confusion_matrix(
    cm: dict[tuple[str, str], int],
) -> list[dict[str, Any]]:
    return [
        {"true": gt, "pred": pred, "count": count}
        for (gt, pred), count in sorted(cm.items())
    ]


def _compute_coverage(classifications: list[ClassificationResult]) -> float:
    if not classifications:
        return 0.0
    n_valid = sum(1 for c in classifications if c.is_valid)
    return n_valid / len(classifications)


def _compute_ece(pairs: list[tuple[str, str, float]], n_bins: int) -> float:
    """ECE (Expected Calibration Error) em n_bins bins."""
    if not pairs:
        return 0.0
    bin_size = 1.0 / n_bins
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for gt, pred, conf in pairs:
        idx = min(int(conf / bin_size), n_bins - 1)
        bins[idx].append((conf, gt == pred))

    total = len(pairs)
    ece = 0.0
    for bin_items in bins:
        if not bin_items:
            continue
        avg_conf = sum(c for c, _ in bin_items) / len(bin_items)
        avg_acc = sum(1 for _, correct in bin_items if correct) / len(bin_items)
        ece += (len(bin_items) / total) * abs(avg_conf - avg_acc)
    return ece


def _compute_recall_precision(
    pairs: list[tuple[str, str, float]],
) -> tuple[dict[str, float], dict[str, float]]:
    tp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)

    all_fields: set[str] = set()
    for gt, pred, _ in pairs:
        all_fields.add(gt)
        all_fields.add(pred)
        if gt == pred:
            tp[gt] += 1
        else:
            fn[gt] += 1
            fp[pred] += 1

    recall: dict[str, float] = {}
    precision: dict[str, float] = {}
    for field in all_fields:
        denom_r = tp[field] + fn[field]
        recall[field] = tp[field] / denom_r if denom_r > 0 else 0.0
        denom_p = tp[field] + fp[field]
        precision[field] = tp[field] / denom_p if denom_p > 0 else 0.0

    return recall, precision


def _compute_accuracy_by_bucket(
    pairs: list[tuple[str, str, float]],
) -> dict[str, float]:
    buckets: dict[str, list[bool]] = {"<0.40": [], "0.40-0.70": [], ">=0.70": []}
    for gt, pred, conf in pairs:
        correct = gt == pred
        if conf < 0.40:
            buckets["<0.40"].append(correct)
        elif conf < 0.70:
            buckets["0.40-0.70"].append(correct)
        else:
            buckets[">=0.70"].append(correct)
    return {
        key: (sum(vals) / len(vals) if vals else 0.0)
        for key, vals in buckets.items()
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_report(run_id: str, eval_dir: Path) -> None:
    """Imprime tabela de EvalReport para um run_id."""
    candidates = list(eval_dir.glob(f"run_{run_id}*.json"))
    if not candidates:
        candidates = list(eval_dir.glob("run_*.json"))
    if not candidates:
        print(f"Nenhum run encontrado em {eval_dir}")
        return

    path = sorted(candidates)[-1] if len(candidates) > 1 else candidates[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    report = EvalReport.model_validate(data)

    print(f"\n=== EvalReport: {path.name} ===")
    print(f"  accuracy_global : {report.accuracy_global:.4f}")
    print(f"  coverage        : {report.coverage:.4f}")
    print(f"  ece             : {report.ece:.4f}")
    print(f"  n_evaluated     : {report.n_evaluated}")
    print(f"  n_excluded_bank : {report.n_excluded_shot_bank}")

    print("\n--- Accuracy por campo ---")
    for field, acc in sorted(report.accuracy_per_field.items()):
        print(f"  {field:10s}: {acc:.4f}")

    print("\n--- Top-5 confusões ---")
    top5 = sorted(report.confusion_matrix_serialized, key=lambda x: x["count"], reverse=True)[:5]
    for entry in top5:
        if entry["true"] != entry["pred"]:
            print(f"  true={entry['true']:10s} pred={entry['pred']:10s} count={entry['count']}")


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Evaluator CLI")
    subparsers = parser.add_subparsers(dest="command")

    report_parser = subparsers.add_parser("report", help="Imprime EvalReport")
    report_parser.add_argument("--run-id", default="", help="Prefixo do run_id")
    report_parser.add_argument(
        "--eval-dir", default="data/eval", help="Diretório com run_*.json"
    )

    args = parser.parse_args()
    if args.command == "report":
        _cli_report(args.run_id, Path(args.eval_dir))
    else:
        parser.print_help()
        sys.exit(1)
