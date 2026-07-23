"""ReportGenerator — Modelo 3 (IAVS-006 + IAVS-044).

Suporta dois modos via settings.report_generator:
  legacy  — gera markdown inteiro via LLM (modo original)
  hybrid  — Jinja2 para seções estruturais + LLM via tool_use para narrativa
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import BaseLoader, Environment, StrictUndefined, UndefinedError

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.llm_provider import ClassificationResult

_log = get_logger(__name__)

_DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent.parent / "docs" / "relatorio" / "template.md"
_DEFAULT_TEMPLATE_V1_1 = Path(__file__).resolve().parent.parent.parent / "docs" / "relatorio" / "template_v1.1.j2"

_NULL_EQUIV = {"não observado", "n/d", "n/a", "-", "–", "—", ""}

_MAX_RETRIES = 2
_MAX_NARRATIVE_RETRIES = 1  # 1 initial + 1 retry = 2 total calls


class ReportGenerationError(Exception):
    """Relatório inválido após esgotar retries."""

    error_code = "report_generation_invalid_after_retry"

    def __init__(self, reason: str) -> None:
        super().__init__(f"Falha ao gerar relatório após {_MAX_RETRIES} retries: {reason}")
        self.reason = reason


class ReportGenerator:
    """Gera markdown de relatório via Modelo 3."""

    def __init__(self, provider: Any, template_path: Path | None = None) -> None:
        self._provider = provider
        self._template_path = template_path or _DEFAULT_TEMPLATE

    # ─── Legacy mode (original) ───────────────────────────────────────────────

    def generate(
        self,
        classifications: list[ClassificationResult],
        checklist_meta: dict[str, Any],
    ) -> str:
        """Gera markdown do relatório. Lança ReportGenerationError após max retries."""
        valid = [c for c in classifications if c.is_valid and c.confidence >= 0.70]
        inconclusive = [c for c in classifications if not c.is_valid and 0.40 <= c.confidence < 0.70]
        excluded = [c for c in classifications if c.confidence < 0.40]

        total_obr = checklist_meta.get("total_obrigatorios", len(classifications))
        cobertura_pct = round(len(valid) / total_obr * 100) if total_obr else 0
        enriched_meta: dict[str, Any] = {
            **checklist_meta,
            "valid_classifications": [c.model_dump() for c in valid],
            "inconclusive_classifications": [c.model_dump() for c in inconclusive],
            "n_excluded": len(excluded),
            "cobertura_pct": cobertura_pct,
        }

        template = self._template_path.read_text(encoding="utf-8")

        visible = valid + inconclusive
        last_error = ""
        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                enriched_meta["_correction_note"] = (
                    f"CORREÇÃO NECESSÁRIA (tentativa {attempt + 1}): {last_error}. "
                    "Preencha TODOS os placeholders {{...}} usando apenas dados do JSON."
                )
            markdown: str = self._provider.generate_report(visible, enriched_meta, template)
            validation_error = _validate(markdown, enriched_meta)
            if validation_error is None:
                _log.info(
                    "report_generated",
                    checklist_id=checklist_meta.get("checklist_id"),
                    attempt=attempt + 1,
                    validas=len(valid),
                    inconclusivos=len(inconclusive),
                    excluidas=len(excluded),
                )
                return markdown
            last_error = validation_error
            _log.warning("report_validation_failed", attempt=attempt + 1, reason=last_error)

        raise ReportGenerationError(last_error)

    # ─── Hybrid mode (IAVS-044) ───────────────────────────────────────────────

    def generate_hybrid(
        self,
        classifications: list[ClassificationResult],
        checklist_meta: dict[str, Any],
        *,
        template_str: str | None = None,
        _call_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> str:
        """Gera relatório no modo hybrid: Jinja2 + tool_use narrative."""
        if template_str is None:
            template_str = _DEFAULT_TEMPLATE_V1_1.read_text(encoding="utf-8")

        summary_data = _build_summary_data(classifications, checklist_meta)
        narrative = self._generate_narrative_sections(summary_data, _call_fn=_call_fn)
        return self._render_structured_sections(
            classifications=classifications,
            checklist_meta=checklist_meta,
            narrative=narrative,
            template_str=template_str,
        )

    def _render_structured_sections(
        self,
        classifications: list[ClassificationResult],
        checklist_meta: dict[str, Any],
        narrative: dict[str, Any],
        template_str: str,
    ) -> str:
        """Aplica Jinja2 com dados estruturados + narrativa pré-gerada."""
        valid = [c for c in classifications if c.is_valid and c.confidence >= 0.70]
        inconclusive = [c for c in classifications if not c.is_valid and 0.40 <= c.confidence < 0.70]
        excluded = [c for c in classifications if c.confidence < 0.40]

        total_obr = checklist_meta.get("total_obrigatorios", len(classifications))
        coverage_pct = round(len(valid) / total_obr * 100) if total_obr else 0

        context: dict[str, Any] = {
            **checklist_meta,
            "valid_classifications": [c.model_dump() for c in valid],
            "inconclusive_classifications": [c.model_dump() for c in inconclusive],
            "excluded_classifications": [c.model_dump() for c in excluded],
            "n_valid": len(valid),
            "n_inconclusive": len(inconclusive),
            "n_excluded": len(excluded),
            "coverage_pct": coverage_pct,
            "resumo_executivo": narrative.get("resumo_executivo", ""),
            "recomendacoes": narrative.get("recomendacoes", []),
        }

        env = Environment(loader=BaseLoader(), undefined=StrictUndefined)
        tmpl = env.from_string(template_str)
        try:
            return tmpl.render(**context)
        except UndefinedError as exc:
            _log.warning("jinja2_undefined_variable", error=str(exc))
            raise

    def _generate_narrative_sections(
        self,
        summary_data: dict[str, Any],
        *,
        _call_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Chama LLM via tool_use para emitir resumo executivo e recomendações.

        Guardrail: cada recomendação deve conter ao menos uma keyword de
        `detected_issues`; se ≥2 suspeitas, retry 1×.
        """
        if _call_fn is None:
            _call_fn = lambda sd: self._provider.generate_narrative(sd)  # noqa: E731

        keywords = _extract_narrative_keywords(summary_data)
        result: dict[str, Any] = {}

        for attempt in range(_MAX_NARRATIVE_RETRIES + 1):
            result = _call_fn(summary_data)
            recs: list[str] = result.get("recomendacoes", [])
            n_suspicious = sum(1 for rec in recs if not _rec_has_keyword(rec, keywords))
            if n_suspicious < 2:
                return result
            _log.warning(
                "guardrail_narrative_suspicious",
                attempt=attempt + 1,
                n_suspicious=n_suspicious,
                checklist_id=summary_data.get("checklist_id"),
            )

        return result


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _build_summary_data(
    classifications: list[ClassificationResult],
    checklist_meta: dict[str, Any],
) -> dict[str, Any]:
    """Constrói summary_data sem passar classifications cruas ao LLM."""
    valid = [c for c in classifications if c.is_valid and c.confidence >= 0.70]
    inconclusive = [c for c in classifications if not c.is_valid and 0.40 <= c.confidence < 0.70]
    excluded = [c for c in classifications if c.confidence < 0.40]

    total_obr = checklist_meta.get("total_obrigatorios", len(classifications))
    coverage_pct = round(len(valid) / total_obr * 100) if total_obr else 0

    # Contagem de não-conformes (campos válidos com detected_issues)
    n_nao_conformes = sum(1 for c in valid if c.detected_issues)

    # Agregação de detected_issues de todos os campos
    issue_freq: dict[str, int] = {}
    for c in classifications:
        for issue in c.detected_issues:
            issue_freq[issue] = issue_freq.get(issue, 0) + 1
    top_3 = sorted(issue_freq.items(), key=lambda x: -x[1])[:3]

    # Campos ausentes (excluídos do eval)
    expected_fields: list[str] = checklist_meta.get("expected_fields", [])
    classified_fields = {c.field_name for c in valid + inconclusive}
    fields_missing = [f for f in expected_fields if f not in classified_fields]

    return {
        "checklist_id": checklist_meta.get("checklist_id", ""),
        "coverage_pct": coverage_pct,
        "n_valid": len(valid),
        "n_inconclusive": len(inconclusive),
        "n_excluded": len(excluded),
        "n_nao_conformes": n_nao_conformes,
        "top_3_detected_issues_with_freq": top_3,
        "fields_missing": fields_missing,
    }


