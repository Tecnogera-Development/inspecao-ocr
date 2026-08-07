"""papel do usuário + janela de primeira senha (código de uso único)


Duas mudanças em ``users``, para o mesmo destino: admin gerenciar usuários e
o próprio usuário definir a senha no primeiro acesso.

``role`` chega **NOT NULL com default 'operador'** — todo usuário existente
migra para operador, nunca para admin. Zero-downtime: ``ADD COLUMN ...
DEFAULT`` em Postgres 11+ não reescreve a tabela. Um ``CHECK`` garante no
banco que nenhum valor fora de ``admin``/``operador`` entra, mesmo por SQL
direto — não só por validação de aplicação (ver risco 1 do mapa: um admin
criado por acidente na janela sem código é o pior caso).

``password_hash`` passa a aceitar nulo: é o estado do usuário recém-criado,
antes de definir a própria senha. ``app.services.auth.authenticate()`` é
quem impede login nesse estado — ver ``0014`` × ``auth.py`` no mesmo commit.

As três colunas da janela de primeira senha guardam só o **hash** do código
de uso único (nunca o valor em claro — é credencial, risco 1 do mapa):
``password_setup_code_hash`` (bcrypt), ``password_setup_expires_at``
(janela de 30 min, timestamp com fuso) e ``password_setup_attempts``
(contador, para o ticket de rotas poder bloquear por tentativa).

O downgrade **não** volta ``password_hash`` para NOT NULL: se algum usuário
estiver na janela de primeira senha (hash nulo por desenho), reimpor NOT NULL
falharia ou exigiria inventar um valor — e o critério do ticket é "sem
perder dado". A tabela some ``role``/colunas da janela e mantém
``password_hash`` nullable também no downgrade.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_ROLE_CHECK = "ck_users_role_valido"


def upgrade() -> None:
    # ── papel ────────────────────────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("role", sa.String(16), nullable=False, server_default="operador"),
    )
    op.create_check_constraint(_ROLE_CHECK, "users", "role IN ('admin', 'operador')")

    # ── senha passa a poder faltar (janela de primeira senha) ─────────────────
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)

    # ── janela de primeira senha / reset — código de uso único, só o hash ────
    op.add_column("users", sa.Column("password_setup_code_hash", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("password_setup_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_setup_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "password_setup_attempts")
    op.drop_column("users", "password_setup_expires_at")
    op.drop_column("users", "password_setup_code_hash")

    # NÃO reimpõe NOT NULL em password_hash — ver nota no topo do arquivo.

    op.drop_constraint(_ROLE_CHECK, "users", type_="check")
    op.drop_column("users", "role")
