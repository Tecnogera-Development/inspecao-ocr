"""Testes unitários do Classifier fallback _unknown_fallback — IAVS-005."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.classifier import Classifier
from app.services.llm_provider import FakeLLMProvider

_UNKNOWN_PROFILE = "F180_visita_gmg"
_F013_PROFILE = "F013_liberacao_gerador"

# Filenames com checklist_id de formulário não-F013
_F180_FILENAMES = [
    "153664205_checklist_278724_c0_0_15_04_2026 14_49_43.jpeg",
    "153664205_checklist_278724_c1_0_15_04_2026 14_41_09.jpeg",
    "153664205_checklist_278724_c15_0_15_04_2026 14_44_22.jpeg",
]


def _fake_paths(filenames: list[str]) -> list[Path]:
    return [Path(fn) for fn in filenames]


@pytest.mark.unit
def test_classify_checklist_unknown_profile_nao_levanta_excecao() -> None:
    """Classifier não levanta ProfileNotSupportedError para perfil desconhecido."""
    provider = FakeLLMProvider(mode="filename_oracle")
    classifier = Classifier(provider, field_names=[])
    paths = _fake_paths(_F180_FILENAMES[:1])

    # deve completar sem exceção
    results = classifier.classify_checklist(paths, profile_id=_UNKNOWN_PROFILE)
    assert len(results) == 1


@pytest.mark.unit
def test_fallback_field_name_none_generic_class_preenchido() -> None:
    """Fallback: field_name=None e generic_class é um dos 5 valores literais."""
    from app.services.llm_provider import _GENERIC_CLASSES

    provider = FakeLLMProvider(mode="filename_oracle")
    classifier = Classifier(provider, field_names=[])
    paths = _fake_paths(_F180_FILENAMES)

    results = classifier.classify_checklist(paths, profile_id=_UNKNOWN_PROFILE)

    for r in results:
        assert r.field_name is None, f"field_name deve ser None no fallback, obtido {r.field_name}"
        assert r.generic_class is not None, "generic_class não deve ser None no fallback"
        assert r.generic_class in _GENERIC_CLASSES, f"generic_class inválido: {r.generic_class}"


@pytest.mark.unit
def test_fallback_requires_human_review_sempre_true() -> None:
    """Fallback: requires_human_review=True em TODAS as classificações, mesmo com conf=1.0."""
    provider = FakeLLMProvider(mode="filename_oracle")  # oracle retorna conf=1.0
    classifier = Classifier(provider, field_names=[])
    paths = _fake_paths(_F180_FILENAMES)

    results = classifier.classify_checklist(paths, profile_id=_UNKNOWN_PROFILE)

    for r in results:
        assert r.requires_human_review is True, (
            f"requires_human_review deve ser True no fallback, arquivo {r.image_filename}"
        )


@pytest.mark.unit
def test_equipment_profiles_yaml_tem_unknown_fallback() -> None:
    """_unknown_fallback existe no YAML com 5 super-classes."""
    from app.services.equipment_profiles import EquipmentProfileService

    svc = EquipmentProfileService()
    profile = svc.get_profile("_unknown_fallback")
    field_names = [c.field_name for c in profile.campos]

    expected = {
        "painel_display",
        "conexao_eletrica",
        "estrutura_externa",
        "componente_mecanico",
        "etiqueta_documento",
    }
    assert set(field_names) == expected


@pytest.mark.unit
def test_fallback_oracle_retorna_super_classe_deterministica() -> None:
    """filename_oracle em modo fallback retorna super-classe determinística via regra cN%5."""
    from app.services.llm_provider import _GENERIC_CLASSES

    provider = FakeLLMProvider(mode="filename_oracle")
    classifier = Classifier(provider, field_names=[])

    # c0 → 0 % 5 = 0 → painel_display
    # c1 → 1 % 5 = 1 → conexao_eletrica
    # c15 → 15 % 5 = 0 → painel_display
    filenames_and_expected = [
        ("153664205_checklist_278724_c0_0_15_04_2026 14_49_43.jpeg", _GENERIC_CLASSES[0]),
        ("153664205_checklist_278724_c1_0_15_04_2026 14_41_09.jpeg", _GENERIC_CLASSES[1]),
        ("153664205_checklist_278724_c15_0_15_04_2026 14_44_22.jpeg", _GENERIC_CLASSES[0]),
    ]

    for fn, expected_class in filenames_and_expected:
        results = classifier.classify_checklist(
            [Path(fn)], profile_id=_UNKNOWN_PROFILE
        )
        assert results[0].generic_class == expected_class, (
            f"{fn}: esperado {expected_class}, obtido {results[0].generic_class}"
        )
