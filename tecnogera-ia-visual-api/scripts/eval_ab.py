#!/usr/bin/env python3
"""eval_ab.py — harness de eval A/B entre dois commits.

Uso:
    python scripts/eval_ab.py <commit_before> <commit_after> \\
        --checklists 276800,278154,278365 \\
        [--partition data/eval/partition_v2.json] \\
        [--label meu_corte]

Requisitos:
    - Git repo local com os dois commits acessíveis.
    - Stack fora do ar (o script gerencia up/down por commit).
    - docker compose disponível.

Saída:
    data/eval/ab/<timestamp>_<label>.json   — métricas brutas
    data/eval/ab/<timestamp>_<label>.md     — comparativo markdown
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

_API_BASE = "http://localhost:8000"
_POLL_INTERVAL = 15


# ── pipeline ──────────────────────────────────────────────────────────────────


def _docker_compose(args: list[str]) -> None:
    subprocess.run(["docker", "compose", *args], check=True)


def _wait_api_ready(api_base: str, retries: int = 30, delay: int = 5) -> None:
    url = f"{api_base}/health"
    for _ in range(retries):
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(delay)
    raise RuntimeError(f"API não respondeu em {retries * delay}s")


def _run_pipeline(checklist_id: str, api_base: str) -> str:
    resp = httpx.post(
        f"{api_base}/api/v1/pipeline/run",
        json={"checklist_id": checklist_id},
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json()["job_id"])


def _poll_job(job_id: str, api_base: str) -> dict:
    url = f"{api_base}/api/v1/pipeline/jobs/{job_id}"
    while True:
        time.sleep(_POLL_INTERVAL)
        resp = httpx.get(url, timeout=15)
        resp.raise_for_status()
        job = resp.json()
        if job.get("status") in {"done", "failed"}:
            return job


def _find_latest_eval(eval_dir: Path, since_ts: float) -> dict | None:
    candidates = [
        p for p in eval_dir.glob("run_*.json")
        if p.stat().st_mtime > since_ts
    ]
    if not candidates:
        return None
    path = max(candidates, key=lambda p: p.stat().st_mtime)
    return json.loads(path.read_text())


def _eval_commit(
    commit: str,
    checklist_ids: list[str],
    api_base: str,
    eval_dir: Path,
) -> dict:
    """Faz checkout do commit, sobe stack, roda checklists, retorna métricas."""
    print(f"\n══ Checkout {commit[:8]} ══")
    subprocess.run(["git", "stash"], check=False)
    subprocess.run(["git", "checkout", commit], check=True)

    _docker_compose(["build", "--quiet", "api"])
    _docker_compose(["up", "-d"])
    _wait_api_ready(api_base)

    results = []
    for cid in checklist_ids:
        print(f"  → checklist {cid}")
        try:
            before_ts = time.time()
            job_id = _run_pipeline(cid, api_base)
            job = _poll_job(job_id, api_base)
            status = job.get("status")
            eval_data = _find_latest_eval(eval_dir, before_ts) if status == "done" else None
            results.append({
                "checklist_id": cid,
                "status": status,
                "error": job.get("error"),
                "accuracy_global": eval_data.get("accuracy_global") if eval_data else None,
                "n_evaluated": eval_data.get("n_evaluated") if eval_data else None,
                "ece": eval_data.get("ece") if eval_data else None,
            })
        except Exception as exc:
            print(f"  ✗ erro em {cid}: {exc}", file=sys.stderr)
            results.append({"checklist_id": cid, "status": "error", "error": str(exc)})

    done = [r for r in results if r.get("accuracy_global") is not None]
    accuracy_agg = (
        sum(r["accuracy_global"] for r in done) / len(done) if done else None
    )

    return {
        "commit": commit,
        "per_checklist": results,
        "accuracy_global_avg": accuracy_agg,
    }


# ── markdown ──────────────────────────────────────────────────────────────────


def _render_md(before: dict, after: dict, label: str) -> str:
    def _acc(r: dict) -> str:
        v = r.get("accuracy_global_avg")
        return f"{v:.4f}" if v is not None else "N/A"

    rows = []
    for r_b, r_a in zip(before["per_checklist"], after["per_checklist"]):
        cid = r_b["checklist_id"]
        acc_b = r_b.get("accuracy_global", "N/A")
        acc_a = r_a.get("accuracy_global", "N/A")
        delta = ""
        if isinstance(acc_b, float) and isinstance(acc_a, float):
            d = acc_a - acc_b
            delta = f"+{d:.4f}" if d >= 0 else f"{d:.4f}"
        rows.append(f"| {cid} | {acc_b!s:>8} | {acc_a!s:>8} | {delta:>8} |")

    rows_str = "\n".join(rows)
    return f"""# Eval A/B — {label}

**Antes**: `{before['commit'][:12]}`
**Depois**: `{after['commit'][:12]}`
**Data**: {datetime.now(UTC).isoformat()}

## Accuracy global (avg)

| Versão | Accuracy |
|--------|----------|
| Antes  | {_acc(before)} |
| Depois | {_acc(after)} |

## Por checklist

| checklist_id | Antes | Depois | Δ |
|---|---|---|---|
{rows_str}

## Gate

- Accuracy global ≥ 0.90: {"✓" if (after.get("accuracy_global_avg") or 0) >= 0.90 else "✗ FALHOU"}
"""


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval A/B entre dois commits")
    parser.add_argument("commit_before", help="Commit ou tag antes da mudança")
    parser.add_argument("commit_after", help="Commit ou tag depois da mudança")
    parser.add_argument(
        "--checklists",
        default="276800,278154,278365",
        help="checklist_ids separados por vírgula",
    )
    parser.add_argument(
        "--partition",
        default="data/eval/partition_v2.json",
        help="Partition JSON para anti-leakage",
    )
    parser.add_argument("--label", default="corte", help="Label do corte (ex: a1, a2, a3)")
    parser.add_argument("--api", default=_API_BASE, help="URL base da API")
    args = parser.parse_args()

    checklist_ids = [c.strip() for c in args.checklists.split(",")]
    eval_dir = Path("data/eval")
    out_dir = Path("data/eval/ab")
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out_json = out_dir / f"{ts}_{args.label}.json"
    out_md = out_dir / f"{ts}_{args.label}.md"

    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    try:
        before = _eval_commit(args.commit_before, checklist_ids, args.api, eval_dir)
        after = _eval_commit(args.commit_after, checklist_ids, args.api, eval_dir)
    finally:
        print(f"\n→ Restaurando branch {current_branch}...")
        subprocess.run(["git", "checkout", current_branch], check=False)
        subprocess.run(["git", "stash", "pop"], check=False)
        _docker_compose(["up", "-d"])

    result = {
        "timestamp": ts,
        "label": args.label,
        "checklists": checklist_ids,
        "before": before,
        "after": after,
    }
    out_json.write_text(json.dumps(result, indent=2))
    print(f"\n→ Métricas salvas em {out_json}")

    md = _render_md(before, after, args.label)
    out_md.write_text(md)
    print(f"→ Comparativo salvo em {out_md}")
    print(md)

    acc = after.get("accuracy_global_avg")
    if acc is not None and acc < 0.90:
        print(f"\n✗ GATE FALHOU: accuracy_global_avg={acc:.4f} < 0.90", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
