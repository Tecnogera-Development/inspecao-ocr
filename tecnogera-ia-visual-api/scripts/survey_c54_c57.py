#!/usr/bin/env python3
"""Mede a incidência real dos campos c54–c57 nos checklists do Dropbox.

=============================== SOMENTE LEITURA ===============================
Este script é **read-only no Dropbox**. Princípio permanente do projeto: o
Dropbox da Tecnogera é fonte, nunca destino. Nenhuma operação de escrita ou
remoção é permitida, em hipótese alguma.

  PERMITIDO : files_list_folder, files_list_folder_continue,
              files_get_metadata, files_search_v2, files_download
  PROIBIDO  : files_delete*, files_permanently_delete, files_upload,
              files_move, files_copy, files_create_folder, files_restore
              — e qualquer outra chamada que altere estado no Dropbox.

A restrição não é só documental: o cliente do SDK é embrulhado em
``_ReadOnlyDropbox`` (abaixo), que levanta ``PermissionError`` se qualquer
método fora da allowlist for invocado. Na prática este script só chama
``files_list_folder`` e ``files_list_folder_continue``.
===============================================================================

Contexto: o escopo fechado do MVP escolheu o filtro
**estrito** — só processa checklist que tenha c54 E c55 E c56 E c57. A única
evidência disponível (9 checklists em ``docs/exploracao/catalog.json``) sugeria
que isso processaria zero. Este script mede numa amostra grande e real.

Não chama LLM e não baixa imagem: é contagem de nomes de arquivo.

Uso:
    python scripts/survey_c54_c57.py                     # janela padrão: 90 dias
    python scripts/survey_c54_c57.py --since-days 180
    python scripts/survey_c54_c57.py --all               # sem recorte de data
    python scripts/survey_c54_c57.py --cache data/survey_listing.json
    python scripts/survey_c54_c57.py --from-cache        # não toca no Dropbox

Flags principais:
    --since-days N   Janela em dias (default 90). Corta por data do arquivo.
    --all            Ignora a janela — varre todo o histórico listado.
    --root PATH      Raiz no Dropbox (default: DROPBOX_ROOT_PATH, /Sisloc).
    --env-file PATH  Carrega variáveis extras antes de instanciar Settings.
    --cache PATH     Grava/lê o resultado bruto da listagem (evita re-varrer).
    --from-cache     Usa apenas o cache; falha se não existir.
    --output-dir DIR Onde gravar o relatório (default: docs/exploracao).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Garante que o pacote `app` é encontrado quando executado de qualquer dir
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TARGET_FIELDS: tuple[str, ...] = ("c54", "c55", "c56", "c57")

_VALID_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp"})

_MAX_RATE_LIMIT_RETRIES = 6

# Única allowlist de métodos do SDK que este script pode tocar. Tudo o que não
# está aqui — em especial qualquer coisa que escreva ou remova — é barrado em
# tempo de execução por ``_ReadOnlyDropbox``.
_ALLOWED_DROPBOX_CALLS: frozenset[str] = frozenset(
    {
        "files_list_folder",
        "files_list_folder_continue",
        "files_get_metadata",
        "files_search_v2",
        "files_search_continue_v2",
        "files_download",
    }
)


class _ReadOnlyDropbox:
    """Proxy que só deixa passar chamadas de leitura ao SDK do Dropbox.

    Guarda de segurança contra escrita acidental: o Dropbox da Tecnogera é
    fonte read-only. Qualquer método fora de ``_ALLOWED_DROPBOX_CALLS``
    (``files_upload``, ``files_delete_v2``, ``files_move``, ...) levanta
    ``PermissionError`` antes de qualquer tráfego de rede.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        if name not in _ALLOWED_DROPBOX_CALLS:
            raise PermissionError(
                f"chamada '{name}' bloqueada: survey_c54_c57.py é read-only no Dropbox "
                f"(permitidas: {', '.join(sorted(_ALLOWED_DROPBOX_CALLS))})"
            )
        return getattr(self._client, name)


# --------------------------------------------------------------------------- #
# coleta
# --------------------------------------------------------------------------- #
def _load_env_file(path: Path) -> None:
    """Carrega KEY=VALUE de um arquivo para os.environ (não sobrescreve)."""
    if not path.exists():
        print(f"[aviso] env-file não encontrado: {path}", file=sys.stderr)
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


#: Quantas vezes o Dropbox devolveu 429 nesta execução — dado operacional que
#: o ticket 07 (cron de 30 min) precisa para dimensionar a ingestão.
RATE_LIMIT_HITS = 0


