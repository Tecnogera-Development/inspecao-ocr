"""Modelos de pipeline_jobs — ORM SQLAlchemy + schemas Pydantic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.sisloc import SislocChecklist

#: ``JSONB`` no Postgres (o banco real), ``JSON`` genérico no SQLite dos testes.
#: JSONB de propósito: o ``metrics`` acima usa ``JSON`` puro, o que é um erro
#: existente — sem binário não há índice GIN nem operador de contenção, e cada
#: leitura reparseia o texto. Não replicar.
_JSONB = JSON().with_variant(JSONB, "postgresql")

JobStatus = Literal["pending", "running", "done", "failed", "pending_batch"]
JobMode = Literal["sync", "batch"]


class PipelineJob(Base):
    """Registro de execução do pipeline E2E.

    Status válidos: pending, running, done, failed, pending_batch.
    Mode válidos: sync (default), batch.
    State machine batch: pending → running → pending_batch → running → done.
    """

    __tablename__ = "pipeline_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    checklist_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="sync")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    batch_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    batch_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Rollup da inspeção visual (ticket mvp-c54-c57/08, migration 0011) ─────
    # O rollup é a PIOR vista, e `vista_determinante` diz qual foi. Sem essa
    # coluna a tela do operador (ticket 09) teria de recalcular o pior caso a
    # cada render — e o rollup exibido poderia divergir do que foi decidido.
    conformidade: Mapped[str | None] = mapped_column(String(24), nullable=True)
    severidade_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vista_determinante: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: CSV das vistas efetivamente recebidas ("c54,c55,c56"). Três vistas é o
    #: caso NORMAL — o F180 não emite `c57` desde set/2025.
    vistas_recebidas: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Custo medido do checklist = soma das vistas. Não é estimativa.
    llm_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Enriquecimento do Sisloc (ticket mvp-c54-c57/17, migration 0012) ──────
    # Persistência HÍBRIDA. Tipadas e indexadas só as três que a **aplicação
    # consulta** — filtro por formulário, busca do operador pelo ativo, busca
    # por cliente. `filial`, `responsavel` e `data_conclusao` são exibição, e
    # exibição sai do snapshot sem custo. Promover do snapshot para coluna
    # tipada depois é migration trivial de backfill; despromover é caro.
    formulario: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    patrimonio: Mapped[str | None] = mapped_column(String(15), nullable=True, index=True)
    #: Valor BRUTO de `projeto` (`<contrato>/<ano>-<CLIENTE>`). O parse em
    #: contrato/cliente vive no snapshot: 0,03% dos valores não casam com o
    #: padrão e o bruto é a única forma de não perdê-los.
    projeto: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    #: Linhas que a view tinha para este `codigo_checklist`. `> 1` ⇒ o checklist
    #: cobre mais de um ativo (78 casos medidos divergem em `patrimonio`) e a
    #: tela precisa avisar. Coluna tipada porque o aviso é uma consulta, não uma
    #: leitura de detalhe.
    n_linhas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Congelamento das 11 colunas + `lido_em`, validado por `SislocSnapshot`.
    #: O ERP muda; o laudo não deveria.
    sisloc_snapshot: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)

    # ── Validação humana / HITL (ticket mvp-c54-c57/10, migration 0013) ───────
    # O gabarito é por VISTA (`checklist_view_results.gt_*`); estas três colunas
    # são o **rollup da validação**, e existem por um motivo de consulta, não de
    # verdade: a lista filtra por `validacao` e conta "a validar" em SQL, e
    # derivar isso das linhas de vista a cada render seria um join por checklist.
    # Para não divergir, o valor é SEMPRE recalculado a partir das vistas em
    # `checklist_validation.recalcular_validacao()` — nunca escrito à mão.
    #: ``None``/fora do enum ⇒ `pendente`. Ver `checklist_query.VALIDACOES`.
    validacao: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    validado_por: Mapped[str | None] = mapped_column(String(255), nullable=True)
    validado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def novo_job_enriquecido(
    *,
    job_id: uuid.UUID,
    checklist_id: str,
    linha: SislocChecklist | None,
    status: str = "pending",
    mode: str = "sync",
) -> PipelineJob:
    """Cria um ``PipelineJob`` já com o enriquecimento do Sisloc.

    Fábrica única para os dois caminhos que materializam job — o cron
    (``checklist_ingestion``) e o backfill sob demanda
    (``checklist_backfill``). Existe para que a decisão "o que é coluna tipada e
    o que vai para o snapshot" tenha **um** lugar: duplicá-la faria o backfill
    gravar um job mais pobre que o do cron, e ninguém notaria até a tela.

    ``linha=None`` só acontece quando o job nasce sem linha no ERP — caminho que
    o filtro não permite, mas que ``POST /pipeline/run`` permite de propósito.
    """
    job = PipelineJob(id=job_id, checklist_id=checklist_id, status=status, mode=mode)
    if linha is None:
        return job
    job.formulario = linha.formulario or None
    job.patrimonio = linha.patrimonio
    job.projeto = linha.projeto  # BRUTO — o parse vive no snapshot
    job.n_linhas = linha.n_linhas
    job.sisloc_snapshot = linha.snapshot().como_json()
    return job


# ── Pydantic response schemas ──────────────────────────────────────────────

class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checklist_id: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result_pdf_path: str | None = None
    metrics: dict[str, Any] | None = None
    conformidade: str | None = None
    severidade_max: int | None = None
    vista_determinante: str | None = None
    vistas_recebidas: str | None = None
    llm_cost_usd: float = 0.0
    llm_calls: int = 0
    formulario: str | None = None
    patrimonio: str | None = None
    projeto: str | None = None
    n_linhas: int | None = None
    sisloc_snapshot: dict[str, Any] | None = None
