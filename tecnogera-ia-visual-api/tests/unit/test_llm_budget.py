"""Freios de gasto de LLM — ticket mvp-c54-c57/08.

A chave da OpenAI é real e paga. Estes testes são o contrato do que impede um
backfill descuidado de virar fatura: kill switch, teto por rodada, orçamento
mensal e o guarda-corpo contra provider fake fora de desenvolvimento.

Nenhuma chamada de API acontece aqui — nem fake nem real. O guarda decide sem
tocar em provider nenhum.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.models.checklist_analysis import STATUS_ANALISADA, ChecklistViewResult
from app.models.pipeline import PipelineJob
from app.services.llm_budget import (
    MOTIVO_DISPATCH_DESABILITADO,
    MOTIVO_ORCAMENTO_ESTOURADO,
    MOTIVO_PROVIDER_FAKE,
    MOTIVO_TETO_DE_CHAMADAS,
    LLMBudgetGuard,
    inicio_do_mes,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> Iterator[Session]:
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


def _cfg(**extra: object) -> Settings:
    base: dict[str, object] = {
        "llm_dispatch_enabled": True,
        "openai_api_key": "sk-teste",
    }
    base.update(extra)
    return Settings(_env_file=None, app_env=AppEnv.TEST, **base)  # type: ignore[arg-type]


def _gasto(db: Session, custo: float, *, quando: datetime | None = None) -> None:
    """Grava uma vista já analisada com custo — é o que o guarda soma."""
    job = PipelineJob(id=uuid.uuid4(), checklist_id="1", status="done")
    db.add(job)
    db.flush()
    db.add(
        ChecklistViewResult(
            id=uuid.uuid4(),
            job_id=job.id,
            checklist_id="1",
            campo="c54",
            status=STATUS_ANALISADA,
            cost_usd=custo,
            created_at=quando or datetime.now(UTC),
        )
    )
    db.commit()


# ── kill switch ───────────────────────────────────────────────────────────────


def test_kill_switch_default_e_desligado() -> None:
    """Subir a esteira ingerindo sem gastar precisa ser o comportamento padrão."""
    assert Settings(_env_file=None, app_env=AppEnv.TEST).llm_dispatch_enabled is False


def test_kill_switch_desligado_bloqueia_a_rodada_inteira(db: Session) -> None:
    guard = LLMBudgetGuard(db, _cfg(llm_dispatch_enabled=False))

    decisao = guard.avaliar_rodada()

    assert not decisao
    assert decisao.motivo == MOTIVO_DISPATCH_DESABILITADO
    assert guard.motivo_de_parada == MOTIVO_DISPATCH_DESABILITADO


def test_kill_switch_desligado_nem_consulta_o_gasto(db: Session) -> None:
    """Barato de propósito: a esteira desligada é o caso comum."""
    _gasto(db, 1.0)
    guard = LLMBudgetGuard(db, _cfg(llm_dispatch_enabled=False))

    guard.avaliar_rodada()

    assert guard.gasto_no_mes == 0.0


# ── teto de chamadas por rodada ───────────────────────────────────────────────


def test_teto_de_chamadas_corta_no_limite(db: Session) -> None:
    guard = LLMBudgetGuard(db, _cfg(llm_max_calls_per_run=2))
    assert guard.avaliar_rodada()

    assert guard.antes_da_chamada()
    guard.registrar_chamada(0.002)
    assert guard.antes_da_chamada()
    guard.registrar_chamada(0.002)

    terceira = guard.antes_da_chamada()
    assert not terceira
    assert terceira.motivo == MOTIVO_TETO_DE_CHAMADAS
    assert guard.chamadas == 2
    assert guard.chamadas_restantes == 0


def test_teto_zero_nem_abre_a_rodada(db: Session) -> None:
    guard = LLMBudgetGuard(db, _cfg(llm_max_calls_per_run=0))

    decisao = guard.avaliar_rodada()

    assert not decisao
    assert decisao.motivo == MOTIVO_TETO_DE_CHAMADAS


def test_default_do_teto_nao_corta_operacao_normal() -> None:
    """~371 checklists/mês × 3 vistas ≈ 0,8 chamada por rodada de 30 min."""
    assert Settings(_env_file=None, app_env=AppEnv.TEST).llm_max_calls_per_run >= 60


# ── orçamento mensal ──────────────────────────────────────────────────────────


def test_orcamento_estourado_barra_antes_de_abrir_a_rodada(db: Session) -> None:
    _gasto(db, 30.0)
    guard = LLMBudgetGuard(db, _cfg(llm_monthly_budget_usd=25.0))

    decisao = guard.avaliar_rodada()

    assert not decisao
    assert decisao.motivo == MOTIVO_ORCAMENTO_ESTOURADO


def test_orcamento_estoura_no_meio_da_rodada(db: Session) -> None:
    """O freio é por chamada, não por rodada: o teto vale para a próxima vista."""
    _gasto(db, 0.99)
    guard = LLMBudgetGuard(db, _cfg(llm_monthly_budget_usd=1.0))
    assert guard.avaliar_rodada()

    assert guard.antes_da_chamada()
    guard.registrar_chamada(0.02)

    segunda = guard.antes_da_chamada()
    assert not segunda
    assert segunda.motivo == MOTIVO_ORCAMENTO_ESTOURADO


def test_gasto_do_mes_soma_o_persistido_com_o_da_rodada(db: Session) -> None:
    _gasto(db, 0.5)
    guard = LLMBudgetGuard(db, _cfg())
    guard.avaliar_rodada()

    guard.registrar_chamada(0.002)
    guard.registrar_chamada(0.003)

    assert guard.custo_da_rodada == pytest.approx(0.005)
    assert guard.gasto_no_mes == pytest.approx(0.505)


def test_gasto_de_mes_anterior_nao_conta(db: Session) -> None:
    """Do contrário o orçamento nunca viraria e a esteira morreria em janeiro."""
    mes_passado = inicio_do_mes() - timedelta(days=1)
    _gasto(db, 999.0, quando=mes_passado)
    guard = LLMBudgetGuard(db, _cfg(llm_monthly_budget_usd=25.0))

    assert guard.avaliar_rodada()
    assert guard.gasto_persistido_no_mes() == 0.0


def test_banco_vazio_tem_gasto_zero(db: Session) -> None:
    assert LLMBudgetGuard(db, _cfg()).gasto_persistido_no_mes() == 0.0


def test_default_do_orcamento_da_folga_sobre_o_custo_medido() -> None:
    """Custo medido do parque: ≈US$ 2/mês. Um teto abaixo disso cortaria operação."""
    assert Settings(_env_file=None, app_env=AppEnv.TEST).llm_monthly_budget_usd >= 10.0


# ── provider fake fora de desenvolvimento ─────────────────────────────────────


@pytest.mark.parametrize("ambiente", [AppEnv.PRODUCTION, AppEnv.STAGING])
def test_provider_fake_nao_despacha_fora_de_desenvolvimento(
    db: Session, ambiente: AppEnv
) -> None:
    """Laudo fictício na tela é indistinguível de um real — pior modo de falha."""
    cfg = Settings(
        _env_file=None,
        app_env=ambiente,
        llm_dispatch_enabled=True,
        session_secret="prod-session-secret-32-chars-min!",
        postgres_password="prod-strong",
        pipeline_api_key="k",
        cors_allow_origins=["https://x"],
        openai_api_key="sk-x",
    )
    guard = LLMBudgetGuard(db, cfg)

    decisao = guard.avaliar_rodada(provider_efetivo="fake")

    assert not decisao
    assert decisao.motivo == MOTIVO_PROVIDER_FAKE


def test_provider_fake_e_aceito_em_test_e_development(db: Session) -> None:
    guard = LLMBudgetGuard(db, _cfg(openai_api_key=None))

    assert guard.avaliar_rodada(provider_efetivo="fake")


def test_provider_efetivo_vem_da_config_quando_nao_informado(db: Session) -> None:
    guard = LLMBudgetGuard(db, _cfg())

    assert guard.avaliar_rodada()  # openai_api_key presente → provider "openai"


# ── higiene ───────────────────────────────────────────────────────────────────


def test_custo_negativo_nao_reduz_o_acumulado(db: Session) -> None:
    guard = LLMBudgetGuard(db, _cfg())
    guard.avaliar_rodada()

    guard.registrar_chamada(-5.0)

    assert guard.custo_da_rodada == 0.0
    assert guard.chamadas == 1


def test_inicio_do_mes_e_utc() -> None:
    inicio = inicio_do_mes(datetime(2026, 8, 17, 13, 45, tzinfo=UTC))

    assert inicio == datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
