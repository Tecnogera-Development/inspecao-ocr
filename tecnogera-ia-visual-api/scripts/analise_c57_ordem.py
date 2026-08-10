#!/usr/bin/env python3
"""Posição do campo na sequência de captura — F180 antes/depois de set/2025.

SOMENTE LEITURA: usa os caches em `data/`, não toca Dropbox, banco nem LLM.

Complementa `analise_c57_orfao.py`. Se a `c57` do F180 virou `c0`, as duas
ocupam a **mesma posição** na ordem em que o operador tira as fotos (os
vizinhos imediatos são os mesmos). Se a `c57` foi removida e a `c0` é um campo
novo, as posições e os vizinhos diferem.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
from analise_c57_orfao import CACHES, PSV, form_prefix, load_formularios, stream_names  # noqa: E402


def main() -> int:
    from app.services.dropbox import parse_filename

    forms, _ = load_formularios(PSV)
    # cid -> list[(ts, campo)]
    seq: dict[str, list] = defaultdict(list)
    for cache in CACHES:
        for name, _sm in stream_names(cache):
            try:
                p = parse_filename(name)
            except ValueError:
                continue
            if form_prefix(forms.get(p.checklist_id)) != "F180":
                continue
            if p.captured_at is None:
                continue
            seq[p.checklist_id].append((p.captured_at, p.field_name))

    antes: dict[str, Counter] = defaultdict(Counter)
    depois: dict[str, Counter] = defaultdict(Counter)
    rank_antes: dict[str, list] = defaultdict(list)
    rank_depois: dict[str, list] = defaultdict(list)
    for cid, itens in seq.items():
        itens.sort()
        mes = f"{itens[0][0].year:04d}-{itens[0][0].month:02d}"
        if "2025-01" <= mes < "2025-09":
            viz, rk = antes, rank_antes
        elif "2025-11" <= mes <= "2026-07":
            viz, rk = depois, rank_depois
        else:
            continue
        # colapsa repetições consecutivas do mesmo campo
        ordem: list[str] = []
        for _ts, c in itens:
            if not ordem or ordem[-1] != c:
                ordem.append(c)
        for i, c in enumerate(ordem):
            prev = ordem[i - 1] if i else "(inicio)"
            nxt = ordem[i + 1] if i + 1 < len(ordem) else "(fim)"
            viz[c][f"{prev} < {c} < {nxt}"] += 1
            rk[c].append(i / max(len(ordem) - 1, 1))

    def mostra(rotulo: str, viz: dict[str, Counter], rk: dict[str, list], campo: str) -> None:
        if campo not in viz:
            print(f"{rotulo} {campo}: ausente")
            return
        n = sum(viz[campo].values())
        med = sorted(rk[campo])[len(rk[campo]) // 2]
        print(f"{rotulo} {campo}: n={n} posição relativa mediana={med:.2f}")
        for padrao, c in viz[campo].most_common(6):
            print(f"    {100*c/n:5.1f}%  {padrao}")

    for campo in ("c57", "c0", "c53", "c56"):
        mostra("ANTES ", antes, rank_antes, campo)
        mostra("DEPOIS", depois, rank_depois, campo)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