def _extract_narrative_keywords(summary_data: dict[str, Any]) -> set[str]:
    """Extrai palavras-chave de detected_issues para o guardrail."""
    keywords: set[str] = set()
    for item in summary_data.get("top_3_detected_issues_with_freq", []):
        if isinstance(item, (list, tuple)) and item:
            issue = str(item[0])
            keywords.update(w.lower() for w in issue.split() if len(w) > 3)
    return keywords


def _rec_has_keyword(rec: str, keywords: set[str]) -> bool:
    """Verifica se a recomendação contém ao menos uma keyword."""
    if not keywords:
        return True
    rec_lower = rec.lower()
    return any(kw in rec_lower for kw in keywords)


# ─── Legacy validation ────────────────────────────────────────────────────────


def _validate(markdown: str, meta: dict[str, Any]) -> str | None:
    """Retorna string com motivo de falha, ou None se markdown válido."""
    unfilled = re.findall(r"\{\{[^}]+\}\}", markdown)
    if unfilled:
        return f"placeholders não preenchidos: {unfilled[:3]}"

    name_error = _validate_no_invented_names(markdown, meta)
    if name_error:
        return name_error

    return None


def _validate_no_invented_names(markdown: str, meta: dict[str, Any]) -> str | None:
    """Verifica que Técnico e Filial no relatório existem no JSON de entrada."""
    allowed: set[str] = set()
    _collect_strings(meta, allowed)

    checks = [
        (r"\|\s*Técnico responsável\s*\|\s*([^|\n]+)\s*\|", "técnico"),
        (r"\|\s*Filial\s*\|\s*([^|\n]+)\s*\|", "filial"),
    ]
    for pattern, label in checks:
        for m in re.finditer(pattern, markdown, re.IGNORECASE):
            value = m.group(1).strip()
            if _is_null_equiv(value) or value in allowed:
                continue
            return f"nome de {label} inventado: {value!r}"
    return None


def _is_null_equiv(value: str) -> bool:
    """Aceita 'não observado', 'n/d', etc. — incluindo notas parentéticas redundantes."""
    lowered = value.lower().strip()
    if lowered in _NULL_EQUIV:
        return True
    primary = lowered.split("(", 1)[0].strip()
    return primary in _NULL_EQUIV


def _collect_strings(obj: Any, result: set[str]) -> None:
    if isinstance(obj, str):
        result.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, result)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, result)
