"""Testes do rate limiting em POST /login — ticket usuarios-portal/03.

Arquivo separado de ``test_portal_auth.py`` de propósito: usa limites
propositalmente baixos na fixture de Settings, o que colidiria com os testes
de login "normais" (que fazem várias chamadas de login/logout em sequência)
se estivessem no mesmo módulo.
"""

from __future__ import annotations

from collections.abc import Generator

import bcrypt
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

IDENTITY_MAX_ATTEMPTS = 3
ORIGIN_MAX_ATTEMPTS = 5


@pytest.fixture
def rate_limit_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        session_secret="test-secret-key-32-chars-minimum!",
        login_rate_limit_identity_max_attempts=IDENTITY_MAX_ATTEMPTS,
        login_rate_limit_identity_window_seconds=900,
        login_rate_limit_origin_max_attempts=ORIGIN_MAX_ATTEMPTS,
        login_rate_limit_origin_window_seconds=900,
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


def _make_user(db: Session, email: str = "celio@tecnogera.com", password: str = "s3cr3t") -> User:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(email=email, password_hash=hashed, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str, password: str, ip: str | None = None):
    headers = {"CF-Connecting-IP": ip} if ip else {}
    return client.post(
        "/api/v1/portal/login",
        json={"email": email, "password": password},
        headers=headers,
    )


# ── critério de aceite central: sucesso nunca bloqueia ───────────────────────


@pytest.mark.unit
def test_login_valido_repetido_nunca_e_bloqueado(client: TestClient, db: Session) -> None:
    """É o modo de falha citado no ticket: login legítimo não pode 429."""
    _make_user(db)
    # roda bem mais vezes que o limite de identidade E de origem
    for _ in range(3 * max(IDENTITY_MAX_ATTEMPTS, ORIGIN_MAX_ATTEMPTS)):
        resp = _login(client, "celio@tecnogera.com", "s3cr3t", ip="203.0.113.9")
        assert resp.status_code == 200, resp.json()


# ── dimensão identidade ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_login_bloqueia_por_identidade_apos_limite(client: TestClient, db: Session) -> None:
    _make_user(db)
    # cada tentativa vem de uma origem DIFERENTE — só a identidade se repete
    for i in range(IDENTITY_MAX_ATTEMPTS):
        resp = _login(client, "celio@tecnogera.com", "errada", ip=f"203.0.113.{i}")
        assert resp.status_code == 401

    resp = _login(client, "celio@tecnogera.com", "s3cr3t", ip="203.0.113.99")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.unit
def test_login_bloqueio_por_identidade_nao_revela_email_existente(
    client: TestClient, db: Session
) -> None:
    _make_user(db, email="existe@tecnogera.com")

    for i in range(IDENTITY_MAX_ATTEMPTS):
        resp_existe = _login(client, "existe@tecnogera.com", "errada", ip=f"198.51.100.{i}")
        assert resp_existe.status_code == 401
    for i in range(IDENTITY_MAX_ATTEMPTS):
        resp_inexiste = _login(
            client, "naoexiste@tecnogera.com", "qualquer", ip=f"198.51.100.{20 + i}"
        )
        assert resp_inexiste.status_code == 401

    resp_bloqueado_existe = _login(client, "existe@tecnogera.com", "errada", ip="198.51.100.201")
    resp_bloqueado_inexiste = _login(
        client, "naoexiste@tecnogera.com", "errada", ip="198.51.100.202"
    )

    assert resp_bloqueado_existe.status_code == 429
    assert resp_bloqueado_inexiste.status_code == 429
    # a mensagem de 429 é idêntica nos dois casos — não dá pra distinguir
    # e-mail existente de inexistente pela resposta do rate limit
    assert resp_bloqueado_existe.json() == resp_bloqueado_inexiste.json()


# ── dimensão origem (IP) ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_login_bloqueia_por_origem_varrendo_identidades_diferentes(
    client: TestClient, db: Session
) -> None:
    """Protege contra spray: muitas contas diferentes tentadas do mesmo IP."""
    _make_user(db, email="alvo@tecnogera.com")
    same_ip = "192.0.2.50"

    for i in range(ORIGIN_MAX_ATTEMPTS):
        resp = _login(client, f"inexiste-{i}@tecnogera.com", "qualquer", ip=same_ip)
        assert resp.status_code == 401

    # nova identidade nunca vista, mesmo IP — bloqueado pela ORIGEM
    resp = _login(client, "alvo@tecnogera.com", "s3cr3t", ip=same_ip)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.unit
def test_login_origem_diferente_nao_e_afetada(client: TestClient, db: Session) -> None:
    _make_user(db, email="alvo@tecnogera.com")
    origem_atacante = "192.0.2.77"

    for i in range(ORIGIN_MAX_ATTEMPTS):
        resp = _login(client, f"inexiste-{i}@tecnogera.com", "qualquer", ip=origem_atacante)
        assert resp.status_code == 401

    # IP diferente, mesmíssima conta — não deveria ter sido tocado
    resp = _login(client, "alvo@tecnogera.com", "s3cr3t", ip="203.0.113.200")
    assert resp.status_code == 200


# ── CF-Connecting-IP não pode ser confundido com X-Forwarded-For ────────────


@pytest.mark.unit
def test_x_forwarded_for_nao_e_usado_para_origem(client: TestClient, db: Session) -> None:
    """Um X-Forwarded-For forjado não deve influenciar o bucket de origem.

    Sem CF-Connecting-IP, o fallback é o peer direto do TestClient (sempre o
    mesmo, "testclient"), então requisições com X-Forwarded-For diferentes
    ainda compartilham o mesmo bucket de origem — provando que o código não
    lê X-Forwarded-For para essa decisão.
    """
    _make_user(db, email="alvo@tecnogera.com")

    for i in range(ORIGIN_MAX_ATTEMPTS):
        resp = client.post(
            "/api/v1/portal/login",
            json={"email": f"inexiste-{i}@tecnogera.com", "password": "qualquer"},
            headers={"X-Forwarded-For": f"9.9.9.{i}"},  # forjado, ignorado de propósito
        )
        assert resp.status_code == 401

    resp = client.post(
        "/api/v1/portal/login",
        json={"email": "alvo@tecnogera.com", "password": "s3cr3t"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    # bloqueado — todas as chamadas acima caíram no MESMO bucket (peer direto
    # do TestClient), porque X-Forwarded-For não é lido para essa decisão
    assert resp.status_code == 429
