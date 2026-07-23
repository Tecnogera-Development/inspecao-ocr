"""Testes do modo hybrid do ReportGenerator (IAVS-044).

TDD vertical: um ciclo por comportamento.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.services.llm_provider import ClassificationResult


def _make_cls(
    filename: str,
    field: str,
    confidence: float,
    *,
    detected_issues: list[str] | None = None,
) -> ClassificationResult:
    is_valid = confidence >= 0.70
    return ClassificationResult(
        image_filename=filename,
        field_name=field,
        confidence=confidence,
        is_valid=is_valid,
        observation=f"Observação de {field}.",
        detected_issues=detected_issues or [],
        requires_human_review=0.40 <= confidence < 0.70,
        model_version="fake-1.0",
        shot_bank_hash="abc",
    )


_META = {
    "checklist_id": "276800",
    "data": "01/06/2026",
    "filial": "Filial Norte",
    "tecnico": "Carlos Silva",
    "total_obrigatorios": 3,
}

_SIMPLE_TEMPLATE = """\
Checklist: {{ checklist_id }}
Data: {{ data }}
Filial: {{ filial }}
Cobertura: {{ coverage_pct }}%
Validos: {{ n_valid }}
Inconclusivos: {{ n_inconclusive }}
Excluidos: {{ n_excluded }}

{% for cls in valid_classifications %}
- {{ cls.field_name }}: {{ cls.confidence }}
{% endfor %}

{% if inconclusive_classifications %}
INCONCLUSIVOS:
{% for cls in inconclusive_classifications %}
- {{ cls.field_name }} (inconc)
{% endfor %}
{% endif %}

RESUMO: {{ resumo_executivo }}