def _call_with_backoff(fn, *args: Any, **kwargs: Any) -> Any:
    """Executa uma chamada do SDK tolerando 429 (rate limit) com backoff.

    Respeita o ``Retry-After`` que o Dropbox devolve (``exc.backoff``); só cai
    no backoff exponencial próprio quando o servidor não informa o intervalo.
    """
    global RATE_LIMIT_HITS  # noqa: PLW0603 — contador de execução do script
    from dropbox.exceptions import RateLimitError  # type: ignore[import-untyped]

    delay = 2.0
    for attempt in range(_MAX_RATE_LIMIT_RETRIES):
        try:
            return fn(*args, **kwargs)
        except RateLimitError as exc:
            RATE_LIMIT_HITS += 1
            wait = float(getattr(exc, "backoff", None) or delay)
            print(
                f"[rate-limit] 429 do Dropbox; aguardando {wait:.0f}s "
                f"(tentativa {attempt + 1}/{_MAX_RATE_LIMIT_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(wait)
            delay = min(delay * 2, 60.0)
    raise RuntimeError("rate limit do Dropbox persistente após múltiplas tentativas")


def _read_cache(cache_path: Path) -> dict[str, Any] | None:
    """Lê o cache local da listagem; devolve ``None`` se ausente/ilegível."""
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[aviso] cache ilegível ({exc})", file=sys.stderr)
        return None
    if not isinstance(data, dict) or "segments" not in data:
        return None
    return data


def _write_cache(cache_path: Path, state: dict[str, Any]) -> None:
    """Grava o checkpoint da varredura.

    ESCRITA **LOCAL**, em disco — jamais no Dropbox. Usa arquivo temporário +
    rename para que uma interrupção no meio do dump não deixe cache truncado.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_path)


def discover_scan_paths(client: Any, root: str, *, subdir: str = "Checklist") -> list[str]:
    """Descobre os subtrees a varrer: ``{root}/{filial}/{subdir}``.

    Varrer ``/Sisloc`` inteiro é inviável (medido: >4M arquivos, ~1150 arq/s).
    As fotos de checklist vivem só sob ``{filial}/Checklist``; ``OM``,
    ``Locacao`` e ``Aplicativos`` são ruído. Restringir a árvore é o que torna
    a medição possível em tempo útil.
    """
    from dropbox.files import FolderMetadata  # type: ignore[import-untyped]

    norm_root = "/" + root.strip("/")
    result = _call_with_backoff(client.files_list_folder, norm_root, recursive=False)
    entries = list(result.entries)
    while result.has_more:
        result = _call_with_backoff(client.files_list_folder_continue, result.cursor)
        entries.extend(result.entries)

    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, FolderMetadata):
            continue
        if entry.name.startswith("_"):
            continue
        paths.append(f"{norm_root}/{entry.name}/{subdir}")
    return sorted(paths)


def _scan_segment(
    client: Any,
    path: str,
    *,
    state: dict[str, Any],
    cache_path: Path,
    checkpoint_every: int,
    deadline: float | None,
    verbose: bool,
) -> None:
    """Varre um subtree, retomando do cursor salvo e checkpointando.

    Atualiza ``state["segments"][path]`` in-place. Marca ``complete`` quando o
    Dropbox sinaliza fim; para com ``complete=False`` se o orçamento de tempo
    (``deadline``) estourar — o cursor fica salvo para a próxima execução.
    """
    from dropbox.files import FileMetadata  # type: ignore[import-untyped]

    seg = state["segments"].setdefault(
        path, {"cursor": None, "complete": False, "pages": 0, "files": []}
    )
    if seg["complete"]:
        if verbose:
            print(f"  [pulado] {path} (já completo)", file=sys.stderr)
        return

    files: list[dict[str, Any]] = seg["files"]
    cursor: str | None = seg["cursor"]
    pages: int = seg["pages"]

    try:
        if cursor is None:
            result = _call_with_backoff(
                client.files_list_folder, path, recursive=True, limit=2000
            )
        else:
            if verbose:
                print(
                    f"  [retomando] {path}: {pages} páginas, {len(files)} imagens",
                    file=sys.stderr,
                )
            result = _call_with_backoff(client.files_list_folder_continue, cursor)
    except Exception as exc:  # noqa: BLE001
        # Filial sem a pasta esperada não é erro fatal — só não tem checklist.
        print(f"  [ignorado] {path}: {type(exc).__name__}", file=sys.stderr)
        seg["complete"] = True
        seg["missing"] = True
        return

    prefix_len = len(path) + 1
    while True:
        pages += 1
        for entry in result.entries:
            if not isinstance(entry, FileMetadata):
                continue
            if Path(entry.name).suffix.lower() not in _VALID_EXTENSIONS:
                continue
            fpath = entry.path_display or entry.path_lower
            rel = fpath[prefix_len:]
            if rel.split("/", 1)[0].startswith("_"):
                continue
            files.append(
                {
                    "path": fpath,
                    "name": entry.name,
                    "server_modified": entry.server_modified.isoformat()
                    if entry.server_modified
                    else None,
                }
            )
        seg["pages"] = pages
        if not result.has_more:
            seg["cursor"] = None
            seg["complete"] = True
            _write_cache(cache_path, state)
            if verbose:
                print(
                    f"  [ok] {path}: {pages} páginas, {len(files)} imagens (completo)",
                    file=sys.stderr,
                )
            return

        cursor = result.cursor
        seg["cursor"] = cursor
        if pages % checkpoint_every == 0:
            state["rate_limit_hits"] = RATE_LIMIT_HITS
            _write_cache(cache_path, state)
            if verbose:
                print(
                    f"  [checkpoint] {path}: {pages} páginas, {len(files)} imagens",
                    file=sys.stderr,
                )
        if deadline is not None and time.monotonic() > deadline:
            state["rate_limit_hits"] = RATE_LIMIT_HITS
            _write_cache(cache_path, state)
            if verbose:
                print(
                    f"  [tempo esgotado] {path}: {pages} páginas, {len(files)} imagens "
                    "— cursor salvo, rode de novo para continuar",
                    file=sys.stderr,
                )
            return
        result = _call_with_backoff(client.files_list_folder_continue, cursor)


def collect_listing(
    root: str,
    *,
    cache_path: Path,
    scan_paths: list[str] | None = None,
    checkpoint_every: int = 20,
    time_budget_s: float | None = None,
    verbose: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Varre os subtrees de checklist e devolve ``(arquivos, cobertura)``.

    Usa ``files_list_folder(recursive=True)`` com paginação por cursor —
    ``files_search_v2`` tem teto de resultados e não serve para censo. Cada
    subtree tem seu próprio cursor persistido, então uma execução interrompida
    é retomada em vez de reiniciada.

    ``cobertura`` descreve honestamente o que foi coberto: quais subtrees
    ficaram completos, quais ficaram parciais e quantos 429 aconteceram.
    """
    from app.services.dropbox import DropboxService

    service = DropboxService()
    # Nunca usar o cliente cru: o proxy read-only barra qualquer escrita.
    client = _ReadOnlyDropbox(service._client)  # noqa: SLF001 — script de exploração

    state = _read_cache(cache_path)
    if state is None or state.get("root") != root:
        state = {"root": root, "segments": {}, "rate_limit_hits": 0}

    if scan_paths is None:
        scan_paths = discover_scan_paths(client, root)
    if verbose:
        print(f"[dropbox] {len(scan_paths)} subtrees a varrer sob {root}", file=sys.stderr)

    deadline = time.monotonic() + time_budget_s if time_budget_s else None

    for path in scan_paths:
        if deadline is not None and time.monotonic() > deadline:
            if verbose:
                print("[tempo esgotado] parando antes de " + path, file=sys.stderr)
            break
        _scan_segment(
            client,
            path,
            state=state,
            cache_path=cache_path,
            checkpoint_every=checkpoint_every,
            deadline=deadline,
            verbose=verbose,
        )

    state["rate_limit_hits"] = RATE_LIMIT_HITS
    _write_cache(cache_path, state)
    return _flatten_cache(state, scan_paths)


def _flatten_cache(
    state: dict[str, Any], scan_paths: list[str] | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Achata o cache por segmento em (lista de arquivos, cobertura)."""
    files: list[dict[str, Any]] = []
    completos: list[str] = []
    parciais: list[str] = []
    ausentes: list[str] = []
    for path, seg in sorted(state.get("segments", {}).items()):
        files.extend(seg.get("files") or [])
        if seg.get("missing"):
            ausentes.append(path)
        elif seg.get("complete"):
            completos.append(path)
        else:
            parciais.append(path)
    total_alvo = len(scan_paths) if scan_paths else len(state.get("segments", {}))
    cobertura = {
        "subtrees_alvo": total_alvo,
        "subtrees_completos": completos,
        "subtrees_parciais": parciais,
        "subtrees_ausentes": ausentes,
        "rate_limit_hits": int(state.get("rate_limit_hits") or 0),
        "complete": not parciais and len(completos) + len(ausentes) >= total_alvo,
    }
    return files, cobertura


# --------------------------------------------------------------------------- #
# formulários (enriquecimento vindo do SQL Server — SOMENTE SELECT)
# --------------------------------------------------------------------------- #
#: Conjunto alternativo de 4 vistas. Descoberto empiricamente: a partir da
#: revisão de set/2025 o F180 (visita GMG, maior volume) deixou de usar `c57` e
#: passou a `c53..c56`. Medir os dois conjuntos lado a lado é o que revela que
#: o problema não é "falta vista", é "o número do campo mudou".
ALT_FIELDS: tuple[str, ...] = ("c53", "c54", "c55", "c56")

_FORM_PREFIX = re.compile(r"^(F\d{3})")


def load_formularios(psv_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Carrega ``codigo_checklist -> (formulario, mês)`` de um dump do SQL Server.

    O arquivo é a saída de (SOMENTE SELECT — o banco nunca é escrito)::

        sqlcmd -S ... -C -s "|" -W -h -1 \
          -Q "SET NOCOUNT ON; SELECT codigo_checklist,
                ISNULL(NULLIF(LTRIM(RTRIM(formulario)),''),'(vazio)'),
                ISNULL(filial,''),
                ISNULL(CONVERT(char(7), data_conclusao_checklist, 126),'')
              FROM dbo.checklist_produto"

    ``formulario`` é ``varchar(30)`` e vem truncado no banco, então o
    agrupamento é sempre pelo prefixo ``F0NN`` — nunca pela string inteira.
    O mês vem do banco (``data_conclusao_checklist``) para que taxa medida e
    volume do parque compartilhem a mesma base de data.
    """
    forms: dict[str, str] = {}
    meses: dict[str, str] = {}
    if not psv_path.exists():
        print(f"[aviso] dump de formulários não encontrado: {psv_path}", file=sys.stderr)
        return forms, meses
    for line in psv_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.rstrip("\n").split("|")
        if len(parts) < 2:
            continue
        cid = parts[0].strip()
        if not cid.isdigit():
            continue
        forms[cid] = parts[1].strip()
        if len(parts) >= 4 and parts[3].strip():
            meses[cid] = parts[3].strip()
    return forms, meses


def projetar_parque(
    checklists: list[dict[str, Any]],
    formularios: dict[str, str],
    meses: dict[str, str],
    form_codes: tuple[str, ...],
    *,
    required: tuple[str, ...] = ("c54", "c55", "c56"),
    since_month: str = "2026-01",
) -> dict[str, Any]:
    """Extrapola o volume do parque a partir da taxa medida na amostra.

    A varredura do Dropbox cobre parte das filiais; a view
    ``dbo.checklist_produto`` cobre **todas**. Então mede-se a taxa de
    checklists com as vistas obrigatórias *dentro da amostra* e aplica-se essa
    taxa ao total do parque, formulário a formulário. Volume e taxa usam a
    mesma coluna de data (``data_conclusao_checklist``), evitando comparar
    data-da-foto com data-de-conclusão.
    """
    amostra = {e["checklist_id"]: e["fields"] for e in checklists}
    req = set(required)

    taxas: dict[str, dict[str, Any]] = {}
    for code in form_codes:
        ids = [
            cid
            for cid, form in formularios.items()
            if form_prefix(form) == code and meses.get(cid, "") >= since_month
        ]
        na_amostra = [cid for cid in ids if cid in amostra]
        com = [cid for cid in na_amostra if req <= amostra[cid]]
        taxas[code] = {
            "parque": len(ids),
            "amostra": len(na_amostra),
            "com_vistas": len(com),
            "taxa": (len(com) / len(na_amostra)) if na_amostra else 0.0,
            "cobertura": (len(na_amostra) / len(ids)) if ids else 0.0,
        }

    por_mes: dict[str, dict[str, Any]] = {}
    for cid, form in formularios.items():
        code = form_prefix(form)
        if code not in form_codes:
            continue
        mes = meses.get(cid, "")
        if mes < since_month:
            continue
        row = por_mes.setdefault(mes, {"mes": mes})
        row[code] = row.get(code, 0) + 1
    for row in por_mes.values():
        for code in form_codes:
            row[code + "_proj"] = round(row.get(code, 0) * taxas[code]["taxa"])

    return {
        "required": required,
        "since_month": since_month,
        "taxas": taxas,
        "meses": [por_mes[k] for k in sorted(por_mes)],
    }


def form_prefix(formulario: str | None) -> str:
    """Agrupa o formulário pelo prefixo ``F0NN`` (a string vem truncada)."""
    if formulario is None:
        return "(sem linha no DB)"
    match = _FORM_PREFIX.match(formulario)
    if match:
        return match.group(1)
    return "(vazio)" if formulario in {"(vazio)", ""} else "(outro)"


def cross_tab_formularios(
    checklists: list[dict[str, Any]], formularios: dict[str, str]
) -> list[dict[str, Any]]:
    """Tabela cruzada ``formulário × quantos dos 4 campos-alvo``.

    Este é o entregável central: responde se existe **algum** formulário em que
    c54, c55, c56 e c57 aparecem juntos.
    """
    rows: dict[str, dict[str, Any]] = {}
    for entry in checklists:
        cid = entry["checklist_id"]
        raw = formularios.get(cid)
        key = form_prefix(raw)
        row = rows.setdefault(
            key,
            {
                "form": key,
                "exemplo": "",
                "total": 0,
                "hist": Counter(),
                "com_alvo": 0,
                "com_alt": 0,
            },
        )
        if raw and not row["exemplo"] and key.startswith("F"):
            row["exemplo"] = raw
        row["total"] += 1
        fields = entry["fields"]
        row["hist"][len(fields & set(TARGET_FIELDS))] += 1
        if set(TARGET_FIELDS) <= fields:
            row["com_alvo"] += 1
        if set(ALT_FIELDS) <= fields:
            row["com_alt"] += 1
    return sorted(rows.values(), key=lambda r: -r["total"])


def field_month_table(
    checklists: list[dict[str, Any]],
    formularios: dict[str, str],
    form_code: str,
    *,
    fields: tuple[str, ...] = ("c53", "c54", "c55", "c56", "c57"),
    since_month: str = "2025-01",
) -> list[dict[str, Any]]:
    """Presença de cada campo, mês a mês, dentro de **um** formulário.

    É o instrumento que data a descontinuação de um campo: quando uma revisão
    do formulário renumera as vistas, a coluna do campo cai de ~100% para 0 em
    um ou dois meses, enquanto as vizinhas seguem estáveis.
    """
    rows: dict[str, dict[str, Any]] = {}
    for entry in checklists:
        if form_prefix(formularios.get(entry["checklist_id"])) != form_code:
            continue
        ref = entry.get("_ref")
        if ref is None:
            continue
        key = f"{ref.year:04d}-{ref.month:02d}"
        if key < since_month:
            continue
        row = rows.setdefault(key, {"mes": key, "n": 0, **{f: 0 for f in fields}})
        row["n"] += 1
        for f in fields:
            if f in entry["fields"]:
                row[f] += 1
    return [rows[k] for k in sorted(rows)]


def form_volume_table(
    checklists: list[dict[str, Any]],
    formularios: dict[str, str],
    form_codes: tuple[str, ...],
    *,
    since_month: str = "2026-01",
) -> list[dict[str, Any]]:
    """Volume mensal por formulário, com quantos têm os 4 campos-alvo."""
    rows: dict[str, dict[str, Any]] = {}
    for entry in checklists:
        code = form_prefix(formularios.get(entry["checklist_id"]))
        if code not in form_codes:
            continue
        ref = entry.get("_ref")
        if ref is None:
            continue
        key = f"{ref.year:04d}-{ref.month:02d}"
        if key < since_month:
            continue
        row = rows.setdefault(key, {"mes": key})
        row.setdefault(code, 0)
        row.setdefault(code + "_4", 0)
        row[code] += 1
        if set(TARGET_FIELDS) <= entry["fields"]:
            row[code + "_4"] += 1
    return [rows[k] for k in sorted(rows)]


# --------------------------------------------------------------------------- #
# agregação
# --------------------------------------------------------------------------- #
def _branch_of(path: str, root: str) -> str:
    """Primeiro segmento sob a raiz — na estrutura real, a filial."""
    norm_root = "/" + root.strip("/") if root else ""
    if norm_root and path.startswith(norm_root):
        rel = path[len(norm_root) + 1:]
    else:
        rel = path.lstrip("/")
    head = rel.split("/", 1)[0]
    return head or "(raiz)"


def build_checklists(
    files: list[dict[str, Any]], *, root: str
) -> tuple[dict[str, dict[str, Any]], int]:
    """Agrupa arquivos por ``checklist_id`` via ``parse_filename()``."""
    checklists: dict[str, dict[str, Any]] = {}
    ignored = update_checklists(checklists, files, root=root)
    return checklists, ignored


def update_checklists(
    checklists: dict[str, dict[str, Any]],
    files: list[dict[str, Any]],
    *,
    root: str,
) -> int:
    """Acumula ``files`` em ``checklists`` in-place; devolve nº de ignorados.

    Existe separado de :func:`build_checklists` para que várias varreduras
    (uma por arquivo de cache) sejam agregadas sem manter todas as listas de
    arquivos na memória ao mesmo tempo — o parque inteiro passa de 4 milhões
    de imagens.
    """
    from app.services.dropbox import parse_filename

    ignored = 0
    for f in files:
        try:
            parsed = parse_filename(f["name"])
        except ValueError:
            ignored += 1
            continue
        cid = parsed.checklist_id
        entry = checklists.setdefault(
            cid,
            {
                "checklist_id": cid,
                "fields": set(),
                "branch": _branch_of(f["path"], root),
                "captured_at": None,
                "server_modified": None,
                "n_files": 0,
            },
        )
        entry["fields"].add(parsed.field_name)
        entry["n_files"] += 1
        if parsed.captured_at is not None:
            prev = entry["captured_at"]
            if prev is None or parsed.captured_at < prev:
                entry["captured_at"] = parsed.captured_at
        sm = f.get("server_modified")
        if sm:
            prev_sm = entry["server_modified"]
            if prev_sm is None or sm < prev_sm:
                entry["server_modified"] = sm
    return ignored


def _reference_date(entry: dict[str, Any]) -> datetime | None:
    """Data do checklist: a do nome do arquivo; senão o server_modified."""
    if entry["captured_at"] is not None:
        dt: datetime = entry["captured_at"]
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    sm = entry["server_modified"]
    if sm:
        try:
            parsed = datetime.fromisoformat(sm)
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return None


def summarize(
    checklists: dict[str, dict[str, Any]],
    *,
    since: datetime | None,
) -> dict[str, Any]:
    """Calcula o censo: histograma, recorte por filial e por mês."""
    in_window: list[dict[str, Any]] = []
    sem_data = 0
    for entry in checklists.values():
        ref = _reference_date(entry)
        if ref is None:
            sem_data += 1
            if since is not None:
                continue
        elif since is not None and ref < since:
            continue
        entry["_ref"] = ref
        entry["_hits"] = sorted(set(entry["fields"]) & set(TARGET_FIELDS))
        in_window.append(entry)

    hist = Counter(len(e["_hits"]) for e in in_window)
    per_field = Counter()
    for e in in_window:
        for f in e["_hits"]:
            per_field[f] += 1

    by_branch: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "com_4": 0, "hist": Counter(), "per_field": Counter()}
    )
    by_month: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "com_4": 0, "com_3": 0}
    )
    combos = Counter()
    for e in in_window:
        b = by_branch[e["branch"]]
        b["total"] += 1
        b["hist"][len(e["_hits"])] += 1
        for f in e["_hits"]:
            b["per_field"][f] += 1
        if len(e["_hits"]) == 4:
            b["com_4"] += 1
        combos["+".join(e["_hits"]) or "(nenhum)"] += 1
        ref = e.get("_ref")
        if ref is not None:
            key = f"{ref.year:04d}-{ref.month:02d}"
            m = by_month[key]
            m["total"] += 1
            if len(e["_hits"]) == 4:
                m["com_4"] += 1
            if len(e["_hits"]) == 3:
                m["com_3"] += 1

    com_4 = [e for e in in_window if len(e["_hits"]) == 4]
    com_3 = [e for e in in_window if len(e["_hits"]) == 3]
    com_alt = [e for e in in_window if set(ALT_FIELDS) <= e["fields"]]

    return {
        "com_alt": len(com_alt),
        "total_checklists": len(in_window),
        "checklists_sem_data": sem_data,
        "hist": {k: hist.get(k, 0) for k in range(5)},
        "per_field": dict(per_field),
        "com_4": com_4,
        "com_3": com_3,
        "by_branch": dict(sorted(by_branch.items())),
        "by_month": dict(sorted(by_month.items())),
        "combos": combos,
    }


