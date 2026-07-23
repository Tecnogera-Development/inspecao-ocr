"""Testes para ClassificationResult v1.1 — IAVS-046.

Cobre: second_best_field + second_best_confidence + quality_score.
Garante backward compatibility com jobs v1.0 (campos ausentes).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.llm_provider import ClassificationResult, _build_emit_classification_tool


# ── Helpers ──────────────────────────────────────────────────────────────────

def _base_payload(**overrides: object) -> dict:  # type: ignore[type-arg]
    base = {
        "image_filename": "checklist_276800_c3_0.jpeg",
        "field_name": "c3",
        "confidence": 0.90,
        "is_valid": True,
        "observation": "Campo ok.",
        "detected_issues": [],
        "requires_human_review": False,
        "model_version": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return base


# ── second_best_field + second_best_confidence ───────────────────────────────


@pytest.mark.unit
def test_ambos_second_best_nulos_e_valido() -> None:
    """Ambos null é o caso padrão — nenhuma ambiguidade detectada."""
    result = ClassificationResult(**_base_payload(second_best_field=None, second_best_confidence=None))
    assert result.second_best_field is None
    assert result.second_best_confidence is None


@pytest.mark.unit
def test_ambos_second_best_preenchidos_e_valido() -> None:
    """Quando há ambiguidade, ambos os campos são fornecidos."""
    result = ClassificationResult(
        **_base_payload(second_best_field="c5", second_best_confidence=0.78)
    )
    assert result.second_best_field == "c5"
    assert result.second_best_confidence == pytest.approx(0.78)


@pytest.mark.unit
def test_apenas_second_best_field_sem_confidence_invalido() -> None:
    """Fornecer só o campo sem a confiança é inválido (deve fazer retry 1×)."""
    with pytest.raises(ValidationError) as exc_info:
        ClassificationResult(**_base_payload(second_best_field="c5", second_best_confidence=None))
    errors = exc_info.value.errors()
    assert any("second_best" in str(e).lower() for e in errors)


@pytest.mark.unit
def test_apenas_second_best_confidence_sem_field_invalido() -> None:
    """Fornecer só a confiança sem o campo também é inválido."""
    with pytest.raises(ValidationError) as exc_info:
        ClassificationResult(**_base_payload(second_best_field=None, second_best_confidence=0.55))
    errors = exc_info.value.errors()
    assert any("second_best" in str(e).lower() for e in errors)


# ── quality_score ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_quality_score_valido_no_intervalo() -> None:
    """quality_score ∈ [0.0, 1.0] é aceito."""
    result = ClassificationResult(**_base_payload(quality_score=0.85))
    assert result.quality_score == pytest.approx(0.85)


@pytest.mark.unit
def test_quality_score_none_aceito() -> None:
    """quality_score ausente (None) é OK — campo opcional."""
    result = ClassificationResult(**_base_payload())
    assert result.quality_score is None


@pytest.mark.unit
def test_quality_score_acima_de_1_invalido() -> None:
    """quality_score > 1.0 deve levantar ValidationError."""
    with pytest.raises(ValidationError):
        ClassificationResult(**_base_payload(quality_score=1.5))


@pytest.mark.unit
def test_quality_score_abaixo_de_0_invalido() -> None:
    """quality_score < 0.0 deve levantar ValidationError."""
    with pytest.raises(ValidationError):
        ClassificationResult(**_base_payload(quality_score=-0.1))


# ── Backward compatibility ────────────────────────────────────────────────────


@pytest.mark.unit
def test_job_v1_0_sem_novos_campos_continua_parseando() -> None:
    """Jobs v1.0 sem second_best_* nem quality_score não quebram."""
    payload = {
        "image_filename": "checklist_276800_c3_0.jpeg",
        "field_name": "c3",
        "confidence": 0.88,
        "is_valid": True,
        "observation": "ok",
        "detected_issues": [],
        "requires_human_review": False,
        "model_version": "claude-sonnet-4-6",
    }
    result = ClassificationResult(**payload)
    assert result.second_best_field is None
    assert result.second_best_confidence is None
    assert result.quality_score is None


# ── Tool schema ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_tool_schema_contem_second_best_field() -> None:
    """O tool schema v1.1 deve conter second_best_field como propriedade."""
    tool = _build_emit_classification_tool(["c0", "c3"])
    props = tool["input_schema"]["properties"]
    assert "second_best_field" in props


@pytest.mark.unit
def test_tool_schema_contem_second_best_confidence() -> None:
    """O tool schema v1.1 deve conter second_best_confidence."""
    tool = _build_emit_classification_tool(["c0", "c3"])
    props = tool["input_schema"]["properties"]
    assert "second_best_confidence" in props


@pytest.mark.unit
def test_tool_schema_contem_quality_score() -> None:
    """O tool schema v1.1 deve conter quality_score com rubrica na descrição."""
    tool = _build_emit_classification_tool(["c0", "c3"])
    props = tool["input_schema"]["properties"]
    assert "quality_score" in props
    desc = props["quality_score"].get("description", "")
    assert "0.0" in desc and "1.0" in desc


@pytest.mark.unit
def test_tool_schema_second_best_fields_nao_sao_required() -> None:
    """second_best_* e quality_score são opcionais (não entram em required)."""
    tool = _build_emit_classification_tool(["c0", "c3"])
    required = tool["input_schema"]["required"]
    assert "second_best_field" not in required
    assert "second_best_confidence" not in required
    assert "quality_score" not in required


# ── EMIT_QUALITY_SCORE setting ────────────────────────────────────────────────


@pytest.mark.unit
def test_emit_quality_score_default_false() -> None:
    """EMIT_QUALITY_SCORE deve ser False por default (pré gate IAVS-047)."""
    from app.core.config import Settings

    s = Settings()
    assert s.emit_quality_score is False
