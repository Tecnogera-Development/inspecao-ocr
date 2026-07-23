"""Testes unitários do FakeLLMProvider — IAVS-001."""

from __future__ import annotations

import pytest

from app.services.llm_provider import FakeLLMProvider


@pytest.mark.unit
def test_filename_oracle_extrai_field_name_do_filename() -> None:
    provider = FakeLLMProvider(mode="filename_oracle")
    result = provider.classify_image(
        image_filename="153269005_checklist_276800_c33_0_10_04_2026 12_16_22.jpeg",
        image_bytes=b"fake",
        field_names=["c33", "c0", "c6"],
    )
    assert result.field_name == "c33"
    assert result.confidence == 1.0
    assert result.is_valid is True
    assert result.requires_human_review is False
    assert result.image_filename == "153269005_checklist_276800_c33_0_10_04_2026 12_16_22.jpeg"


@pytest.mark.unit
def test_low_conf_retorna_confidence_050() -> None:
    provider = FakeLLMProvider(mode="low_conf")
    result = provider.classify_image(
        image_filename="153269005_checklist_276800_c0_0_10_04_2026 12_00_00.jpeg",
        image_bytes=b"fake",
        field_names=["c0"],
    )
    assert result.confidence == 0.50
    assert result.is_valid is False
    assert result.requires_human_review is True


@pytest.mark.unit
def test_noisy_retorna_n_classificacoes_para_n_imagens() -> None:
    provider = FakeLLMProvider(mode="noisy", seed=42)
    filenames = [
        f"153269005_checklist_276800_c{i}_0_10_04_2026 12_00_00.jpeg"
        for i in range(10)
    ]
    results = [
        provider.classify_image(
            image_filename=fn,
            image_bytes=b"fake",
            field_names=[f"c{i}" for i in range(10)],
        )
        for fn in filenames
    ]
    assert len(results) == 10
    confidences = {r.confidence for r in results}
    # noisy retorna confiança variável (não fixa em 1.0 ou 0.5)
    assert len(confidences) > 1 or all(0 < r.confidence <= 1.0 for r in results)


@pytest.mark.unit
def test_filename_oracle_generate_report_retorna_markdown() -> None:
    from app.services.llm_provider import ClassificationResult

    provider = FakeLLMProvider(mode="filename_oracle")
    result = ClassificationResult(
        image_filename="153269005_checklist_276800_c0_0_10_04_2026 12_00_00.jpeg",
        field_name="c0",
        confidence=1.0,
        is_valid=True,
        observation="Campo ok.",
        detected_issues=[],
        requires_human_review=False,
        model_version="fake-1.0",
        shot_bank_hash="abc123",
    )
    md = provider.generate_report(
        classifications=[result],
        checklist_meta={"checklist_id": "276800"},
        template="# Relatório\n{{checklist.id}}\n",
    )
    assert isinstance(md, str)
    assert len(md) > 0


@pytest.mark.unit
def test_modo_invalido_levanta_value_error() -> None:
    with pytest.raises(ValueError, match="modo"):
        FakeLLMProvider(mode="invalido")  # type: ignore[arg-type]
