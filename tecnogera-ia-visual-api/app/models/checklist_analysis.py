"""Resultado da inspeção por vista — ticket ``mvp-c54-c57/08``.

Uma linha por (job, campo). A granularidade é a vista, não o checklist, por
três razões que se reforçam:

* **Atribuição.** O operador precisa ver *qual* foto gerou o achado. Um blob
  agregado no ``pipeline_job`` obrigaria a tela (ticket 09) a desempacotar JSON
  para descobrir de onde veio cada coisa.
* **Isolamento.** Uma vista que falha (download, timeout, JSON inválido) grava
  a própria linha com ``status='falhou'`` e as outras seguem. Sem linha por
  vista, uma falha derruba o checklist inteiro.
* **Custo medido.** ``cost_usd`` por linha é o que o teto de orçamento mensal
  soma (``app/services/llm_budget.py``). Custo por checklist é a soma das
  vistas — medido, não estimado.

O rollup mora em colunas de ``pipeline_jobs`` (migration 0011), não aqui: a
tela lista checklists e só abre as vistas de um por vez.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

#: ``status`` de uma vista.
#:  ``analisada``       — o modelo emitiu laudo (conforme ou não conforme)
#:  ``nao_processavel`` — barrada pela validação técnica OU pelo próprio modelo
#:  ``falhou``          — erro de infraestrutura; **não** é veredito sobre a foto
#:  ``nao_despachada``  — freio de gasto cortou antes da chamada
STATUS_ANALISADA = "analisada"
STATUS_NAO_PROCESSAVEL = "nao_processavel"
STATUS_FALHOU = "falhou"
STATUS_NAO_DESPACHADA = "nao_despachada"


class ChecklistViewResult(Base):
    """Laudo de UMA vista de um checklist."""

    __tablename__ = "checklist_view_results"
    __table_args__ = (
        # Reprocessar um job não pode duplicar a vista: a linha é atualizada.
        UniqueConstraint("job_id", "campo", name="uq_checklist_view_results_job_campo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pipeline_jobs.id"), nullable=False, index=True
    )
    checklist_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Código do campo Sisloc (``c54``…``c57``) — a vista.
    campo: Mapped[str] = mapped_column(String(16), nullable=False)
    dropbox_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    conformidade: Mapped[str | None] = mapped_column(String(24), nullable=True)
    motivo_nao_processavel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Métrica de alarme do dicionário de campos (taxonomia v0.2 §8), não veredito
    #: sobre o equipamento: se subir em volume, o mapa campo→vista está errado.
    vista_confere: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    conteudo_observado: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Lista completa de achados (schema em ``view_inspection.Achado``).
    achados: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    #: Denormalizados do achado principal — a tela ordena e filtra por eles sem
    #: abrir o JSON.
    severidade_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classe: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tipo_defeito: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confianca: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Custo MEDIDO desta chamada. É a coluna que o teto mensal soma.
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Gabarito humano (HITL) — ticket mvp-c54-c57/10, migration 0013 ────────
    # O gabarito mora AQUI, na mesma linha da predição, e não numa tabela de
    # anotações. Três razões:
    #
    # * **Unidade.** O laudo é por vista; o gabarito também. Uma linha por
    #   (job, campo) já existe e o ``UniqueConstraint`` acima é a garantia de
    #   idempotência **de graça**: validar duas vezes é UPDATE da mesma linha,
    #   nunca INSERT — não há como duplicar registro nem inflar a métrica.
    # * **Eval sem join.** P/R/F1 é (predito, verdadeiro) lado a lado; numa
    #   tabela separada seria join, e um join com linha órfã vira registro que
    #   entra na conta sem predição.
    # * **Ausência é o estado inicial.** ``gt_classe IS NULL`` significa "ainda
    #   não validado" sem precisar de linha nenhuma — pendência não é dado.
    #
    # O preço é não haver histórico de quem validou o quê ao longo do tempo:
    # a última palavra sobrescreve. É o que o ticket pede ("validar duas vezes
    # não duplica registro"); log de auditoria seria outro requisito.

    #: Classe VERDADEIRA na projeção do eval — ``conforme`` e ``nao_processavel``
    #: são pseudo-classes ao lado das três reais (``ausencia_item``,
    #: ``fora_padrao_visual``, ``dano_visivel``). ``None`` = vista não validada.
    #: Colapsar ``nao_processavel`` em ``conforme`` aqui faria a métrica premiar
    #: o modelo por acertar um "está tudo bem" que ninguém conseguiu julgar.
    gt_classe: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Severidade verdadeira (1 = pior). ``None`` quando o gabarito é conforme
    #: ou não processável.
    gt_severidade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``None`` ⇒ o operador CONFIRMOU o laudo desta vista. Preenchido ⇒
    #: corrigiu, e este é **o que** estava errado — o insumo de calibragem do
    #: prompt. "Corrigido" sem tipo só serve para contar.
    gt_tipo_erro: Mapped[str | None] = mapped_column(String(24), nullable=True)
    gt_observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    validado_por: Mapped[str | None] = mapped_column(String(255), nullable=True)
    validado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
