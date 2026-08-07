"""Serviço de gerenciamento de usuários — ticket ``usuarios-portal/02``.

Testes de dado real (SQLite em memória, mesmo padrão de
``tests/unit/test_user_role_e_senha.py``), sem TestClient: a lógica de
"o que conta como tentativa" e a garantia de uso único do código merecem
teste isolado do transporte HTTP.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import bcrypt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.user import User
from app.services.user_management import (
    MAX_PASSWORD_SETUP_ATTEMPTS,
    PASSWORD_SETUP_WINDOW_MINUTES,
    consumir_codigo_definir_senha,
    generate_setup_code,
    open_password_setup_window,
)

pytestmark = pytest.mark.unit

_CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _novo_usuario(db: Session, email: str = "novato@tecnogera.com", **kwargs: object) -> User:
    kwargs.setdefault("password_hash", None)
    user = User(email=email, **kwargs)  # type: ignore[arg-type]
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _aware(momento: datetime) -> datetime:
    """SQLite devolve datetime naive no refresh — normaliza pra comparar em teste."""
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


# ── geração do código ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_codigo_gerado_tem_o_formato_esperado() -> None:
    codigo = generate_setup_code()
    assert _CODE_RE.match(codigo), codigo


@pytest.mark.unit
def test_codigo_gerado_nao_repete_em_sequencia() -> None:
    """Sanity check de CSPRNG: 50 códigos seguidos, nenhuma colisão."""
    codigos = {generate_setup_code() for _ in range(50)}
    assert len(codigos) == 50


# ── abrir a janela ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_abrir_janela_grava_so_o_hash_do_codigo(db: Session) -> None:
    user = _novo_usuario(db)
    opened = open_password_setup_window(db, user)

    assert opened.code
    assert user.password_setup_code_hash is not None
    assert user.password_setup_code_hash != opened.code
    assert bcrypt.checkpw(opened.code.encode(), user.password_setup_code_hash.encode())


@pytest.mark.unit
def test_abrir_janela_define_expiracao_de_30_minutos(db: Session) -> None:
    user = _novo_usuario(db)
    antes = datetime.now(UTC)
    open_password_setup_window(db, user)
    depois = datetime.now(UTC)

    assert user.password_setup_expires_at is not None
    esperado_min = antes + timedelta(minutes=PASSWORD_SETUP_WINDOW_MINUTES)
    esperado_max = depois + timedelta(minutes=PASSWORD_SETUP_WINDOW_MINUTES)
    assert esperado_min <= _aware(user.password_setup_expires_at) <= esperado_max


@pytest.mark.unit
def test_abrir_janela_zera_tentativas(db: Session) -> None:
    user = _novo_usuario(db)
    user.password_setup_attempts = 3
    db.commit()

    open_password_setup_window(db, user)

    assert user.password_setup_attempts == 0


@pytest.mark.unit
def test_reabrir_janela_invalida_o_codigo_anterior(db: Session) -> None:
    """Reset gera código novo — o antigo para de funcionar mesmo dentro da janela."""
    user = _novo_usuario(db)
    primeiro = open_password_setup_window(db, user)
    open_password_setup_window(db, user)  # reabre — código novo substitui o antigo

    resultado = consumir_codigo_definir_senha(
        db, email=user.email, codigo=primeiro.code, nova_senha="senha-nova-123"
    )
    assert resultado is None


@pytest.mark.unit
def test_abrir_janela_zera_password_hash(db: Session) -> None:
    """É o mecanismo por trás de "resetar derruba a sessão ativa" — ver
    app/routers/usuarios.py e app/routers/portal.py::current_user.
    """
    hashed = bcrypt.hashpw(b"senha-antiga", bcrypt.gensalt()).decode()
    user = _novo_usuario(db, email="comsenha@tecnogera.com", password_hash=hashed)
    assert user.password_hash is not None

    open_password_setup_window(db, user)

    assert user.password_hash is None


# ── consumir o código ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_consumir_codigo_correto_grava_senha_e_zera_janela(db: Session) -> None:
    user = _novo_usuario(db)
    opened = open_password_setup_window(db, user)

    resultado = consumir_codigo_definir_senha(
        db, email=user.email, codigo=opened.code, nova_senha="senha-nova-123"
    )

    assert resultado is not None
    assert resultado.id == user.id
    assert user.password_hash is not None
    assert bcrypt.checkpw(b"senha-nova-123", user.password_hash.encode())
    assert user.password_setup_code_hash is None
    assert user.password_setup_expires_at is None
    assert user.password_setup_attempts == 0


@pytest.mark.unit
def test_codigo_usado_nao_funciona_de_novo(db: Session) -> None:
    """Uso único de verdade: a segunda tentativa com o MESMO código falha."""
    user = _novo_usuario(db)
    opened = open_password_setup_window(db, user)

    primeira = consumir_codigo_definir_senha(
        db, email=user.email, codigo=opened.code, nova_senha="senha-nova-123"
    )
    segunda = consumir_codigo_definir_senha(
        db, email=user.email, codigo=opened.code, nova_senha="outra-senha-456"
    )

    assert primeira is not None
    assert segunda is None
    # a senha gravada na primeira tentativa continua valendo — a segunda
    # tentativa (que falhou) não a sobrescreveu
    assert user.password_hash is not None
    assert bcrypt.checkpw(b"senha-nova-123", user.password_hash.encode())


@pytest.mark.unit
def test_janela_expirada_recusa(db: Session) -> None:
    user = _novo_usuario(db)
    opened = open_password_setup_window(db, user)
    user.password_setup_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    resultado = consumir_codigo_definir_senha(
        db, email=user.email, codigo=opened.code, nova_senha="senha-nova-123"
    )

    assert resultado is None
    assert user.password_hash is None  # não define senha em janela expirada


@pytest.mark.unit
def test_codigo_errado_incrementa_tentativas(db: Session) -> None:
    user = _novo_usuario(db)
    open_password_setup_window(db, user)
    assert user.password_setup_attempts == 0

    consumir_codigo_definir_senha(
        db, email=user.email, codigo="ERRADO-01", nova_senha="senha-nova-123"
    )

    assert user.password_setup_attempts == 1


@pytest.mark.unit
def test_tentativas_estouradas_bloqueiam_mesmo_codigo_correto(db: Session) -> None:
    """Depois de MAX_PASSWORD_SETUP_ATTEMPTS erros, nem o código certo funciona
    mais — só um reset do admin (nova janela) reabre o caminho.
    """
    user = _novo_usuario(db)
    opened = open_password_setup_window(db, user)

    for _ in range(MAX_PASSWORD_SETUP_ATTEMPTS):
        resultado = consumir_codigo_definir_senha(
            db, email=user.email, codigo="ERRADO-01", nova_senha="x"
        )
        assert resultado is None

    assert user.password_setup_attempts == MAX_PASSWORD_SETUP_ATTEMPTS

    # mesmo com o código CERTO, a janela já morreu
    resultado_com_codigo_certo = consumir_codigo_definir_senha(
        db, email=user.email, codigo=opened.code, nova_senha="senha-nova-123"
    )
    assert resultado_com_codigo_certo is None
    assert user.password_hash is None


@pytest.mark.unit
def test_usuario_inativo_nao_define_senha_mesmo_com_codigo_valido(db: Session) -> None:
    user = _novo_usuario(db)
    opened = open_password_setup_window(db, user)
    user.is_active = False
    db.commit()

    resultado = consumir_codigo_definir_senha(
        db, email=user.email, codigo=opened.code, nova_senha="senha-nova-123"
    )

    assert resultado is None
    assert user.password_hash is None


@pytest.mark.unit
def test_email_inexistente_recusa_sem_diferenca_de_motivo(db: Session) -> None:
    """Não há linha nenhuma pra esse e-mail — devolve None do mesmo jeito que
    qualquer outra falha (ticket 02, risco 2: não vira oráculo de e-mail).
    """
    resultado = consumir_codigo_definir_senha(
        db, email="ninguem@tecnogera.com", codigo="QUALQUER-1", nova_senha="senha-nova-123"
    )
    assert resultado is None


@pytest.mark.unit
def test_usuario_sem_janela_aberta_recusa(db: Session) -> None:
    """Usuário existe e está ativo, mas nunca teve (ou já usou) o código."""
    user = _novo_usuario(db)
    resultado = consumir_codigo_definir_senha(
        db, email=user.email, codigo="QUALQUER-1", nova_senha="senha-nova-123"
    )
    assert resultado is None
