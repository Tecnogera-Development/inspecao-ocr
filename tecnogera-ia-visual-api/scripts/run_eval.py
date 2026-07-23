#!/usr/bin/env python3
"""run_eval.py — dispara o pipeline em um checklist e avalia o resultado.

Uso:
    python scripts/run_eval.py --checklist 276800
    python scripts/run_eval.py --checklist 276800 --partition data/eval/partition_v2.json

Saída: data/eval/run_<timestamp>.json (sobreponível via --output)

Requisitos: stack up (make up), .env com variáveis configuradas.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

_API_BASE = "http://localhost:8000"
_POLL_INTERVAL = 10  # segundos


def _run_checklist(checklist_id: str, api_base: str) -> str:
    """Dispara pipeline e retorna job_id."""
    resp = httpx.post(
        f"{api_base}/api/v1/pipeline/run",
        json={"checklist_id": checklist_id},
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json()["job_id"])


def _poll_job(job_id: str, api_base: str, poll_interval: int = _POLL_INTERVAL) -> dict:
    """Aguarda job concluir e retorna o JSON completo do job."""
    url = f"{api_base}/api/v1/pipeline/jobs/{job_id}"
    while True:
        time.sleep(poll_interval)
        resp = httpx.get(url, timeout=15)
        resp.raise_for_status()
        job = resp.json()
        status = job.get("status", "?")
        print(f"  status={status}", flush=True)
        if status in {"done", "failed"}:
            return job


def _find_latest_eval(eval_dir: Path, before_ts: float) -> Path | None:
    """Retorna o run_*.json mais recente criado após before_ts."""
    candidates = [
        p for p in eval_dir.glob("run_*.json")
        if p.stat().st_mtime > before_ts
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa pipeline + eval para um checklist")
    parser.add_argument("--checklist", required=True, help="checklist_id numérico")
    parser.add_argument(
        "--partition",
        default="data/eval/partition_v2.json",
        help="Caminho para partition JSON (default: data/eval/partition_v2.json)",
    )
    parser.add_argument("--api", default=_API_BASE, help="URL base da API")
    parser.add_argument("--output", help="Caminho de saída do JSON de eval (opcional)")
    args = parser.parse_args()

    checklist_id = args.checklist
    partition_path = Path(args.partition) if args.partition else None
    eval_dir = Path("data/eval")

    print(f"→ Disparando pipeline para checklist {checklist_id}...")
    job_id = _run_checklist(checklist_id, args.api)
    print(f"  job_id={job_id}")

    before_ts = time.time()
    print("→ Aguardando conclusão...")
    job = _poll_job(job_id, args.api)

    status = job.get("status")
    if status == "failed":
        print(f"✗ Job falhou: {job.get('error')}", file=sys.stderr)
        return 1

    eval_path = _find_latest_eval(eval_dir, before_ts)
    if eval_path is None:
        print("✗ Nenhum arquivo de eval encontrado após o job", file=sys.stderr)
        return 1

    if args.output:
        import shutil
        dest = Path(args.output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(eval_path, dest)
        eval_path = dest

    data = json.loads(eval_path.read_text())
    print(f"\n→ Resultado salvo em {eval_path}")
    print(f"  accuracy_global : {data.get('accuracy_global', '?'):.4f}")
    print(f"  n_evaluated     : {data.get('n_evaluated', '?')}")
    print(f"  ece             : {data.get('ece', '?'):.4f}")
    if partition_path:
        print(f"  partition       : {partition_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
