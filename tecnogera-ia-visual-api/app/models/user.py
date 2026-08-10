"""Modelo ORM User para autenticação do portal."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from app.db.base import Base

#: Papéis do portal. A única diferença entre eles é poder gerenciar
#: usuários. Nada de RBAC fino (por filial, por formulário, por ação).
ROLE_ADMIN = "admin"
ROLE_OPERADOR = "operador"
ROLES = frozenset({ROLE_ADMIN, ROLE_OPERADOR})


class User(Base):
    """Usuário do portal admin."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"role IN ('{ROLE_ADMIN}', '{ROLE_OPERADOR}')", name="ck_users_role_valido"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # Nulo: usuário recém-criado ainda não definiu senha própria (janela de
    # primeira senha / reset). authenticate() é quem garante que ele não loga
    # assim mesmo — ver app/services/auth.py.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ROLE_OPERADOR, server_default=ROLE_OPERADOR
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── janela de primeira senha / reset (30 min, código de uso único) ─────
    # O código NUNCA é gravado em claro — é credencial (risco 1 do mapa). Só
    # o hash bcrypt mora aqui; o valor em claro existe só na memória do
    # processo que gera o código e no repasse fora de banda pelo admin.
    password_setup_code_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_setup_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_setup_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
