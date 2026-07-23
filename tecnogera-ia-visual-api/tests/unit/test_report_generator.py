"""Testes do ReportGenerator real (IAVS-006).

TDD vertical: um ciclo por comportamento.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.llm_provider import ClassificationResult, FakeLLMProvider


def _make_result(
    filename: str,
    field: str,
    confidence: float,
    *,
    is_valid: bool | None = None,
    requires_human_review: bool | None = None,
) -> ClassificationResult:
    if is_valid is None:
        is_valid = confidence >= 0.70
    if requires_human_review is None:
        requires_human_review = 0.40 <= confidence < 0.70
    return ClassificationResult(
        image_filename=filename,
        field_name=field,
        confidence=confidence,
        is_valid=is_valid,
        observation=f"Observação de {field}.",
        detected_issues=[],
        requires_human_review=requires_human_review,
        model_version="fake-1.0",
        shot_bank_hash="abc123",
    )


_META = {
    "checklist_id": "276800",
    "data": "01/01/2026",
    "total_obrigatorios": 3,
}


# ── Ciclo 1: tracer bullet ────────────────────────────────────────────────────


@pytest.mark.unit
def test_generate_calls_provider() -> None:
    """ReportGenerator.generate() delega a geração ao provider."""
    from app.services.report_generator import ReportGenerator

    called_with: dict[str, Any] = {}

    def fake_generate(classifications: Any, meta: Any, template: Any) -> str:
        called_with["classifications"] = classifications
        called_with["meta"] = meta
        called_with["template"] = template
        return "# Relatório Fake\nConteúdo gerado."

    provider = MagicMock()
    provider.generate_report.side_effect = fake_generate

    gen = ReportGenerator(provider)
    classifications = [_make_result("276800_c0_001.jpg", "c0", 0.95)]
    result = gen.generate(classifications, _META)

    assert provider.generate_report.called, "provider.generate_report deve ser chamado"
    assert isinstance(result, str)
    assert "{{" not in result


# ── Ciclo 2: roteamento de classificações ────────────────────────────────────


@pytest.mark.unit
def test_routing_valid_in_valid_bucket() -> None:
    """Classificações is_valid=True (conf≥0.70) aparecem em valid_classifications."""
    from app.services.report_generator import ReportGenerator

    captured: dict[str, Any] = {}

    def spy(cls: Any, meta: Any, tpl: Any) -> str:
        captured["meta"] = meta
        return "# ok"

    provider = MagicMock()
    provider.generate_report.side_effect = spy

    gen = ReportGenerator(provider)
    high = _make_result("img_high.jpg", "c0", 0.90)
    low_inconc = _make_result("img_inconc.jpg", "c1", 0.50)
    low_excl = _make_result("img_excl.jpg", "c2", 0.30)

    gen.generate([high, low_inconc, low_excl], _META)

    valid = captured["meta"]["valid_classifications"]
    inconc = captured["meta"]["inconclusive_classifications"]
    n_excl = captured["meta"]["n_excluded"]

    assert len(valid) == 1 and valid[0]["image_filename"] == "img_high.jpg"
    assert len(inconc) == 1 and inconc[0]["image_filename"] == "img_inconc.jpg"
    assert n_excl == 1


@pytest.mark.unit
def test_routing_cobertura_pct() -> None:
    """cobertura_pct = (validas / total_obrigatorios) * 100, arredondado."""
    from app.services.report_generator import ReportGenerator

    captured: dict[str, Any] = {}

    def spy(cls: Any, meta: Any, tpl: Any) -> str:
        captured["meta"] = meta
        return "# ok"

    provider = MagicMock()
    provider.generate_report.side_effect = spy

    meta = {**_META, "total_obrigatorios": 6}
    gen = ReportGenerator(provider)
    # 2 valid out of 6 total → 33%
    gen.generate(
        [
            _make_result("a.jpg", "c0", 0.90),
            _make_result("b.jpg", "c1", 0.90),
            _make_result("c.jpg", "c2", 0.50),
            _make_result("d.jpg", "c3", 0.20),
        ],
        meta,
    )

    assert captured["meta"]["cobertura_pct"] == 33


# ── Ciclo 3: validação e retry ───────────────────────────────────────────────


@pytest.mark.unit
def test_retry_on_unfilled_placeholder() -> None:
    """Primeira chamada retorna placeholder {{x}}, segunda retorna markdown válido."""
    from app.services.report_generator import ReportGenerator

    call_count = 0

    def mock_generate(cls: Any, meta: Any, tpl: Any) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "# Relatório\n{{tecnico.nome}} não preenchido"
        return "# Relatório\nTécnico: não observado"

    provider = MagicMock()
    provider.generate_report.side_effect = mock_generate

    gen = ReportGenerator(provider)
    result = gen.generate([_make_result("x.jpg", "c0", 0.90)], _META)

    assert call_count == 2, "deve ter tentado 2 vezes (1 inicial + 1 retry)"
    assert "{{" not in result


@pytest.mark.unit
def test_max_retries_raises_report_generation_error() -> None:
    """Após 3 tentativas falhadas lança ReportGenerationError."""
    from app.services.report_generator import ReportGenerationError, ReportGenerator

    def always_invalid(cls: Any, meta: Any, tpl: Any) -> str:
        return "# Relatório\n{{placeholder.nao.preenchido}} ainda aqui"

    provider = MagicMock()
    provider.generate_report.side_effect = always_invalid

    gen = ReportGenerator(provider)
    with pytest.raises(ReportGenerationError) as exc_info:
        gen.generate([_make_result("x.jpg", "c0", 0.90)], _META)

    assert "report_generation_invalid_after_retry" in exc_info.value.error_code
    assert provider.generate_report.call_count == 3  # 1 inicial + 2 retries


@pytest.mark.unit
def test_validation_rejects_invented_tecnico_name() -> None:
    """Validação rejeita markdown com nome de técnico não presente no JSON."""
    from app.services.report_generator import ReportGenerationError, ReportGenerator

    # meta sem tecnico.nome → qualquer nome na tabela é inventado
    meta_sem_tecnico = {"checklist_id": "276800", "total_obrigatorios": 1}

    call_count = 0

    def mock_generate(cls: Any, meta: Any, tpl: Any) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "| Técnico responsável | João da Silva |\n# Relatório"
        return "| Técnico responsável | não observado |\n# Relatório"

    provider = MagicMock()
    provider.generate_report.side_effect = mock_generate

    gen = ReportGenerator(provider)
    result = gen.generate([_make_result("x.jpg", "c0", 0.90)], meta_sem_tecnico)

    assert call_count == 2, "deve ter retentado após detectar nome inventado"
    assert "João da Silva" not in result


@pytest.mark.unit
def test_validation_aceita_nao_observado_com_nota_parentetica() -> None:
    """Regressão IAVS-009: 'não observado (não observado)' não deve ser tratado como inventado.

    O LLM ocasionalmente acrescenta nota parentética ao valor nulo. Como ambos
    os tokens são null-equiv, o validador deve aceitar.
    """
    from app.services.report_generator import ReportGenerator

    call_count = 0

    def mock_generate(cls: Any, meta: Any, tpl: Any) -> str:
        nonlocal call_count
        call_count += 1
        return "| Filial | não observado (não observado) |\n# Relatório"

    provider = MagicMock()
    provider.generate_report.side_effect = mock_generate

    gen = ReportGenerator(provider)
    result = gen.generate([_make_result("x.jpg", "c0", 0.90)], {"checklist_id": "276800"})

    assert call_count == 1, "validador deveria aceitar na 1ª tentativa, sem retry"
    assert "não observado" in result


@pytest.mark.unit
def test_validation_accepts_known_tecnico_name() -> None:
    """Validação aceita nome de técnico que existe no JSON de entrada."""
    from app.services.report_generator import ReportGenerator

    meta_com_tecnico = {
        "checklist_id": "276800",
        "total_obrigatorios": 1,
        "tecnico": "Carlos Souza",
    }

    def mock_generate(cls: Any, meta: Any, tpl: Any) -> str:
        return "| Técnico responsável | Carlos Souza |\n# Relatório"

    provider = MagicMock()
    provider.generate_report.side_effect = mock_generate

    gen = ReportGenerator(provider)
    result = gen.generate([_make_result("x.jpg", "c0", 0.90)], meta_com_tecnico)

    assert "Carlos Souza" in result  # nome permitido, não causa retry


@pytest.mark.unit
def test_retry_meta_has_correction_note() -> None:
    """No retry, meta inclui _correction_note com explicação do problema."""
    from app.services.report_generator import ReportGenerator

    metas: list[dict[str, Any]] = []

    def capture(cls: Any, meta: Any, tpl: Any) -> str:
        metas.append(dict(meta))
        if len(metas) == 1:
            return "# {{placeholder}}"
        return "# Relatório ok"

    provider = MagicMock()
    provider.generate_report.side_effect = capture

    ReportGenerator(provider).generate([_make_result("x.jpg", "c0", 0.90)], _META)

    assert "_correction_note" not in metas[0], "primeira tentativa não deve ter correction_note"
    assert "_correction_note" in metas[1], "retry deve incluir _correction_note"
    assert "placeholder" in metas[1]["_correction_note"].lower()
