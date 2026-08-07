"""Testes do módulo cost_calculator — IAVS-049."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.cost_calculator import LLMUsage, compute_cost


@pytest.mark.unit
def test_sonnet_custo_correto_sem_cache() -> None:
    """Tracer bullet: Sonnet 4.6 a $3/MTok input + $15/MTok output."""
    # 1_000 input tokens = $0.003; 200 output tokens = $0.003
    cost = compute_cost(
        model="claude-sonnet-4-6",
        input_tokens=1_000,
        output_tokens=200,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )
    expected = 1_000 * 3e-6 + 200 * 15e-6
    assert abs(cost - expected) < 1e-9


@pytest.mark.unit
def test_haiku_custo_correto() -> None:
    """Haiku 4.5 a $1/MTok input + $5/MTok output."""
    cost = compute_cost(
        model="claude-haiku-4-5",
        input_tokens=800,
        output_tokens=400,
    )
    expected = 800 * 1e-6 + 400 * 5e-6
    assert abs(cost - expected) < 1e-9


@pytest.mark.unit
def test_batch_aplica_desconto_50_porcento() -> None:
    """batch_mode=True aplica desconto de 50% sobre custo normal."""
    sync_cost = compute_cost("claude-sonnet-4-6", input_tokens=1_000, output_tokens=100)
    batch_cost = compute_cost("claude-sonnet-4-6", input_tokens=1_000, output_tokens=100, batch_mode=True)
    assert abs(batch_cost - sync_cost / 2) < 1e-9


@pytest.mark.unit
def test_cache_read_tokens_mais_baratos_que_input() -> None:
    """Tokens de cache read custam menos por token que input normal (Sonnet: $0.30 vs $3/MTok)."""
    cost_normal = compute_cost("claude-sonnet-4-6", input_tokens=1_000, output_tokens=0)
    cost_cache_read = compute_cost("claude-sonnet-4-6", input_tokens=0, output_tokens=0, cache_read_tokens=1_000)
    assert cost_cache_read < cost_normal


@pytest.mark.unit
def test_cache_creation_tokens_custo_correto() -> None:
    """Cache creation tokens para Sonnet 4.6: $3.75/MTok."""
    cost = compute_cost("claude-sonnet-4-6", input_tokens=0, output_tokens=0, cache_creation_tokens=1_000_000)
    assert abs(cost - 3.75) < 1e-6


@pytest.mark.unit
def test_modelo_desconhecido_usa_sonnet_como_fallback() -> None:
    """Modelo não listado recebe pricing de Sonnet 4.6."""
    cost_unknown = compute_cost("claude-future-model-99", input_tokens=1_000, output_tokens=100)
    cost_sonnet = compute_cost("claude-sonnet-4-6", input_tokens=1_000, output_tokens=100)
    assert abs(cost_unknown - cost_sonnet) < 1e-9


# ── OpenAI (ticket mvp-c54-c57/08) ────────────────────────────────────────────


@pytest.mark.unit
def test_gpt_4_1_mini_bate_com_a_medicao_real_do_ticket_15() -> None:
    """12.517 tokens in + 292 out em 3 chamadas custaram ≈US$ 0,006.

    Este é o único número de custo REAL do GPT no projeto. Se a tabela de
    pricing derivar dele, o teto de orçamento passa a frear na hora errada.
    """
    cost = compute_cost("gpt-4.1-mini", input_tokens=12_517, output_tokens=292)

    assert cost == pytest.approx(0.005474, abs=1e-6)
    assert round(cost, 3) == 0.005


@pytest.mark.unit
def test_custo_por_imagem_do_parque_projetado() -> None:
    """~4,2k tokens/imagem → ≈US$ 0,002; 1.113 imagens/mês → ≈US$ 2/mês."""
    por_imagem = compute_cost("gpt-4.1-mini", input_tokens=4_200, output_tokens=100)

    assert por_imagem == pytest.approx(0.00184, abs=1e-5)
    assert 1.5 < por_imagem * 1_113 < 2.5


@pytest.mark.unit
def test_cache_read_da_openai_e_mais_barato() -> None:
    normal = compute_cost("gpt-4.1-mini", input_tokens=10_000, output_tokens=0)
    cacheado = compute_cost(
        "gpt-4.1-mini", input_tokens=0, output_tokens=0, cache_read_tokens=10_000
    )
    assert cacheado == pytest.approx(normal / 4)


@pytest.mark.unit
def test_batch_api_da_openai_tambem_leva_50_porcento() -> None:
    """Metade do custo, 24h de latência — a decisão de usar está no ticket 08."""
    sync = compute_cost("gpt-4.1-mini", input_tokens=4_200, output_tokens=100)
    batch = compute_cost("gpt-4.1-mini", input_tokens=4_200, output_tokens=100, batch_mode=True)
    assert batch == pytest.approx(sync / 2)


@pytest.mark.unit
def test_modelo_gpt_desconhecido_cai_no_mais_caro_da_familia() -> None:
    """Subestimar custo é o erro perigoso: o teto deixaria passar gasto."""
    from app.services.cost_calculator import resolve_pricing_model

    assert resolve_pricing_model("gpt-5-turbo-vision") == "gpt-4o"
    desconhecido = compute_cost("gpt-5-turbo-vision", input_tokens=1_000, output_tokens=0)
    barato = compute_cost("gpt-4.1-mini", input_tokens=1_000, output_tokens=0)
    assert desconhecido > barato


@pytest.mark.unit
def test_modelo_conhecido_nao_sofre_fallback() -> None:
    from app.services.cost_calculator import resolve_pricing_model

    assert resolve_pricing_model("gpt-4.1-mini") == "gpt-4.1-mini"
    assert resolve_pricing_model("claude-haiku-4-5") == "claude-haiku-4-5"


@pytest.mark.unit
def test_modelo_sem_familia_reconhecivel_cai_no_sonnet() -> None:
    from app.services.cost_calculator import resolve_pricing_model

    assert resolve_pricing_model("llama-3-70b") == "claude-sonnet-4-6"


# ── LLMUsage ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_llm_usage_accumulate_soma_tokens() -> None:
    """LLMUsage.accumulate() soma tokens corretamente em múltiplas chamadas."""
    usage = LLMUsage(model="claude-sonnet-4-6")
    usage.accumulate(input_tokens=100, output_tokens=20, cache_read_tokens=50, cache_creation_tokens=0)
    usage.accumulate(input_tokens=200, output_tokens=30, cache_read_tokens=0, cache_creation_tokens=10)

    assert usage.input_tokens == 300
    assert usage.output_tokens == 50
    assert usage.cache_read_tokens == 50
    assert usage.cache_creation_tokens == 10


# ── Orchestrator persiste estimated_cost_usd ──────────────────────────────────


@pytest.fixture
def _engine():
    from app.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def _db(_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _make_local_images_for_cost(tmp_path: Path, n: int = 3):
    from app.models.dropbox import ImageMetadata, LocalImage, ParsedFilename

    images = []
    for i in range(n):
        filename = f"153269005_checklist_276800_c{i}_0_10_04_2026 12_12_01.jpeg"
        p = tmp_path / filename
        p.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]))
        meta = ImageMetadata(
            dropbox_path=f"/Sisloc/{filename}",
            filename=filename,
            size_bytes=len(p.read_bytes()),
            parsed=ParsedFilename(
                raw=filename,
                checklist_id="276800",
                field_name=f"c{i}",
                captured_at=None,
                extension=".jpeg",
            ),
        )
        images.append(LocalImage(metadata=meta, local_path=p))
    return images


@pytest.mark.unit
def test_orchestrator_persiste_estimated_cost_usd(tmp_path: Path, _db: Session) -> None:
    """Após Orchestrator.run(), metrics contém 'estimated_cost_usd' como float."""
    from app.core.config import AppEnv, Settings
    from app.models.pipeline import PipelineJob
    from app.services.orchestrator import Orchestrator

    local_images = _make_local_images_for_cost(tmp_path)
    dropbox_mock = MagicMock()
    dropbox_mock.download_checklist_batch.return_value = local_images
    dropbox_mock.upload_report.return_value = MagicMock(dropbox_path="/relatorios/test.pdf")

    settings = Settings(_env_file=None, app_env=AppEnv.TEST, llm_provider="fake")
    job = PipelineJob(checklist_id="276800", status="pending")
    _db.add(job)
    _db.commit()

    with patch("app.services.orchestrator.PdfRendererService") as mock_renderer_cls:
        mock_renderer_cls.return_value.render.return_value = b"%PDF-fake"
        orch = Orchestrator(_db, dropbox_mock, settings=settings, work_dir=tmp_path)
        orch.run(job.id, "276800")

    _db.refresh(job)
    assert job.status == "done"
    assert "estimated_cost_usd" in (job.metrics or {})
    assert isinstance(job.metrics["estimated_cost_usd"], float)


@pytest.mark.unit
def test_continue_after_batch_persiste_estimated_cost_usd(tmp_path: Path, _db: Session) -> None:
    """continue_after_batch também persiste estimated_cost_usd em metrics."""
    from dataclasses import dataclass, field as dc_field
    from typing import Any

    from app.core.config import AppEnv, Settings
    from app.models.pipeline import PipelineJob
    from app.services.orchestrator import Orchestrator

    @dataclass
    class _FakeToolInput:
        field_name: str = "c0"
        confidence: float = 0.90
        observation: str = "OK"
        detected_issues: list = dc_field(default_factory=list)

    @dataclass
    class _FakeToolBlock:
        type: str = "tool_use"
        input: dict = dc_field(default_factory=lambda: {"field_name": "c0", "confidence": 0.90, "observation": "OK", "detected_issues": []})

    @dataclass
    class _FakeMsg:
        content: list = dc_field(default_factory=lambda: [_FakeToolBlock()])

    @dataclass
    class _FakeResult:
        type: str = "succeeded"
        message: Any = dc_field(default_factory=_FakeMsg)

    @dataclass
    class _FakeBatchResult:
        custom_id: str
        result: Any = dc_field(default_factory=_FakeResult)

    raw_results = [_FakeBatchResult(custom_id="276800_c0_2026-01-01.jpeg")]

    job = PipelineJob(
        checklist_id="276800",
        status="pending_batch",
        mode="batch",
        batch_id="batch_test_123",
        metrics={"profile_id": "_unknown_fallback"},
    )
    _db.add(job)
    _db.commit()

    dropbox_mock = MagicMock()
    dropbox_mock.upload_report.return_value = MagicMock(dropbox_path="/relatorios/test.pdf")

    settings = Settings(_env_file=None, app_env=AppEnv.TEST, llm_provider="fake")

    with patch("app.services.orchestrator.PdfRendererService") as mock_renderer_cls:
        mock_renderer_cls.return_value.render.return_value = b"%PDF-fake"
        orch = Orchestrator(_db, dropbox_mock, settings=settings, work_dir=tmp_path)
        orch.continue_after_batch(job.id, raw_results)

    _db.refresh(job)
    assert job.status == "done"
    assert "estimated_cost_usd" in (job.metrics or {})
    assert isinstance(job.metrics["estimated_cost_usd"], float)
