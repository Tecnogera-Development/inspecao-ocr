"""Testes para DamageClassifier e DamageClassifierResult (IAVS-063)."""

from __future__ import annotations

import pytest

from app.services.damage_classifier import DamageClassifier, DamageClassifierResult
from app.services.llm_provider import DamageClassifyResult, DamageClassItem


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_result(
    no_conformity: bool,
    classes: list[DamageClassItem] | None = None,
    canonical_angle: str = "frontal",
) -> DamageClassifyResult:
    return DamageClassifyResult(
        no_conformity=no_conformity,
        classes=classes or [],
        canonical_angle=canonical_angle,
        model_version="fake-1.0",
    )


def _make_item(
    class_name: str = "dano_visivel",
    confidence: float = 0.9,
    severity: int = 2,
    observation: str = "trinca no painel frontal",
) -> DamageClassItem:
    return DamageClassItem(
        class_name=class_name,
        confidence=confidence,
        severity=severity,
        observation=observation,
    )


class _StubProvider:
    def __init__(self, result: DamageClassifyResult) -> None:
        self._result = result

    def classify_event(self, image_bytes: bytes, *, gabarito_bytes=None, saida_bytes=None, references=None):
        return self._result


# ── DamageClassItem validação ────────────────────────────────────────────────


def test_damage_class_item_valido():
    item = _make_item()
    assert item.class_name == "dano_visivel"
    assert item.confidence == 0.9
    assert item.severity == 2


def test_damage_class_item_confidence_invalida():
    with pytest.raises(Exception):
        DamageClassItem(
            class_name="dano_visivel",
            confidence=1.5,
            severity=1,
            observation="teste",
        )


def test_damage_class_item_class_name_invalida():
    with pytest.raises(Exception):
        DamageClassItem(
            class_name="classe_inexistente",
            confidence=0.8,
            severity=2,
            observation="teste",
        )


# ── DamageClassifyResult ─────────────────────────────────────────────────────


def test_damage_classify_result_conforme():
    r = _make_result(no_conformity=False)
    assert r.no_conformity is False
    assert r.classes == []


def test_damage_classify_result_com_dano():
    item = _make_item(class_name="dano_visivel", confidence=0.85, severity=2)
    r = _make_result(no_conformity=True, classes=[item])
    assert r.no_conformity is True
    assert len(r.classes) == 1
    assert r.classes[0].class_name == "dano_visivel"


# ── DamageClassifierResult propriedades ─────────────────────────────────────


def test_resultado_conforme_sem_dano():
    raw = _make_result(no_conformity=False)
    res = DamageClassifierResult(event_id="abc", raw=raw)
    assert res.damage_class is None
    assert res.damage_confidence is None
    assert res.damage_severity is None
    assert res.angle_class == "frontal"


def test_resultado_com_dano_simples():
    item = _make_item(class_name="dano_visivel", confidence=0.9, severity=2)
    raw = _make_result(no_conformity=True, classes=[item], canonical_angle="lat_dir")
    res = DamageClassifierResult(event_id="ev1", raw=raw)
    assert res.damage_class == "dano_visivel"
    assert res.damage_confidence == 0.9
    assert res.damage_severity == "alta"
    assert res.angle_class == "lat_dir"


def test_resultado_multi_label_maior_confianca_vence():
    item_a = _make_item(class_name="ausencia_item", confidence=0.6, severity=3)
    item_b = _make_item(class_name="fora_padrao_visual", confidence=0.85, severity=4)
    raw = _make_result(no_conformity=True, classes=[item_a, item_b])
    res = DamageClassifierResult(event_id="ev2", raw=raw)
    assert res.damage_class == "fora_padrao_visual"
    assert res.damage_confidence == 0.85


def test_resultado_severity_mais_critica_vence():
    item_alta = _make_item(class_name="dano_visivel", confidence=0.9, severity=2)
    item_critica = _make_item(class_name="ausencia_item", confidence=0.7, severity=1)
    raw = _make_result(no_conformity=True, classes=[item_alta, item_critica])
    res = DamageClassifierResult(event_id="ev3", raw=raw)
    assert res.damage_severity == "critica"


def test_resultado_severity_labels():
    for sev, label in [(1, "critica"), (2, "alta"), (3, "media"), (4, "baixa")]:
        item = _make_item(severity=sev)
        raw = _make_result(no_conformity=True, classes=[item])
        res = DamageClassifierResult(event_id="x", raw=raw)
        assert res.damage_severity == label


def test_to_event_columns_conforme():
    raw = _make_result(no_conformity=False)
    res = DamageClassifierResult(event_id="x", raw=raw)
    cols = res.to_event_columns()
    assert cols["damage_class"] is None
    assert cols["damage_confidence"] is None
    assert cols["damage_severity"] is None
    assert cols["angle_class"] == "frontal"
    assert cols["result_json"]["no_conformity"] is False
    assert cols["result_json"]["classes"] == []


def test_to_event_columns_com_dano():
    item = _make_item(class_name="dano_visivel", confidence=0.92, severity=1)
    raw = _make_result(no_conformity=True, classes=[item], canonical_angle="traseira")
    res = DamageClassifierResult(event_id="ev4", raw=raw)
    cols = res.to_event_columns()
    assert cols["damage_class"] == "dano_visivel"
    assert cols["damage_confidence"] == 0.92
    assert cols["damage_severity"] == "critica"
    assert cols["angle_class"] == "traseira"
    assert cols["result_json"]["no_conformity"] is True
    assert len(cols["result_json"]["classes"]) == 1


# ── DamageClassifier.classify ────────────────────────────────────────────────


def test_classifier_chama_provider_e_retorna():
    item = _make_item(class_name="fora_padrao_visual", confidence=0.75, severity=3)
    raw = _make_result(no_conformity=True, classes=[item], canonical_angle="interior")
    provider = _StubProvider(raw)
    classifier = DamageClassifier(provider)
    result = classifier.classify(b"fake-image", event_id="ev5")
    assert isinstance(result, DamageClassifierResult)
    assert result.event_id == "ev5"
    assert result.damage_class == "fora_padrao_visual"
    assert result.angle_class == "interior"


def test_classifier_conforme():
    raw = _make_result(no_conformity=False)
    provider = _StubProvider(raw)
    classifier = DamageClassifier(provider)
    result = classifier.classify(b"img", event_id="conf1")
    assert result.no_conformity is False
    assert result.damage_class is None


def test_fake_provider_classify_event():
    from app.services.llm_provider import FakeLLMProvider

    provider = FakeLLMProvider()
    result = provider.classify_event(b"img")
    assert result.no_conformity is False
    assert result.classes == []
    assert result.canonical_angle == "frontal"
    assert result.model_version == "fake-damage-1.0"


def test_fake_provider_ignora_kwargs():
    from app.services.llm_provider import FakeLLMProvider

    provider = FakeLLMProvider()
    result = provider.classify_event(b"img", gabarito_bytes=b"gab", saida_bytes=b"saida")
    assert result.no_conformity is False
