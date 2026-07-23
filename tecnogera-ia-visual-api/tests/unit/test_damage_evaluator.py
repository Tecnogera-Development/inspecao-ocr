"""Testes para DamageEvaluator (IAVS-066)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.event_pair  # noqa: F401
from app.db.base import Base
from app.models.event import Event
from app.services.damage_evaluator import (
    ClassMetrics,
    DamageEvalRecord,
    DamageEvalReport,
    DamageEvaluator,
    _prf1,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _rec(pred: str, true: str, moment: str | None = None, angle: str | None = None) -> DamageEvalRecord:
    return DamageEvalRecord(
        event_id=str(uuid.uuid4()),
        predicted_class=pred,
        true_class=true,
        moment=moment,
        angle=angle,
    )


# ── _prf1 ─────────────────────────────────────────────────────────────────────


def test_prf1_perfeito():
    p, r, f = _prf1(tp=10, fp=0, fn=0)
    assert p == 1.0 and r == 1.0 and f == 1.0


def test_prf1_zero_tp():
    p, r, f = _prf1(tp=0, fp=5, fn=5)
    assert p == 0.0 and r == 0.0 and f == 0.0


def test_prf1_sem_denominador():
    p, r, f = _prf1(tp=0, fp=0, fn=0)
    assert p == 0.0 and r == 0.0 and f == 0.0


def test_prf1_valores_parciais():
    p, r, f = _prf1(tp=2, fp=2, fn=1)
    assert abs(p - 0.5) < 1e-9
    assert abs(r - 2 / 3) < 1e-6
    assert f > 0


# ── DamageEvaluator.evaluate ─────────────────────────────────────────────────


def test_evaluate_lista_vazia():
    report = DamageEvaluator.evaluate([])
    assert report.n_evaluated == 0
    assert report.accuracy == 0.0
    assert report.macro_f1 == 0.0
    assert report.per_class == {}


def test_evaluate_predicao_perfeita():
    records = [
        _rec("dano_visivel", "dano_visivel"),
        _rec("conforme", "conforme"),
        _rec("ausencia_item", "ausencia_item"),
    ]
    report = DamageEvaluator.evaluate(records)
    assert report.accuracy == 1.0
    assert report.macro_f1 == 1.0
    for cls, m in report.per_class.items():
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0


def test_evaluate_predicao_errada():
    records = [
        _rec("conforme", "dano_visivel"),
        _rec("conforme", "dano_visivel"),
    ]
    report = DamageEvaluator.evaluate(records)
    assert report.accuracy == 0.0
    assert report.per_class["dano_visivel"].recall == 0.0
    assert report.per_class["conforme"].precision == 0.0


def test_evaluate_accuracy_parcial():
    records = [
        _rec("dano_visivel", "dano_visivel"),
        _rec("dano_visivel", "dano_visivel"),
        _rec("conforme", "dano_visivel"),
        _rec("conforme", "conforme"),
    ]
    report = DamageEvaluator.evaluate(records)
    assert abs(report.accuracy - 0.75) < 1e-9


def test_evaluate_n_evaluated():
    records = [_rec("conforme", "conforme") for _ in range(7)]
    report = DamageEvaluator.evaluate(records)
    assert report.n_evaluated == 7


def test_evaluate_per_class_support():
    records = [
        _rec("dano_visivel", "dano_visivel"),
        _rec("dano_visivel", "dano_visivel"),
        _rec("conforme", "conforme"),
    ]
    report = DamageEvaluator.evaluate(records)
    assert report.per_class["dano_visivel"].support == 2
    assert report.per_class["conforme"].support == 1


def test_evaluate_per_moment():
    records = [
        _rec("conforme", "conforme", moment="saida"),
        _rec("conforme", "conforme", moment="saida"),
        _rec("dano_visivel", "conforme", moment="retorno"),
    ]
    report = DamageEvaluator.evaluate(records)
    assert report.per_moment["saida"] == 1.0
    assert report.per_moment["retorno"] == 0.0


def test_evaluate_per_angle():
    records = [
        _rec("dano_visivel", "dano_visivel", angle="frontal"),
        _rec("conforme", "dano_visivel", angle="frontal"),
        _rec("conforme", "conforme", angle="traseira"),
    ]
    report = DamageEvaluator.evaluate(records)
    assert abs(report.per_angle["frontal"] - 0.5) < 1e-9
    assert report.per_angle["traseira"] == 1.0


def test_evaluate_confusion_matrix():
    records = [
        _rec("dano_visivel", "dano_visivel"),
        _rec("conforme", "dano_visivel"),
    ]
    report = DamageEvaluator.evaluate(records)
    matrix = {(r["true"], r["predicted"]): r["count"] for r in report.confusion_matrix}
    assert matrix[("dano_visivel", "dano_visivel")] == 1
    assert matrix[("dano_visivel", "conforme")] == 1


def test_evaluate_macro_f1_media_das_classes():
    records = [
        _rec("dano_visivel", "dano_visivel"),
        _rec("conforme", "conforme"),
    ]
    report = DamageEvaluator.evaluate(records)
    expected_macro = (
        report.per_class["dano_visivel"].f1 + report.per_class["conforme"].f1
    ) / 2
    assert abs(report.macro_f1 - expected_macro) < 1e-9


def test_evaluate_multi_classe():
    records = [
        _rec("ausencia_item", "ausencia_item"),
        _rec("fora_padrao_visual", "fora_padrao_visual"),
        _rec("dano_visivel", "dano_visivel"),
        _rec("conforme", "conforme"),
        _rec("conforme", "ausencia_item"),   # erro
    ]
    report = DamageEvaluator.evaluate(records)
    assert report.accuracy == 4 / 5
    assert "ausencia_item" in report.per_class
    assert "fora_padrao_visual" in report.per_class
    assert "dano_visivel" in report.per_class
    assert "conforme" in report.per_class


# ── DamageEvaluator.records_from_db ──────────────────────────────────────────


def _persist_event(db, damage_class: str | None, ground_truth: str | None, status: str = "done") -> Event:
    ev = Event(
        id=uuid.uuid4(),
        asset_code="GER-001",
        canonical_angle="frontal",
        captured_at=datetime(2026, 6, 10, 8, 0, 0, tzinfo=UTC),
        moment="saida",
        uploaded_by="tech01",
        source_path=f"/Avarias/GER-001/{uuid.uuid4()}.jpg",
        status=status,
        damage_class=damage_class,
        ground_truth_class=ground_truth,
    )
    db.add(ev)
    db.commit()
    return ev


def test_records_from_db_filtra_sem_gt(db):
    _persist_event(db, damage_class="dano_visivel", ground_truth=None)
    records = DamageEvaluator.records_from_db(db)
    assert records == []


def test_records_from_db_filtra_nao_done(db):
    _persist_event(db, damage_class=None, ground_truth="conforme", status="processing")
    records = DamageEvaluator.records_from_db(db)
    assert records == []


def test_records_from_db_carrega_anotados(db):
    _persist_event(db, damage_class="dano_visivel", ground_truth="dano_visivel")
    _persist_event(db, damage_class=None, ground_truth="conforme")
    records = DamageEvaluator.records_from_db(db)
    assert len(records) == 2


def test_records_from_db_damage_class_none_vira_conforme(db):
    _persist_event(db, damage_class=None, ground_truth="conforme")
    records = DamageEvaluator.records_from_db(db)
    assert records[0].predicted_class == "conforme"


def test_eval_report_e_serializavel():
    records = [_rec("conforme", "conforme")]
    report = DamageEvaluator.evaluate(records)
    data = report.model_dump()
    assert "per_class" in data
    assert "confusion_matrix" in data
