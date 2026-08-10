#!/usr/bin/env python3
"""Para onde foi a foto traseira quando a `c57` sumiu do F180? (ticket 16)

=============================== SOMENTE LEITURA ===============================
Não toca no Dropbox, não toca no banco, não chama LLM. Lê apenas os caches de
listagem já gravados em `data/` e o dump PSV da view `dbo.checklist_produto`.
===============================================================================

O ticket 01 datou a queda da `c57` no F180 (set/2025) e concluiu "renumeração".
A tabela dele cobre só `c53`–`c57` e mostra `c53`–`c56` estáveis — o que **não**
sustenta renumeração. Este script levanta o **conjunto completo** de códigos
`cN` por mês e a **contagem média de fotos por checklist**, que é o teste
decisivo: se a média caiu ~1 foto, a vista saiu; se ficou estável, a vista
continua sendo tirada sob outro código.

Uso:
    python3 scripts/analise_c57_orfao.py
    python3 scripts/analise_c57_orfao.py --json-out /tmp/saida.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CACHES = (
    _REPO_ROOT / "data" / "survey_c54_c57_listing.json",
    _REPO_ROOT / "data" / "survey_c54_c57_resto.json",
)
PSV = _REPO_ROOT / "data" / "checklist_produto_formularios.psv"

_RECORD = re.compile(
    r'"name":\s*"((?:[^"\\]|\\.)*)",\s*"server_modified":\s*"([^"]*)"'
)
_FORM_PREFIX = re.compile(r"^(F\d{3})")
_CHUNK = 1 << 23  # 8 MiB


def _unescape(raw: str) -> str:
    if "\\" in raw:
        return json.loads(f'"{raw}"')
    return raw


def stream_names(path: Path):
    """Emite (name, server_modified) de um cache de listagem, em streaming."""
    tail = ""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf = tail + chunk
            last = 0
            for m in _RECORD.finditer(buf):
                yield _unescape(m.group(1)), m.group(2)
                last = m.end()
            tail = buf[last:][-4096:] if last else buf[-4096:]
    for m in _RECORD.finditer(tail):
        yield _unescape(m.group(1)), m.group(2)


def load_formularios(psv: Path) -> tuple[dict[str, str], dict[str, str]]:
    forms: dict[str, str] = {}
    meses: dict[str, str] = {}
    for line in psv.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("|")
        if len(parts) < 2:
            continue
        cid = parts[0].strip()
        if not cid.isdigit():
            continue
        forms[cid] = parts[1].strip()
        if len(parts) >= 4 and parts[3].strip():
            meses[cid] = parts[3].strip()
    return forms, meses


def form_prefix(formulario: str | None) -> str:
    if formulario is None:
        return "(sem linha no DB)"
    m = _FORM_PREFIX.match(formulario)
    if m:
        return m.group(1)
    return "(vazio)" if formulario in {"(vazio)", ""} else "(outro)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--dump-checklists", type=Path, default=None)
    ap.add_argument("--dump-names", default=None, help="dumpa nomes de arquivo do campo cN")
    ap.add_argument("--forms", default="F180,F038")
    args = ap.parse_args()

    from app.services.dropbox import parse_filename

    forms, meses_db = load_formularios(PSV)
    alvo = tuple(args.forms.split(","))

    # cid -> [set(fields), n_files, min_captured, min_server_modified]
    ck: dict[str, list[Any]] = {}
    nomes: list[tuple[str, str]] = []
    total = ignored = 0
    for cache in CACHES:
        if not cache.exists():
            print(f"[erro] cache ausente: {cache}", file=sys.stderr)
            return 1
        n0 = total
        for name, sm in stream_names(cache):
            total += 1
            try:
                p = parse_filename(name)
            except ValueError:
                ignored += 1
                continue
            cid = p.checklist_id
            # só carrega checklists dos formulários-alvo — economiza memória
            if form_prefix(forms.get(cid)) not in alvo:
                continue
            e = ck.get(cid)
            if e is None:
                e = ck[cid] = [set(), 0, None, None, {}]
            if args.dump_names and p.field_name == args.dump_names:
                nomes.append((cid, name))
            e[0].add(p.field_name)
            e[4][p.field_name] = e[4].get(p.field_name, 0) + 1
            e[1] += 1
            if p.captured_at is not None and (e[2] is None or p.captured_at < e[2]):
                e[2] = p.captured_at
            if sm and (e[3] is None or sm < e[3]):
                e[3] = sm
        print(f"[cache] {cache.name}: {total - n0} arquivos", file=sys.stderr)
    print(
        f"[total] {total} arquivos, {ignored} sem parse, "
        f"{len(ck)} checklists nos formulários {alvo}",
        file=sys.stderr,
    )

    def ref_month(e: list[Any]) -> str | None:
        dt: datetime | None = e[2]
        if dt is None and e[3]:
            try:
                dt = datetime.fromisoformat(e[3])
            except ValueError:
                return None
        if dt is None:
            return None
        return f"{dt.year:04d}-{dt.month:02d}"

    out: dict[str, Any] = {}
    for code in alvo:
        # mes -> {n, files, Counter(field->checklists)}
        meses: dict[str, dict[str, Any]] = {}
        for cid, e in ck.items():
            if form_prefix(forms.get(cid)) != code:
                continue
            mes = ref_month(e)
            if mes is None or mes < "2025-01" or mes > "2026-07":
                continue
            row = meses.setdefault(mes, {"n": 0, "files": 0, "campos": Counter()})
            row["n"] += 1
            row["files"] += e[1]
            for f in e[0]:
                row["campos"][f] += 1
        out[code] = {
            m: {
                "n": r["n"],
                "files": r["files"],
                "media_fotos": r["files"] / r["n"] if r["n"] else 0.0,
                "campos": dict(r["campos"]),
            }
            for m, r in sorted(meses.items())
        }

    if args.dump_checklists:
        recs = [
            {
                "cid": cid,
                "form": form_prefix(forms.get(cid)),
                "mes": ref_month(e),
                "n_files": e[1],
                "fields": sorted(e[0]),
                "por_campo": e[4],
            }
            for cid, e in ck.items()
        ]
        args.dump_checklists.write_text(json.dumps(recs, ensure_ascii=False))
        print(f"[ok] {len(recs)} checklists em {args.dump_checklists}", file=sys.stderr)
    if args.dump_names:
        for cid, name in nomes[:200000]:
            print(f"{cid}\t{name}", file=sys.stderr)

    print(json.dumps(out, ensure_ascii=False)[:200], file=sys.stderr)
    if args.json_out:
        args.json_out.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"[ok] gravado em {args.json_out}", file=sys.stderr)
    else:
        json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