# --------------------------------------------------------------------------- #
# relatório
# --------------------------------------------------------------------------- #
def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.2f}%" if total else "n/a"


def render_report(
    summary: dict[str, Any],
    *,
    root: str,
    since: datetime | None,
    since_days: int | None,
    total_files: int,
    ignored: int,
    total_checklists_bruto: int,
    cobertura: dict[str, Any],
    cross_tab: list[dict[str, Any]] | None = None,
    c57_table: list[dict[str, Any]] | None = None,
    volume_table: list[dict[str, Any]] | None = None,
    volume_codes: tuple[str, ...] = (),
    projecao: dict[str, Any] | None = None,
) -> str:
    total = summary["total_checklists"]
    hist = summary["hist"]
    lines: list[str] = []
    add = lines.append

    janela = (
        f"últimos {since_days} dias (desde {since:%Y-%m-%d})"
        if since is not None
        else "todo o histórico listado (sem recorte de data)"
    )

    add("# Censo dos campos c54–c57 no Dropbox")
    add("")
    add(f"**Gerado em**: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC  ")
    add(f"**Fonte**: `{root}` (Dropbox, `files_list_folder` recursivo)  ")
    add(f"**Janela**: {janela}  ")
    add("**Script**: `scripts/survey_c54_c57.py` (read-only no Dropbox)  ")
    completos = cobertura["subtrees_completos"]
    parciais = cobertura["subtrees_parciais"]
    complete = bool(cobertura["complete"])
    add(
        f"**Cobertura**: {len(completos)} subtree(s) varrido(s) até o fim, "
        f"{len(parciais)} parcial(is), de {cobertura['subtrees_alvo']} alvo(s)  "
    )
    add(f"**Rate limits (429) durante a coleta**: {cobertura['rate_limit_hits']}")
    add("")
    if not complete:
        add(
            "> ⚠️ **Amostra declarada, não censo do `/Sisloc` inteiro.** Varrer a raiz toda é "
            "inviável em tempo útil: medimos ~1.150 arquivos/s via `files_list_folder`, e uma "
            "única filial passa de 125 mil imagens. A amostra abaixo é composta pelos subtrees "
            "**varridos até o fim** — para esses, os números são censo completo da filial, não "
            "recorte. Subtrees parciais entram como piso."
        )
        add("")
    add("**Subtrees varridos até o fim** (números completos para estes):")
    add("")
    for s in completos:
        add(f"- `{s}`")
    if not completos:
        add("- (nenhum)")
    if parciais:
        add("")
        add("**Subtrees parciais** (contam como piso, não como percentual confiável):")
        add("")
        for s in parciais:
            add(f"- `{s}`")
    nao_cobertos = cobertura.get("subtrees_nao_cobertos") or []
    if nao_cobertos:
        add("")
        add(
            f"**Filiais NÃO varridas** ({len(nao_cobertos)}) — a varredura foi encerrada por "
            "decisão de escopo, não por falha. A §12 extrapola o parque inteiro pelo banco, "
            "que cobre estas também:"
        )
        add("")
        add("- " + ", ".join(f"`{s}`" for s in nao_cobertos))
    add("")
    add(
        f"Varredura bruta: **{total_files} imagens**, **{total_checklists_bruto} checklists** "
        f"distintos; {ignored} arquivos com nome fora dos padrões conhecidos foram ignorados."
    )
    add("")
    add("### Como reproduzir sem varrer o Dropbox de novo")
    add("")
    add(
        "A listagem está em cache no disco — **não refaça a varredura**, ela levou horas e "
        "consome rate limit compartilhado. Os caches e o dump do banco são:"
    )
    add("")
    add("| Arquivo | Conteúdo |")
    add("|---|---|")
    add("| `data/survey_c54_c57_listing.json` | MG - CGE + SP - SBC (2,18M imagens) |")
    add("| `data/survey_c54_c57_resto.json` | as outras 11 filiais varridas (1,05M imagens) |")
    add("| `data/checklist_produto_formularios.psv` | dump da view (276.005 linhas, SELECT) |")
    add("")
    add("```bash")
    add("uv run python scripts/survey_c54_c57.py --from-cache --all \\")
    add("  --cache data/survey_c54_c57_listing.json \\")
    add("  --extra-cache data/survey_c54_c57_resto.json \\")
    add("  --formularios data/checklist_produto_formularios.psv")
    add("```")
    add("")
    add(
        "Para retomar a varredura das filiais que faltam, rode **sem** `--from-cache` e com "
        "`--paths`: cada subtree guarda seu próprio cursor, então filiais já completas são "
        "puladas e as parciais continuam de onde pararam."
    )
    add("")
    add("## 1. Números-chave")
    add("")
    add("| Métrica | Valor |")
    add("|---|---|")
    add(f"| Checklists na janela | {total} |")
    add(f"| Com os **4** campos (c54+c55+c56+c57) | {hist[4]} ({_pct(hist[4], total)}) |")
    add(f"| Com 3 dos 4 | {hist[3]} ({_pct(hist[3], total)}) |")
    add(f"| Com 2 dos 4 | {hist[2]} ({_pct(hist[2], total)}) |")
    add(f"| Com 1 dos 4 | {hist[1]} ({_pct(hist[1], total)}) |")
    add(f"| Com nenhum dos 4 | {hist[0]} ({_pct(hist[0], total)}) |")
    add(
        f"| _Conjunto alternativo_ **c53+c54+c55+c56** | {summary['com_alt']} "
        f"({_pct(summary['com_alt'], total)}) |"
    )
    add("")
    add("## 2. Histograma — quantos campos-alvo por checklist")
    add("")
    add("| Campos-alvo | Checklists | % | |")
    add("|---:|---:|---:|---|")
    for k in range(5):
        n = hist[k]
        bar = "█" * max(0, round(40 * n / total)) if total else ""
        add(f"| {k} | {n} | {_pct(n, total)} | {bar} |")
    add("")
    add("## 3. Incidência campo a campo")
    add("")
    add("| Campo | Vista (mapa) | Checklists que têm | % |")
    add("|---|---|---:|---:|")
    vistas = {
        "c54": "Lateral direita",
        "c55": "Lateral esquerda",
        "c56": "Frontal",
        "c57": "Traseira",
    }
    for f in TARGET_FIELDS:
        n = summary["per_field"].get(f, 0)
        add(f"| `{f}` | {vistas[f]} | {n} | {_pct(n, total)} |")
    add("")
    add("## 4. Combinações mais comuns dos campos-alvo")
    add("")
    add("| Combinação | Checklists | % |")
    add("|---|---:|---:|")
    for combo, n in summary["combos"].most_common(12):
        add(f"| `{combo}` | {n} | {_pct(n, total)} |")
    add("")
    add("## 5. Recorte por filial")
    add("")
    add("| Filial | Checklists | 4 campos | % com 4 | 3 campos | c54 | c55 | c56 | c57 |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, b in sorted(
        summary["by_branch"].items(), key=lambda kv: -kv[1]["total"]
    ):
        pf = b["per_field"]
        add(
            f"| {name} | {b['total']} | {b['com_4']} | {_pct(b['com_4'], b['total'])} | "
            f"{b['hist'].get(3, 0)} | {pf.get('c54', 0)} | {pf.get('c55', 0)} | "
            f"{pf.get('c56', 0)} | {pf.get('c57', 0)} |"
        )
    add("")
    add("## 6. Tendência mensal")
    add("")
    add("| Mês | Checklists | Com 4 | % com 4 | Com 3 |")
    add("|---|---:|---:|---:|---:|")
    for month, m in summary["by_month"].items():
        add(
            f"| {month} | {m['total']} | {m['com_4']} | "
            f"{_pct(m['com_4'], m['total'])} | {m['com_3']} |"
        )
    add("")
    add("## 7. Checklists que passariam no filtro estrito")
    add("")
    if summary["com_4"]:
        add("| checklist_id | Filial | Data | Arquivos |")
        add("|---|---|---|---:|")
        for e in sorted(summary["com_4"], key=lambda x: str(x.get("_ref")))[:200]:
            ref = e.get("_ref")
            data = f"{ref:%Y-%m-%d}" if ref else "?"
            add(f"| {e['checklist_id']} | {e['branch']} | {data} | {e['n_files']} |")
        if len(summary["com_4"]) > 200:
            add("")
            add(f"_(mostrando 200 de {len(summary['com_4'])})_")
    else:
        add("**Nenhum.** O filtro estrito processaria zero checklists nesta janela.")
    add("")
    add("## 8. Quase-lá (3 dos 4 campos)")
    add("")
    if summary["com_3"]:
        add("| checklist_id | Filial | Data | Campos-alvo presentes |")
        add("|---|---|---|---|")
        for e in sorted(summary["com_3"], key=lambda x: str(x.get("_ref")))[:60]:
            ref = e.get("_ref")
            data = f"{ref:%Y-%m-%d}" if ref else "?"
            add(
                f"| {e['checklist_id']} | {e['branch']} | {data} | "
                f"{', '.join(e['_hits'])} |"
            )
        if len(summary["com_3"]) > 60:
            add("")
            add(f"_(mostrando 60 de {len(summary['com_3'])})_")
    else:
        add("Nenhum checklist com exatamente 3 dos 4 campos-alvo.")
    add("")

    if cross_tab:
        add("## 9. Tabela cruzada — formulário × quantos dos 4 campos-alvo")
        add("")
        add(
            "Origem do `formulario`: `dbo.checklist_produto` no SQL Server (SOMENTE SELECT), "
            "casado por `codigo_checklist` = `checklist_id` do nome do arquivo. A coluna é "
            "`varchar(30)` e vem truncada, então o agrupamento é pelo prefixo `F0NN`. "
            "A coluna `c53–c56` mede o conjunto **alternativo** de 4 vistas."
        )
        add("")
        add(
            "| Formulário | Exemplo (truncado no DB) | Checklists | 0 | 1 | 2 | 3 | "
            "**4 (c54–c57)** | % | c53–c56 | % |"
        )
        add("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in cross_tab:
            h, n = r["hist"], r["total"]
            add(
                f"| `{r['form']}` | {r['exemplo']} | {n} | {h.get(0, 0)} | {h.get(1, 0)} | "
                f"{h.get(2, 0)} | {h.get(3, 0)} | **{r['com_alvo']}** | "
                f"{_pct(r['com_alvo'], n)} | {r['com_alt']} | {_pct(r['com_alt'], n)} |"
            )
        add("")
        com = [r for r in cross_tab if r["com_alvo"] > 0]
        add("### Formulários em que c54+c55+c56+c57 aparecem juntos")
        add("")
        if com:
            for r in sorted(com, key=lambda r: -r["com_alvo"]):
                add(
                    f"- **`{r['form']}`** ({r['exemplo']}): {r['com_alvo']} de {r['total']} "
                    f"checklists ({_pct(r['com_alvo'], r['total'])})"
                )
        else:
            add("**Nenhum formulário** tem os 4 campos juntos nesta amostra.")
        add("")

    if c57_table:
        add("## 10. A descontinuação do `c57` no F180")
        add("")
        add(
            "O F180 (visita GMG) é o formulário de maior volume e o que a reunião tinha em "
            "mente. A tabela abaixo mostra, mês a mês, quantos checklists F180 têm cada "
            "campo. `c53`–`c56` seguem estáveis; `c57` cai de ~100% para zero — não é foto "
            "faltando, é **renumeração de campo numa revisão do formulário**."
        )
        add("")
        campos = [k for k in c57_table[0] if k.startswith("c")]
        add("| Mês | Checklists F180 | " + " | ".join(f"`{c}`" for c in campos) + " | `c57` % |")
        add("|---|---:|" + "---:|" * (len(campos) + 1))
        for r in c57_table:
            cells = " | ".join(str(r[c]) for c in campos)
            add(f"| {r['mes']} | {r['n']} | {cells} | {_pct(r.get('c57', 0), r['n'])} |")
        add("")

    if volume_table and volume_codes:
        add("## 11. Volume mensal 2026 por formulário")
        add("")
        add(
            "F038 é **gerador**; F277 é **plataforma elevatória (PEMT)** — equipamentos "
            "diferentes, com taxonomia de dano diferente. F180 é visita a gerador, mas sem "
            "`c57` desde a revisão de set/2025."
        )
        add("")
        header = "| Mês |"
        sep = "|---|"
        for code in volume_codes:
            header += f" {code} total | {code} c54–c57 |"
            sep += "---:|---:|"
        add(header)
        add(sep)
        for r in volume_table:
            row = f"| {r['mes']} |"
            for code in volume_codes:
                row += f" {r.get(code, 0)} | {r.get(code + '_4', 0)} |"
            add(row)
        add("")

    if projecao:
        req = " ∧ ".join(f"`{f}`" for f in projecao["required"])
        add("## 12. Projeção do parque inteiro (extrapolação)")
        add("")
        add(
            f"A varredura cobre parte das filiais; a view `dbo.checklist_produto` cobre "
            f"**todas**. Aqui a taxa de checklists com {req} medida **dentro da amostra** é "
            f"aplicada ao total do parque, formulário a formulário. Volume e taxa usam a "
            f"mesma coluna de data (`data_conclusao_checklist`), então não há mistura de "
            f"data-da-foto com data-de-conclusão."
        )
        add("")
        add(f"**Taxas medidas** (desde {projecao['since_month']}):")
        add("")
        add("| Formulário | Parque (DB) | Na amostra | Cobertura | Com as vistas | Taxa |")
        add("|---|---:|---:|---:|---:|---:|")
        for code, t in projecao["taxas"].items():
            add(
                f"| `{code}` | {t['parque']} | {t['amostra']} | {t['cobertura'] * 100:.1f}% | "
                f"{t['com_vistas']} | **{t['taxa'] * 100:.1f}%** |"
            )
        add("")
        codes = list(projecao["taxas"])
        add("**Volume mensal projetado para o parque:**")
        add("")
        header = "| Mês |"
        sep = "|---|"
        for code in codes:
            header += f" {code} parque | {code} **projetado** |"
            sep += "---:|---:|"
        add(header)
        add(sep)
        for r in projecao["meses"]:
            row = f"| {r['mes']} |"
            for code in codes:
                row += f" {r.get(code, 0)} | **{r.get(code + '_proj', 0)}** |"
            add(row)
        add("")
        # O último mês da série é o mês corrente, ainda incompleto: incluí-lo na
        # média puxaria o número para baixo e subdimensionaria custo e cron.
        fechados = projecao["meses"][:-1] if len(projecao["meses"]) > 1 else projecao["meses"]
        tot_proj = sum(
            int(r.get(code + "_proj", 0)) for r in fechados for code in codes
        )
        media = tot_proj // max(1, len(fechados))
        add(
            f"> Média de **~{media} checklists/mês** processáveis no parque inteiro somando "
            f"{', '.join(codes)} (média sobre os {len(fechados)} meses fechados — o último "
            f"mês da tabela ainda está em curso e ficou de fora). É esse o número que "
            f"dimensiona o custo de LLM (4 imagens por checklist) e o cron de 30 min."
        )
        add("")

    if cross_tab:
        com4 = {r["form"]: r for r in cross_tab if r["com_alvo"] > 0}
        add("## 13. Recomendação — o filtro estrito é viável?")
        add("")
        add(
            "**Sim, mas não como estava escrito no mapa.** O risco 1 (\"o filtro estrito "
            "processaria zero\") era um artefato da amostra de 9 checklists, não a realidade: "
            f"nesta amostra {summary['hist'][4]} checklists têm os 4 campos. O que a medição "
            "derruba não é o filtro, é a premissa de que `c54–c57` é um conjunto estável e "
            "de que o formulário-alvo seria o F013."
        )
        add("")
        add("Três fatos, em ordem de consequência:")
        add("")
        add(
            "1. **`cN` é por formulário, e o F013 não tem as 4 vistas.** O F013 "
            "(\"LIBERAÇÃO DE GERADOR\"), que o mapa apontava como provável formulário-alvo, "
            f"tem {com4.get('F013', {}).get('com_alvo', 0)} checklists com os 4 campos — ele "
            "usa `c55`+`c57` para *outra coisa* (plaqueta de dados e carregador de bateria). "
            "Filtrar por F013 processaria zero."
        )
        add(
            "2. **O `c57` foi descontinuado no F180 em setembro/2025** (§10): 99,7% em "
            "ago/2025 → 45,1% em set/2025 → 0,5% em out/2025 → zero desde então, enquanto "
            "`c53`–`c56` seguem estáveis. Exigir `c57` no F180 hoje processa **zero**."
        )
        add(
            "3. **O conjunto de 4 vistas depende do formulário.** F038 e F277 mantêm "
            "`c54`–`c57`; o F180 pós-revisão usa `c53`–`c56`."
        )
        add("")
        add("**Filtro recomendado** — por formulário, com `c57` opcional:")
        add("")
        add("```sql")
        add("formulario LIKE 'F180%' OR formulario LIKE 'F038%'   -- geradores")
        add("```")
        add("")
        add(
            "com `c54 ∧ c55 ∧ c56` obrigatórios e `c57` opcional. Isso captura "
            f"{projecao['taxas']['F180']['taxa'] * 100:.0f}% dos F180 e "
            f"{projecao['taxas']['F038']['taxa'] * 100:.0f}% dos F038 "
            "(§12). O F277 fica de fora: é plataforma elevatória (PEMT), equipamento "
            "diferente, com taxonomia de dano diferente — apesar de ser o formulário com a "
            "maior taxa de 4 vistas do parque."
            if projecao
            else "com `c54 ∧ c55 ∧ c56` obrigatórios e `c57` opcional."
        )
        add("")
        add(
            "**Não precisa voltar à Tecnogera para destravar o MVP** — há entrada de sobra. "
            "O que ainda precisa da Tecnogera é a *semântica* das vistas: o mapa assume "
            "`c54`=lateral direita, `c55`=lateral esquerda, `c56`=frontal, `c57`=traseira, e "
            "essa tabela é do F180. Vale confirmar que os mesmos códigos significam o mesmo "
            "no F038 antes de escrever o prompt."
        )
        add("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Mede a incidência dos campos c54–c57 nos checklists do Dropbox. "
            "SOMENTE LEITURA: nunca escreve nem remove nada no Dropbox."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--since-days", type=int, default=90, help="Janela em dias.")
    p.add_argument("--all", action="store_true", help="Ignora a janela de data.")
    p.add_argument("--root", default=None, help="Raiz no Dropbox (default: settings).")
    p.add_argument("--env-file", type=Path, default=None, help="Arquivo .env extra.")
    p.add_argument("--cache", type=Path, default=Path("data/survey_c54_c57_listing.json"))
    p.add_argument("--from-cache", action="store_true", help="Não toca no Dropbox.")
    p.add_argument(
        "--paths",
        nargs="+",
        default=None,
        metavar="SUBTREE",
        help="Subtrees a varrer (default: descobre {root}/{filial}/Checklist).",
    )
    p.add_argument(
        "--time-budget",
        type=float,
        default=None,
        metavar="SEGUNDOS",
        help="Para a varredura após N segundos; o cursor fica salvo para retomar.",
    )
    p.add_argument(
        "--checkpoint-every",
        type=int,
        default=10,
        help="Grava o cache a cada N páginas (permite retomar).",
    )
    p.add_argument(
        "--extra-cache",
        nargs="+",
        type=Path,
        default=None,
        metavar="JSON",
        help="Caches adicionais a agregar no relatório (só leitura, não varre).",
    )
    p.add_argument(
        "--nao-cobertos",
        nargs="+",
        default=None,
        metavar="FILIAL",
        help="Filiais declaradamente NÃO varridas (aparecem no relatório).",
    )
    p.add_argument(
        "--formularios",
        type=Path,
        default=None,
        metavar="PSV",
        help="Dump 'codigo|formulario|filial|patrimonio' do SQL Server (SELECT).",
    )
    p.add_argument("--output-dir", type=Path, default=Path("docs/exploracao"))
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.env_file:
        _load_env_file(args.env_file.resolve())

    try:
        from app.core.config import get_settings

        root = args.root or get_settings().dropbox_root_path
    except Exception as exc:  # noqa: BLE001
        print(f"[erro] não consegui carregar as settings: {exc}", file=sys.stderr)
        return 1

    cache_path: Path = args.cache.resolve()

    if args.from_cache:
        state = _read_cache(cache_path)
        if state is None:
            print(f"[erro] --from-cache pedido mas {cache_path} não tem dado", file=sys.stderr)
            return 1
        files, cobertura = _flatten_cache(state, args.paths)
        print(f"[cache] {len(files)} imagens lidas de {cache_path}", file=sys.stderr)
    else:
        print(f"[dropbox] varrendo {root} (somente leitura)...", file=sys.stderr)
        try:
            files, cobertura = collect_listing(
                root,
                cache_path=cache_path,
                scan_paths=args.paths,
                checkpoint_every=args.checkpoint_every,
                time_budget_s=args.time_budget,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                "[erro] a varredura do Dropbox falhou — nenhum número foi produzido.\n"
                f"       {type(exc).__name__}: {exc}\n"
                "       Verifique DROPBOX_APP_KEY / DROPBOX_APP_SECRET / "
                "DROPBOX_REFRESH_TOKEN (.env ou --env-file)\n"
                "       e se DROPBOX_ROOT_PATH aponta para uma pasta existente.\n"
                f"       Um checkpoint parcial pode estar em {cache_path} — "
                "rode de novo para retomar.",
                file=sys.stderr,
            )
            return 1

    checklists: dict[str, dict[str, Any]] = {}
    total_files = len(files)
    ignored = update_checklists(checklists, files, root=root)
    del files  # libera a lista bruta antes de carregar o próximo cache

    for extra in args.extra_cache or []:
        extra_state = _read_cache(extra.resolve())
        if extra_state is None:
            print(f"[aviso] cache extra ilegível/ausente: {extra}", file=sys.stderr)
            continue
        extra_files, extra_cob = _flatten_cache(extra_state)
        total_files += len(extra_files)
        ignored += update_checklists(checklists, extra_files, root=root)
        cobertura["subtrees_completos"] = (
            cobertura["subtrees_completos"] + extra_cob["subtrees_completos"]
        )
        cobertura["subtrees_parciais"] = (
            cobertura["subtrees_parciais"] + extra_cob["subtrees_parciais"]
        )
        cobertura["subtrees_ausentes"] = (
            cobertura["subtrees_ausentes"] + extra_cob["subtrees_ausentes"]
        )
        cobertura["subtrees_alvo"] += extra_cob["subtrees_alvo"]
        cobertura["rate_limit_hits"] += extra_cob["rate_limit_hits"]
        cobertura["complete"] = cobertura["complete"] and extra_cob["complete"]
        print(
            f"[extra-cache] {extra}: +{len(extra_files)} imagens", file=sys.stderr
        )
        del extra_files, extra_state

    if args.nao_cobertos:
        cobertura["subtrees_nao_cobertos"] = args.nao_cobertos

    since = None if args.all else datetime.now(UTC) - timedelta(days=args.since_days)
    summary = summarize(checklists, since=since)

    cross_tab = None
    c57_table = None
    volume_table = None
    projecao = None
    volume_codes: tuple[str, ...] = ("F180", "F038", "F277")
    if args.formularios:
        formularios, meses = load_formularios(args.formularios.resolve())
        if formularios:
            # summarize() marca com "_hits" apenas os checklists dentro da janela
            na_janela = [e for e in checklists.values() if "_hits" in e]
            cross_tab = cross_tab_formularios(na_janela, formularios)
            c57_table = field_month_table(na_janela, formularios, "F180")
            volume_table = form_volume_table(na_janela, formularios, volume_codes)
            projecao = projetar_parque(
                na_janela, formularios, meses, ("F180", "F038")
            )
            print(
                f"[formularios] {len(formularios)} linhas do DB; "
                f"{len(cross_tab)} formulários na amostra",
                file=sys.stderr,
            )

    report = render_report(
        summary,
        root=root,
        since=since,
        since_days=None if args.all else args.since_days,
        total_files=total_files,
        ignored=ignored,
        total_checklists_bruto=len(checklists),
        cobertura=cobertura,
        cross_tab=cross_tab,
        c57_table=c57_table,
        volume_table=volume_table,
        volume_codes=volume_codes,
        projecao=projecao,
    )

    out_dir: Path = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "survey-c54-c57.md"
    out_path.write_text(report, encoding="utf-8")

    total = summary["total_checklists"]
    print(
        f"\n== janela: {'tudo' if args.all else str(args.since_days) + 'd'} | "
        f"checklists: {total} | com 4 campos: {summary['hist'][4]} "
        f"({_pct(summary['hist'][4], total)}) | com 3: {summary['hist'][3]} | "
        f"subtrees completos: {len(cobertura['subtrees_completos'])}/"
        f"{cobertura['subtrees_alvo']} | 429s: {cobertura['rate_limit_hits']}",
        file=sys.stderr,
    )
    print(f"relatório: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