RECOMENDACOES:
{% for rec in recomendacoes %}
{{ loop.index }}. {{ rec }}
{% endfor %}
"""


# ── Ciclo 1: _render_structured_sections preenche todos os placeholders ──────


@pytest.mark.unit
def test_render_preenche_todos_placeholders() -> None:
    """_render_structured_sections não deve deixar sintaxe Jinja2 no output."""
    from app.services.report_generator import ReportGenerator
    from unittest.mock import MagicMock

    provider = MagicMock()
    gen = ReportGenerator(provider)

    classifications = [
        _make_cls("img_a.jpg", "c1", 0.90),
        _make_cls("img_b.jpg", "c2", 0.55),
    ]
    narrative = {"resumo_executivo": "Tudo OK.", "recomendacoes": ["Verificar óleo."]}

    result = gen._render_structured_sections(
        classifications=classifications,
        checklist_meta=_META,
        narrative=narrative,
        template_str=_SIMPLE_TEMPLATE,
    )

    # Nenhum {{ }} ou {% %} deve restar
    assert "{{" not in result, f"Placeholder não preenchido no output:\n{result}"
    assert "{%" not in result, f"Bloco Jinja2 não resolvido no output:\n{result}"


# ── Ciclo 2: checklist_id aparece no output ───────────────────────────────────


@pytest.mark.unit
def test_render_inclui_checklist_id() -> None:
    """checklist_id do meta deve aparecer no output renderizado."""
    from app.services.report_generator import ReportGenerator
    from unittest.mock import MagicMock

    gen = ReportGenerator(MagicMock())
    narrative = {"resumo_executivo": "Resumo.", "recomendacoes": ["Rec1."]}
    result = gen._render_structured_sections(
        classifications=[_make_cls("a.jpg", "c1", 0.90)],
        checklist_meta=_META,
        narrative=narrative,
        template_str=_SIMPLE_TEMPLATE,
    )
    assert "276800" in result


# ── Ciclo 3: seção de inconclusivos renderizada ───────────────────────────────


@pytest.mark.unit
def test_render_inclui_inconclusivos() -> None:
    """Campos inconclusivos aparecem na seção de inconclusivos."""
    from app.services.report_generator import ReportGenerator
    from unittest.mock import MagicMock

    gen = ReportGenerator(MagicMock())
    classifications = [
        _make_cls("a.jpg", "c1", 0.90),
        _make_cls("b.jpg", "c2", 0.55),   # inconclusivo
    ]
    narrative = {"resumo_executivo": "Resumo.", "recomendacoes": ["Rec1."]}
    result = gen._render_structured_sections(
        classifications=classifications,
        checklist_meta=_META,
        narrative=narrative,
        template_str=_SIMPLE_TEMPLATE,
    )
    assert "INCONCLUSIVOS" in result
    assert "c2 (inconc)" in result


# ── Ciclo 4: _generate_narrative_sections chama _call_fn e retorna dict ───────


@pytest.mark.unit
def test_generate_narrative_chama_call_fn() -> None:
    """_generate_narrative_sections usa _call_fn injetável e retorna dict."""
    from app.services.report_generator import ReportGenerator
    from unittest.mock import MagicMock

    gen = ReportGenerator(MagicMock())
    summary_data = {
        "checklist_id": "276800",
        "coverage_pct": 80,
        "n_valid": 4,
        "n_inconclusive": 1,
        "n_excluded": 0,
        "n_nao_conformes": 1,
        "top_3_detected_issues_with_freq": [("vazamento óleo", 2)],
        "fields_missing": [],
    }

    expected = {
        "resumo_executivo": "Inspeção realizada com sucesso.",
        "recomendacoes": ["Verificar vazamento de óleo."],
    }
    call_fn = MagicMock(return_value=expected)

    result = gen._generate_narrative_sections(summary_data, _call_fn=call_fn)

    assert call_fn.called, "_call_fn deve ser invocado"
    assert result["resumo_executivo"] == expected["resumo_executivo"]
    assert result["recomendacoes"] == expected["recomendacoes"]


# ── Ciclo 5: guardrail — todas as recs têm keywords → sem retry ───────────────


@pytest.mark.unit
def test_guardrail_recs_validas_sem_retry() -> None:
    """Se todas as recomendações contêm keywords de detected_issues, sem retry."""
    from app.services.report_generator import ReportGenerator
    from unittest.mock import MagicMock

    gen = ReportGenerator(MagicMock())
    summary_data = {
        "checklist_id": "276800",
        "coverage_pct": 75,
        "n_valid": 3,
        "n_inconclusive": 1,
        "n_excluded": 0,
        "n_nao_conformes": 1,
        "top_3_detected_issues_with_freq": [("vazamento", 2), ("filtro", 1)],
        "fields_missing": [],
    }

    call_count = 0

    def valid_call_fn(sd: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "resumo_executivo": "Inspeção realizada.",
            "recomendacoes": [
                "Inspecionar o vazamento detectado.",
                "Trocar o filtro com urgência.",
            ],
        }

    result = gen._generate_narrative_sections(summary_data, _call_fn=valid_call_fn)
    assert call_count == 1, "Sem retry esperado quando keywords estão presentes"
    assert len(result["recomendacoes"]) == 2


# ── Ciclo 6: guardrail — ≥2 suspeitas → retry ────────────────────────────────


@pytest.mark.unit
def test_guardrail_duas_suspeitas_dispara_retry() -> None:
    """Se ≥2 recomendações não contêm keywords, retry é disparado."""
    from app.services.report_generator import ReportGenerator
    from unittest.mock import MagicMock

    gen = ReportGenerator(MagicMock())
    summary_data = {
        "checklist_id": "276800",
        "coverage_pct": 75,
        "n_valid": 3,
        "n_inconclusive": 1,
        "n_excluded": 0,
        "n_nao_conformes": 1,
        "top_3_detected_issues_with_freq": [("óleo", 2)],
        "fields_missing": [],
    }

    call_count = 0

    def call_fn(sd: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Ambas as recs sem keywords de "óleo"
            return {
                "resumo_executivo": "OK.",
                "recomendacoes": [
                    "Fazer revisão periódica.",     # sem "óleo"
                    "Registrar ocorrências.",        # sem "óleo"
                ],
            }
        # Retry: rec com keyword
        return {
            "resumo_executivo": "OK.",
            "recomendacoes": ["Verificar óleo imediatamente."],
        }

    result = gen._generate_narrative_sections(summary_data, _call_fn=call_fn)
    assert call_count == 2, "Retry deve ter ocorrido uma vez"
    assert "óleo" in result["recomendacoes"][0].lower()


# ── Ciclo 7: guardrail — cap de 2 retries, aceita resultado final ─────────────


@pytest.mark.unit
def test_guardrail_cap_dois_retries_aceita_resultado() -> None:
    """Após 2 retries sem sucesso, aceita o resultado sem levantar exceção."""
    from app.services.report_generator import ReportGenerator
    from unittest.mock import MagicMock

    gen = ReportGenerator(MagicMock())
    summary_data = {
        "checklist_id": "276800",
        "coverage_pct": 75,
        "n_valid": 3,
        "n_inconclusive": 1,
        "n_excluded": 0,
        "n_nao_conformes": 1,
        "top_3_detected_issues_with_freq": [("filtro", 1)],
        "fields_missing": [],
    }

    call_count = 0

    def always_suspicious(sd: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "resumo_executivo": "OK.",
            # Nenhuma rec menciona "filtro"
            "recomendacoes": [
                "Verificar painel elétrico.",
                "Checar porcas e parafusos.",
            ],
        }

    # Não deve levantar exceção — apenas aceita após esgotar retries
    result = gen._generate_narrative_sections(summary_data, _call_fn=always_suspicious)
    assert call_count == 2, "Deve ter tentado 1 + 1 retry = 2 vezes (cap 2 total)"
    assert "recomendacoes" in result


# ── Ciclo 8: settings — REPORT_GENERATOR default é "hybrid" ──────────────────


@pytest.mark.unit
def test_settings_report_generator_default_hybrid() -> None:
    """REPORT_GENERATOR deve ter default 'hybrid' no Settings."""
    from app.core.config import Settings

    cfg = Settings(_env_file=None)
    assert cfg.report_generator == "hybrid"


# ── Ciclo 9: settings — REPORT_MODEL default é claude-haiku-4-5 ──────────────


@pytest.mark.unit
def test_settings_report_model_default() -> None:
    """REPORT_MODEL deve ter default 'claude-haiku-4-5' no Settings."""
    from app.core.config import Settings

    cfg = Settings(_env_file=None)
    assert cfg.report_model == "claude-haiku-4-5"
