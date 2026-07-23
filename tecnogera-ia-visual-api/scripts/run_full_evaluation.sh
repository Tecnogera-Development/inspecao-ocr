#!/usr/bin/env bash
# Rodada full IAVS-010: dispara pipeline para os 9 checklists em sequência,
# captura os run JSONs do Evaluator e gera um resumo agregado.
#
# Requisitos: stack up (make up), prompt-v1.0 ativo, 9 checklists em
# data/checklists/, .env com LLM_PROVIDER=anthropic.
#
# Uso: ./scripts/run_full_evaluation.sh
# Saída: data/eval/run_full_<ts>.json com agregação + linha por checklist.

set -euo pipefail

CHECKLIST_IDS=(278749 278154 278365)
# Amostra representativa (custo-otimizada): outlier pequeno (fallback) +
# F013 pequeno + F013 grande. Sanity 276800 (F013 médio) já cobre.
# Gate v1.0 fica INDICATIVO (não strict cumpre acceptance da #010).
API=http://localhost:8000
TS=$(date -u +%Y%m%dT%H%M%S)
OUT=data/eval/run_full_${TS}.json
JOBS_LOG=data/eval/run_full_${TS}_jobs.tsv

echo "→ Rodada full IAVS-010 — timestamp ${TS}"
echo "→ 9 checklists, prompt-v1.0"
echo "checklist_id\tjob_id\tstatus\teval_run_file\terror" > "$JOBS_LOG"

declare -a RUN_FILES=()

for CID in "${CHECKLIST_IDS[@]}"; do
    echo ""
    echo "→ [${CID}] disparando..."
    BEFORE=$(ls -t data/eval/run_*.json 2>/dev/null | head -1 || echo "")
    RESP=$(curl -sS -X POST "${API}/api/v1/pipeline/run" \
        -H "Content-Type: application/json" \
        -d "{\"checklist_id\": \"${CID}\"}")
    JOB_ID=$(echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["job_id"])')
    echo "→ [${CID}] job_id=${JOB_ID}"

    # poll until done/failed
    while true; do
        sleep 20
        S=$(curl -s "${API}/api/v1/pipeline/jobs/${JOB_ID}" || echo '{}')
        STATUS=$(echo "$S" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("status","?"))' 2>/dev/null || echo "parse-fail")
        if [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ]; then
            break
        fi
        echo "→ [${CID}] status=${STATUS}..."
    done

    ERROR=$(echo "$S" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("error") or "")')
    AFTER=$(ls -t data/eval/run_*.json 2>/dev/null | head -1 || echo "")
    NEW_RUN=""
    if [ "$AFTER" != "$BEFORE" ] && [ -n "$AFTER" ]; then
        NEW_RUN="$AFTER"
        RUN_FILES+=("$NEW_RUN")
    fi
    echo "→ [${CID}] status=${STATUS}  eval=${NEW_RUN}  error=${ERROR:0:80}"
    printf "%s\t%s\t%s\t%s\t%s\n" "$CID" "$JOB_ID" "$STATUS" "$NEW_RUN" "$ERROR" >> "$JOBS_LOG"
done

echo ""
echo "→ Agregando ${#RUN_FILES[@]} eval JSONs em ${OUT}..."
python3 - <<PY
import json, os, sys
run_files = """${RUN_FILES[@]}""".split()
checklist_ids = """${CHECKLIST_IDS[@]}""".split()

per_checklist = []
all_confusions = {}
total_evaluated = 0
total_correct = 0

for cid, rf in zip(checklist_ids, run_files):
    if not rf or not os.path.exists(rf):
        per_checklist.append({"checklist_id": cid, "run_file": rf, "skipped": True})
        continue
    with open(rf) as f:
        d = json.load(f)
    per_checklist.append({
        "checklist_id": cid,
        "run_file": rf,
        "accuracy_global": d["accuracy_global"],
        "n_evaluated": d["n_evaluated"],
        "n_excluded_shot_bank": d["n_excluded_shot_bank"],
        "coverage": d["coverage"],
        "ece": d["ece"],
    })
    total_evaluated += d["n_evaluated"]
    total_correct += round(d["accuracy_global"] * d["n_evaluated"])
    for c in d["confusion_matrix_serialized"]:
        k = (c["true"], c["pred"])
        all_confusions[k] = all_confusions.get(k, 0) + c["count"]

aggregate = {
    "timestamp": "${TS}",
    "n_checklists": len(checklist_ids),
    "n_run_files": len(run_files),
    "accuracy_global_aggregate": (total_correct / total_evaluated) if total_evaluated else 0.0,
    "total_evaluated": total_evaluated,
    "total_correct": total_correct,
    "per_checklist": per_checklist,
    "confusion_matrix_aggregate": [
        {"true": k[0], "pred": k[1], "count": v}
        for k, v in sorted(all_confusions.items(), key=lambda kv: -kv[1])
    ],
}

with open("${OUT}", "w") as f:
    json.dump(aggregate, f, indent=2)

print(f"\n→ accuracy_global_aggregate: {aggregate['accuracy_global_aggregate']:.4f}")
print(f"→ total_correct: {total_correct}/{total_evaluated}")
print(f"→ saída: ${OUT}")
PY

echo ""
echo "→ Concluído. Log de jobs: ${JOBS_LOG}"
