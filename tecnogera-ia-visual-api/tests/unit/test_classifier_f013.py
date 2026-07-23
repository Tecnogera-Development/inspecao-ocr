"""Testes unitários do Classifier F013 com ShotBank — IAVS-004."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.classifier import Classifier, ProfileNotSupportedError
from app.services.llm_provider import FakeLLMProvider

# Filenames reais do dataset F013
_F013_FILENAMES = [
    "153269005_checklist_276800_c0_0_10_04_2026 12_12_01.jpeg",
    "153269005_checklist_276800_c3_0_10_04_2026 12_13_00.jpeg",
    "153269005_checklist_276800_c6_0_10_04_2026 12_14_00.jpeg",
]
_F013_PROFILE = "F013_liberacao_gerador"
_F013_FIELDS = ["c0", "c3", "c4", "c6", "c33", "c38", "c39", "c55"]


def _image_paths(filenames: list[str]) -> list[Path]:
    """Paths fictícios — FakeLLMProvider não lê bytes reais."""
    return [Path(fn) for fn in filenames]


@pytest.mark.unit
def test_classify_checklist_f013_oracle_acerta_100_percent() -> None:
    """Tracer bullet: filename_oracle acerta todos os campos F013."""
    provider = FakeLLMProvider(mode="filename_oracle")
    classifier = Classifier(provider, field_names=_F013_FIELDS)
    paths = _image_paths(_F013_FILENAMES)

    results = classifier.classify_checklist(paths, profile_id=_F013_PROFILE)

    assert len(results) == len(paths)
    for result, fn in zip(results, _F013_FILENAMES):
        from app.services.dropbox import parse_filename
        expected_field = parse_filename(fn).field_name
        assert result.field_name == expected_field, (
            f"filename {fn}: esperado {expected_field}, obtido {result.field_name}"
        )
        assert result.confidence == 1.0
        assert result.is_valid is True


@pytest.mark.unit
def test_classify_checklist_non_f013_usa_fallback() -> None:
    """Checklist de formulário desconhecido usa _unknown_fallback (não levanta exceção)."""
    from app.services.llm_provider import _GENERIC_CLASSES

    provider = FakeLLMProvider(mode="filename_oracle")
    classifier = Classifier(provider, field_names=[])
    paths = _image_paths(_F013_FILENAMES[:1])

    results = classifier.classify_checklist(paths, profile_id="F180_visita_tecnica")

    assert len(results) == 1
    assert results[0].field_name is None
    assert results[0].generic_class in _GENERIC_CLASSES
    assert results[0].requires_human_review is True


@pytest.mark.unit
def test_classify_checklist_low_conf_is_invalid_e_requires_review() -> None:
    """low_conf: confidence=0.50 → is_valid=False e requires_human_review=True."""
    provider = FakeLLMProvider(mode="low_conf")
    classifier = Classifier(provider, field_names=_F013_FIELDS)
    paths = _image_paths([_F013_FILENAMES[0]])

    results = classifier.classify_checklist(paths, profile_id=_F013_PROFILE)

    assert len(results) == 1
    r = results[0]
    assert r.confidence == 0.50
    assert r.is_valid is False
    assert r.requires_human_review is True


@pytest.mark.unit
def test_classify_checklist_noisy_nao_filtra_erros_do_provider() -> None:
    """noisy: classifier retorna todos os resultados sem filtrar erros do provider."""
    provider = FakeLLMProvider(mode="noisy", seed=0)
    classifier = Classifier(provider, field_names=_F013_FIELDS)
    # 5 imagens de campos diferentes
    filenames = [
        f"153269005_checklist_276800_c{f}_0_10_04_2026 12_00_00.jpeg"
        for f in ["0", "3", "4", "6", "33"]
    ]
    paths = _image_paths(filenames)

    results = classifier.classify_checklist(paths, profile_id=_F013_PROFILE)

    assert len(results) == 5, "Todos os resultados devem ser retornados, sem filtro"


@pytest.mark.unit
def test_classify_checklist_shot_bank_hash_propagado_para_resultados(tmp_path: Path) -> None:
    """shot_bank_hash do ShotBank aparece em todos os ClassificationResult."""
    from unittest.mock import MagicMock

    # Monta ShotBank mínimo com hash fixo
    shot_bank = MagicMock()
    shot_bank.compute_hash.return_value = "abc123fakehash"
    shot_bank.select_shots.return_value = []  # sem shots reais

    provider = FakeLLMProvider(mode="filename_oracle")
    classifier = Classifier(provider, field_names=_F013_FIELDS)
    paths = _image_paths(_F013_FILENAMES[:2])

    results = classifier.classify_checklist(
        paths, profile_id=_F013_PROFILE, shot_bank=shot_bank
    )

    assert len(results) == 2
    for r in results:
        assert r.shot_bank_hash == "abc123fakehash"


@pytest.mark.unit
def test_classify_checklist_ordena_imagens_por_field_name() -> None:
    """Classifier processa imagens agrupadas por campo (maximiza cache hit).

    Sem ordering, shots mudam a cada imagem (cada field tem shots distintos)
    e o cache write/read fica ruim. Com ordering, todas as imagens do mesmo
    campo são classificadas em sequência → cache do bloco de shots reusado.
    """
    from unittest.mock import MagicMock

    # Imagens desordenadas: c145, c0, c145, c0, c145
    paths = [
        Path("153269005_checklist_276800_c145_0_10_04_2026 17_08_27.jpeg"),
        Path("153269005_checklist_276800_c0_0_10_04_2026 12_12_01.jpeg"),
        Path("153269005_checklist_276800_c145_1_10_04_2026 17_08_35.jpeg"),
        Path("153269005_checklist_276800_c0_1_10_04_2026 12_13_00.jpeg"),
        Path("153269005_checklist_276800_c145_2_10_04_2026 17_08_57.jpeg"),
    ]

    spy = MagicMock(wraps=FakeLLMProvider(mode="filename_oracle"))
    classifier = Classifier(spy, field_names=_F013_FIELDS)
    classifier.classify_checklist(paths, profile_id=_F013_PROFILE)

    called_filenames = [
        call.kwargs.get("image_filename") or call.args[0]
        for call in spy.classify_image.call_args_list
    ]
    assert len(called_filenames) == len(paths), (
        f"esperado {len(paths)} calls, recebido {len(called_filenames)}; spy não capturou"
    )
    from app.services.dropbox import parse_filename
    fields_in_order = [parse_filename(fn).field_name for fn in called_filenames]
    # Comprime runs consecutivos: ['0','0','145','145','145'] → ['0','145']
    runs = [f for i, f in enumerate(fields_in_order) if i == 0 or f != fields_in_order[i - 1]]
    assert len(runs) == len(set(fields_in_order)), (
        f"campos intercalados (cache hit ruim): ordem={fields_in_order}, runs={runs}"
    )


@pytest.mark.unit
def test_classify_checklist_loga_warning_quando_shot_bytes_falha(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Quando read_bytes() lança OSError, classifier deve logar warning (não silenciar).

    Sem warning, iter 1 do dry-run rodou "few-shot" silenciosamente sem shots.
    """
    from unittest.mock import MagicMock

    from app.services.shot_bank import ImageRef

    bad_ref = ImageRef(
        path=tmp_path / "nao_existe.jpeg",
        filename="shot_c0.jpeg",
        field_name="c0",
        checklist_id="000000",
        quality_score=1000.0,
    )
    shot_bank = MagicMock()
    shot_bank.compute_hash.return_value = "h"
    shot_bank.select_shots.return_value = [bad_ref]

    provider = FakeLLMProvider(mode="filename_oracle")
    classifier = Classifier(provider, field_names=_F013_FIELDS)
    paths = _image_paths(_F013_FILENAMES[:1])

    classifier.classify_checklist(paths, profile_id=_F013_PROFILE, shot_bank=shot_bank)

    out = capfd.readouterr().out + capfd.readouterr().err
    assert "shot_read_failed" in out, f"warning não foi emitido; saída: {out[:500]}"
