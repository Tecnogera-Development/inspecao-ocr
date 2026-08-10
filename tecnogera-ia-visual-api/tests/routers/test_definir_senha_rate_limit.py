"""Rate limiting em POST /definir-senha — ticket usuarios-portal/02.

Arquivo separado de ``test_usuarios.py`` de propósito, mesmo motivo do
``test_portal_login_rate_limit.py`` do ticket 03: usa limites propositalmente
baixos, o que colidiria com os testes "normais" (que abrem várias janelas em
sequência) se estivessem no mesmo módulo.

Reaproveita o MESMO motor do login (``app/core/ratelimit.py``,
``check_password_setup_rate_limit`` / ``record_password_setup_*``) — nenhum
limitador novo foi escrito para esta rota, só uma segunda instância do par
já existente (ver docstring de ``app/core/ratelimit.py``).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.user import User
from app.services.user_management import open_password_setup_window

IDENTITY_MAX_ATTEMPTS = 3
ORIGIN_MAX_ATTEMPTS = 5

DEFINIR_SENHA = "/api/v1/portal/definir-senha"

pytestmark = pytest.mark.unit


@pytest.fixture
def rate_limit_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        session_secret="test-secret-key-32-chars-minimum!",
        password_setup_rate_limit_identity_max_attempts=IDENTITY_MAX_ATTEMPTS,
        password_setup_rate_limit_identity_window_seconds=900,
        password_setup_rate_limit_origin_max_attempts=ORIGIN_MAX_ATTEMPTS,
        password_setup_rate_limit_origin_window_seconds=900,
        # tentativas persistidas (password_setup_attempts) bem folgadas — só
        # o rate limiter deve ser o motivo do bloqueio nestes testes
        login_rate_limit_identity_max_attempts=1000,
        login_rate_limit_origin_max_attempts=1000,
    )


@pytest.fixture
def sqlite_engine():
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
def db(sqlite_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(rate_limit_settings: Settings, db: Session) -> TestClient:
    def _override_db():
        yield db

    app = create_app(rate_limit_settings)
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: rate_limit_settings
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


def _make_user_com_janela(
    db: Session, email: str = "celio@tecnogera.com", *, ativo: bool = True
) -> tuple[User, str]:
    user = User(email=email, password_hash=None, is_active=ativo)
    db.add(user)
    db.commit()
    db.refresh(user)
    opened = open_password_setup_window(db, user)
    return user, opened.code


def _definir_senha(
    client: TestClient,
    email: str,
    codigo: str,
    senha: str = "senha-teste-12",
    ip: str | None = None,
):
    headers = {"CF-Connecting-IP": ip} if ip else {}
    return client.post(
        DEFINIR_SENHA, json={"email": email, "codigo": codigo, "senha": senha}, headers=headers
    )


# ── sucesso nunca bloqueia ────────────────────────────────────────────────────


@pytest.mark.unit
def test_sucesso_nao_bloqueia_definicoes_seguintes_de_outras_contas(
    client: TestClient, db: Session
) -> None:
    """Cada sucesso é uma conta diferente — sucesso não deve consumir o
    orçamento de tentativas da identidade seguinte nem travar a origem.
    """
    for i in range(2 * max(IDENTITY_MAX_ATTEMPTS, ORIGIN_MAX_ATTEMPTS)):
        _, codigo = _make_user_com_janela(db, email=f"conta{i}@tecnogera.com")
        resp = _definir_senha(client, f"conta{i}@tecnogera.com", codigo, ip="203.0.113.9")
        assert resp.status_code == 200, resp.json()


# ── dimensão identidade ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_bloqueia_por_identidade_apos_limite(client: TestClient, db: Session) -> None:
    _, codigo = _make_user_com_janela(db, email="alvo@tecnogera.com")

    for i in range(IDENTITY_MAX_ATTEMPTS):
        resp = _definir_senha(client, "alvo@tecnogera.com", "codigo-errado", ip=f"203.0.113.{i}")
        assert resp.status_code == 400

    resp = _definir_senha(client, "alvo@tecnogera.com", codigo, ip="203.0.113.99")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.unit
def test_bloqueio_por_identidade_nao_revela_email_existente(
    client: TestClient, db: Session
) -> None:
    _make_user_com_janela(db, email="existe@tecnogera.com")

    for i in range(IDENTITY_MAX_ATTEMPTS):
        resp_existe = _definir_senha(client, "existe@tecnogera.com", "errado", ip=f"198.51.100.{i}")
        assert resp_existe.status_code == 400
    for i in range(IDENTITY_MAX_ATTEMPTS):
        resp_inexiste = _definir_senha(
            client, "naoexiste@tecnogera.com", "qualquer", ip=f"198.51.100.{20 + i}"
        )
        assert resp_inexiste.status_code == 400

    resp_bloqueado_existe = _definir_senha(
        client, "existe@tecnogera.com", "errado", ip="198.51.100.201"
    )
    resp_bloqueado_inexiste = _definir_senha(
        client, "naoexiste@tecnogera.com", "errado", ip="198.51.100.202"
    )

    assert resp_bloqueado_existe.status_code == 429
    assert resp_bloqueado_inexiste.status_code == 429
    assert resp_bloqueado_existe.json() == resp_bloqueado_inexiste.json()


# ── dimensão origem (IP) ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_bloqueia_por_origem_varrendo_identidades_diferentes(
    client: TestClient, db: Session
) -> None:
    same_ip = "192.0.2.50"
    _, codigo = _make_user_com_janela(db, email="alvo-origem@tecnogera.com")

    for i in range(ORIGIN_MAX_ATTEMPTS):
        resp = _definir_senha(client, f"inexiste-{i}@tecnogera.com", "qualquer", ip=same_ip)
        assert resp.status_code == 400

    resp = _definir_senha(client, "alvo-origem@tecnogera.com", codigo, ip=same_ip)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.unit
def test_origem_diferente_nao_e_afetada(client: TestClient, db: Session) -> None:
    origem_atacante = "192.0.2.77"
    _, codigo = _make_user_com_janela(db, email="alvo-livre@tecnogera.com")

    for i in range(ORIGIN_MAX_ATTEMPTS):
        resp = _definir_senha(client, f"inexiste-{i}@tecnogera.com", "qualquer", ip=origem_atacante)
        assert resp.status_code == 400

    resp = _definir_senha(client, "alvo-livre@tecnogera.com", codigo, ip="203.0.113.200")
    assert resp.status_code == 200
