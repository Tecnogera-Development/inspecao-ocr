"""Rotas de gerenciamento de usuários e primeira senha — ticket ``usuarios-portal/02``.

O que estes testes protegem, em ordem de quanto dói errar:

1. **O código de uso único vaza.** Só ``POST /usuarios`` e
   ``POST /usuarios/{id}/resetar-senha`` podem devolvê-lo; nenhuma outra
   resposta — nem ``GET /usuarios``, nem ``/me``, nem ``/login`` — pode
   conter o valor em claro.
2. **`require_admin` não é decorativo.** Operador recebe 403 em toda rota de
   gerenciamento, rota a rota.
3. **A resposta de `/definir-senha` não vira oráculo de e-mail.**
4. **Admin não fica sem admin.** Auto-inativação bloqueada; pelo menos um
   admin ativo sempre sobra.
5. **A decisão do ticket — reset derruba sessão ativa — está implementada.**
"""

from __future__ import annotations

import re
from collections.abc import Generator

import bcrypt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.user import ROLE_ADMIN, ROLE_OPERADOR, User
from app.routers.usuarios import _outros_admins_ativos, inativar_usuario

pytestmark = pytest.mark.unit

USUARIOS = "/api/v1/portal/usuarios"
DEFINIR_SENHA = "/api/v1/portal/definir-senha"
_CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        session_secret="test-secret-key-32-chars-minimum!",
        # limites bem folgados — estes testes exercitam a REGRA DE NEGÓCIO
        # (password_setup_attempts), não o rate limiter; o rate limiter tem
        # arquivo próprio (test_definir_senha_rate_limit.py) com limites
        # baixos de propósito.
        password_setup_rate_limit_identity_max_attempts=1000,
        password_setup_rate_limit_origin_max_attempts=1000,
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
def client(settings: Settings, db: Session) -> TestClient:
    def _override_db():
        yield db

    app = create_app(settings)
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


def _criar_usuario_direto(
    db: Session, email: str, *, role: str = ROLE_OPERADOR, senha: str | None = "s3cr3t"
) -> User:
    hashed = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode() if senha else None
    user = User(email=email, password_hash=hashed, role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str, password: str) -> TestClient:
    resp = client.post("/api/v1/portal/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.json()
    return client


@pytest.fixture
def admin(db: Session) -> User:
    return _criar_usuario_direto(db, "admin@tecnogera.com", role=ROLE_ADMIN, senha="admin-s3cr3t")


@pytest.fixture
def logado_admin(client: TestClient, admin: User) -> TestClient:
    return _login(client, admin.email, "admin-s3cr3t")


@pytest.fixture
def operador(db: Session) -> User:
    return _criar_usuario_direto(
        db, "operador@tecnogera.com", role=ROLE_OPERADOR, senha="op-s3cr3t"
    )


@pytest.fixture
def logado_operador(client: TestClient, operador: User) -> TestClient:
    return _login(client, operador.email, "op-s3cr3t")


def _csrf(client: TestClient) -> str:
    return client.get("/api/v1/portal/csrf").json()["token"]


# ── require_admin: 403 rota a rota para operador ────────────────────────────


@pytest.mark.unit
def test_operador_403_em_listar_usuarios(logado_operador: TestClient) -> None:
    resp = logado_operador.get(USUARIOS)
    assert resp.status_code == 403


@pytest.mark.unit
def test_operador_403_em_criar_usuario(logado_operador: TestClient) -> None:
    token = _csrf(logado_operador)
    resp = logado_operador.post(
        USUARIOS,
        json={"email": "novo@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 403


@pytest.mark.unit
def test_operador_403_em_inativar(logado_operador: TestClient, admin: User) -> None:
    token = _csrf(logado_operador)
    resp = logado_operador.post(f"{USUARIOS}/{admin.id}/inativar", headers={"X-CSRF-Token": token})
    assert resp.status_code == 403


@pytest.mark.unit
def test_operador_403_em_reativar(logado_operador: TestClient, admin: User) -> None:
    token = _csrf(logado_operador)
    resp = logado_operador.post(f"{USUARIOS}/{admin.id}/reativar", headers={"X-CSRF-Token": token})
    assert resp.status_code == 403


@pytest.mark.unit
def test_operador_403_em_resetar_senha(logado_operador: TestClient, admin: User) -> None:
    token = _csrf(logado_operador)
    resp = logado_operador.post(
        f"{USUARIOS}/{admin.id}/resetar-senha", headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 403


@pytest.mark.unit
def test_anonimo_401_em_todas_as_rotas_de_admin(client: TestClient, admin: User) -> None:
    assert client.get(USUARIOS).status_code == 401
    assert client.post(USUARIOS, json={"email": "x@tecnogera.com"}).status_code == 401
    assert client.post(f"{USUARIOS}/{admin.id}/inativar").status_code == 401
    assert client.post(f"{USUARIOS}/{admin.id}/reativar").status_code == 401
    assert client.post(f"{USUARIOS}/{admin.id}/resetar-senha").status_code == 401


# ── CSRF nas rotas de escrita ────────────────────────────────────────────────


@pytest.mark.unit
def test_criar_usuario_sem_csrf_403(logado_admin: TestClient) -> None:
    resp = logado_admin.post(USUARIOS, json={"email": "novo@tecnogera.com"})
    assert resp.status_code == 403


@pytest.mark.unit
def test_resetar_senha_sem_csrf_403(logado_admin: TestClient, operador: User) -> None:
    resp = logado_admin.post(f"{USUARIOS}/{operador.id}/resetar-senha")
    assert resp.status_code == 403


# ── criação: o código só existe nesta resposta ──────────────────────────────


@pytest.mark.unit
def test_criar_usuario_devolve_codigo_em_claro(logado_admin: TestClient) -> None:
    token = _csrf(logado_admin)
    resp = logado_admin.post(
        USUARIOS,
        json={"email": "convidado@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert _CODE_RE.match(body["codigo"]), body["codigo"]
    assert body["email"] == "convidado@tecnogera.com"
    assert body["role"] == "operador"


@pytest.mark.unit
def test_criar_usuario_papel_invalido_422(logado_admin: TestClient) -> None:
    token = _csrf(logado_admin)
    resp = logado_admin.post(
        USUARIOS,
        json={"email": "x@tecnogera.com", "role": "superadmin"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 422


@pytest.mark.unit
def test_criar_usuario_email_duplicado_409(logado_admin: TestClient, operador: User) -> None:
    token = _csrf(logado_admin)
    resp = logado_admin.post(
        USUARIOS,
        json={"email": operador.email, "role": "operador"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 409


# ── o código NÃO aparece em nenhuma outra resposta ──────────────────────────


@pytest.mark.unit
def test_codigo_nao_vaza_em_nenhuma_outra_rota(
    logado_admin: TestClient, admin: User, operador: User
) -> None:
    """Cria um usuário, guarda o código, e varre um monte de outras respostas
    HTTP do módulo procurando esse valor — não pode aparecer em nenhuma.
    """
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "vazamento@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()
    codigo = criado["codigo"]
    novo_id = criado["id"]

    outras_respostas = [
        logado_admin.get(USUARIOS),
        logado_admin.get("/api/v1/portal/me"),
        logado_admin.post(f"{USUARIOS}/{novo_id}/inativar", headers={"X-CSRF-Token": token}),
        logado_admin.post(f"{USUARIOS}/{novo_id}/reativar", headers={"X-CSRF-Token": token}),
        logado_admin.post(f"{USUARIOS}/{operador.id}/inativar", headers={"X-CSRF-Token": token}),
        logado_admin.post(f"{USUARIOS}/{operador.id}/reativar", headers={"X-CSRF-Token": token}),
    ]
    for resp in outras_respostas:
        assert codigo not in resp.text, f"código vazou em {resp.request.url}: {resp.text}"
        if resp.headers.get("content-type", "").startswith("application/json"):
            body = resp.json()
            if isinstance(body, dict):
                assert "codigo" not in body, f"campo 'codigo' presente em {resp.request.url}"


@pytest.mark.unit
def test_codigo_nao_vaza_em_login_nem_me(
    logado_admin: TestClient, client: TestClient, db: Session
) -> None:
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "outrovazamento@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()
    codigo = criado["codigo"]

    # define a senha de verdade pra poder logar e checar /login e /me também
    senha_resp = client.post(
        DEFINIR_SENHA,
        json={"email": "outrovazamento@tecnogera.com", "codigo": codigo, "senha": "senha-nova-1"},
    )
    assert senha_resp.status_code == 200

    login_resp = client.post(
        "/api/v1/portal/login",
        json={"email": "outrovazamento@tecnogera.com", "password": "senha-nova-1"},
    )
    me_resp = client.get("/api/v1/portal/me")

    assert codigo not in login_resp.text
    assert codigo not in me_resp.text
    assert "codigo" not in login_resp.json()
    assert "codigo" not in me_resp.json()


@pytest.mark.unit
def test_codigo_guardado_como_hash_nao_como_valor(logado_admin: TestClient, db: Session) -> None:
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "hashcheck@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()
    codigo = criado["codigo"]

    linha = db.query(User).filter(User.email == "hashcheck@tecnogera.com").first()
    assert linha is not None
    assert linha.password_setup_code_hash is not None
    assert linha.password_setup_code_hash != codigo
    assert bcrypt.checkpw(codigo.encode(), linha.password_setup_code_hash.encode())


# ── reset: mesma mecânica, código devolvido de novo ─────────────────────────


@pytest.mark.unit
def test_resetar_senha_devolve_codigo_novo(logado_admin: TestClient, operador: User) -> None:
    token = _csrf(logado_admin)
    resp = logado_admin.post(
        f"{USUARIOS}/{operador.id}/resetar-senha", headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _CODE_RE.match(body["codigo"])
    assert body["email"] == operador.email


# ── lista: e-mail, papel, ativo, último login, janela aberta ────────────────


@pytest.mark.unit
def test_listar_usuarios_mostra_janela_aberta(logado_admin: TestClient, admin: User) -> None:
    token = _csrf(logado_admin)
    logado_admin.post(
        USUARIOS,
        json={"email": "comjanela@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    )

    lista = logado_admin.get(USUARIOS).json()
    item = next(u for u in lista if u["email"] == "comjanela@tecnogera.com")
    assert item["janela_aberta"] is True
    assert item["role"] == "operador"
    assert item["is_active"] is True
    assert item["last_login_at"] is None

    item_admin = next(u for u in lista if u["email"] == admin.email)
    assert item_admin["janela_aberta"] is False


# ── definir-senha: fluxo completo e resposta genérica ───────────────────────


@pytest.mark.unit
def test_definir_senha_fluxo_completo(logado_admin: TestClient, client: TestClient) -> None:
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "primeira-senha@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()

    resp = client.post(
        DEFINIR_SENHA,
        json={
            "email": "primeira-senha@tecnogera.com",
            "codigo": criado["codigo"],
            "senha": "senha-definida-1",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    login = client.post(
        "/api/v1/portal/login",
        json={"email": "primeira-senha@tecnogera.com", "password": "senha-definida-1"},
    )
    assert login.status_code == 200
    assert login.json()["role"] == "operador"


@pytest.mark.unit
def test_definir_senha_codigo_ja_usado_falha(logado_admin: TestClient, client: TestClient) -> None:
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "reuso@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()
    payload = {
        "email": "reuso@tecnogera.com",
        "codigo": criado["codigo"],
        "senha": "senha-definida-1",
    }
    primeira = client.post(DEFINIR_SENHA, json=payload)
    segunda = client.post(DEFINIR_SENHA, json={**payload, "senha": "outra-senha-2"})

    assert primeira.status_code == 200
    assert segunda.status_code == 400


@pytest.mark.unit
def test_definir_senha_janela_expirada_falha(
    logado_admin: TestClient, client: TestClient, db: Session
) -> None:
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "expirado@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()

    from datetime import UTC, datetime, timedelta

    user = db.query(User).filter(User.email == "expirado@tecnogera.com").first()
    assert user is not None
    user.password_setup_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    resp = client.post(
        DEFINIR_SENHA,
        json={
            "email": "expirado@tecnogera.com",
            "codigo": criado["codigo"],
            "senha": "senha-definida-1",
        },
    )
    assert resp.status_code == 400


@pytest.mark.unit
def test_definir_senha_usuario_inativo_falha_mesmo_com_codigo_valido(
    logado_admin: TestClient, client: TestClient, db: Session
) -> None:
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "inativoprimeirasenha@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()
    logado_admin.post(f"{USUARIOS}/{criado['id']}/inativar", headers={"X-CSRF-Token": token})

    resp = client.post(
        DEFINIR_SENHA,
        json={
            "email": "inativoprimeirasenha@tecnogera.com",
            "codigo": criado["codigo"],
            "senha": "senha-definida-1",
        },
    )
    assert resp.status_code == 400


@pytest.mark.unit
def test_definir_senha_resposta_generica_nao_diferencia_motivo(
    logado_admin: TestClient, client: TestClient
) -> None:
    """E-mail inexistente e código errado devolvem a MESMA mensagem — não dá
    pra usar a resposta como oráculo de e-mail válido.
    """
    token = _csrf(logado_admin)
    logado_admin.post(
        USUARIOS,
        json={"email": "comparaerro@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    )

    resp_email_inexistente = client.post(
        DEFINIR_SENHA,
        json={"email": "ninguem@tecnogera.com", "codigo": "AAAA-1111", "senha": "senha-teste-1"},
    )
    resp_codigo_errado = client.post(
        DEFINIR_SENHA,
        json={
            "email": "comparaerro@tecnogera.com",
            "codigo": "AAAA-1111",
            "senha": "senha-teste-1",
        },
    )

    assert resp_email_inexistente.status_code == resp_codigo_errado.status_code == 400
    assert resp_email_inexistente.json() == resp_codigo_errado.json()


@pytest.mark.unit
def test_definir_senha_senha_curta_422(logado_admin: TestClient, client: TestClient) -> None:
    token = _csrf(logado_admin)
    criado = logado_admin.post(
        USUARIOS,
        json={"email": "senhacurta@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    ).json()
    resp = client.post(
        DEFINIR_SENHA,
        json={"email": "senhacurta@tecnogera.com", "codigo": criado["codigo"], "senha": "curta"},
    )
    assert resp.status_code == 422


# ── admin não fica sem admin ─────────────────────────────────────────────────


@pytest.mark.unit
def test_admin_nao_pode_se_auto_inativar(logado_admin: TestClient, admin: User) -> None:
    token = _csrf(logado_admin)
    resp = logado_admin.post(f"{USUARIOS}/{admin.id}/inativar", headers={"X-CSRF-Token": token})
    assert resp.status_code == 400

    ainda_ativo = logado_admin.get(USUARIOS).json()
    item = next(u for u in ainda_ativo if u["email"] == admin.email)
    assert item["is_active"] is True


@pytest.mark.unit
def test_admin_pode_inativar_outro_admin_se_sobra_um_ativo(
    logado_admin: TestClient, admin: User, db: Session
) -> None:
    outro_admin = _criar_usuario_direto(
        db, "outroadmin@tecnogera.com", role=ROLE_ADMIN, senha="outro-s3cr3t"
    )
    token = _csrf(logado_admin)
    resp = logado_admin.post(
        f"{USUARIOS}/{outro_admin.id}/inativar", headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.unit
def test_outros_admins_ativos_conta_certo(db: Session, admin: User) -> None:
    """Unit direto do invariante que impede zerar admins ativos — ver
    docstring de ``inativar_usuario`` sobre a defesa em profundidade além do
    auto-bloqueio.
    """
    # só o admin da fixture: nenhum OUTRO admin ativo
    assert _outros_admins_ativos(db, admin.id) == 0

    segundo = _criar_usuario_direto(db, "segundo-admin@tecnogera.com", role=ROLE_ADMIN)
    assert _outros_admins_ativos(db, admin.id) == 1
    assert _outros_admins_ativos(db, segundo.id) == 1

    segundo.is_active = False
    db.commit()
    assert _outros_admins_ativos(db, admin.id) == 0


# ── /me devolve o papel ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_me_devolve_papel_admin(logado_admin: TestClient) -> None:
    resp = logado_admin.get("/api/v1/portal/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


@pytest.mark.unit
def test_me_devolve_papel_operador(logado_operador: TestClient) -> None:
    resp = logado_operador.get("/api/v1/portal/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "operador"


# ── a decisão do ticket: resetar senha derruba a sessão ativa ───────────────


@pytest.mark.unit
def test_resetar_senha_derruba_sessao_ativa_do_usuario(
    logado_admin: TestClient, client: TestClient, operador: User
) -> None:
    """`operador` está logado numa sessão própria; o admin reseta a senha
    dele; a sessão que já existia (cookie já emitido) deve parar de
    funcionar no próximo request — é a decisão registrada no módulo.
    """
    sessao_do_operador = TestClient(client.app, raise_server_exceptions=False)
    login_operador = sessao_do_operador.post(
        "/api/v1/portal/login", json={"email": operador.email, "password": "op-s3cr3t"}
    )
    assert login_operador.status_code == 200

    # a sessão funciona antes do reset
    assert sessao_do_operador.get("/api/v1/portal/me").status_code == 200

    token = _csrf(logado_admin)
    reset = logado_admin.post(
        f"{USUARIOS}/{operador.id}/resetar-senha", headers={"X-CSRF-Token": token}
    )
    assert reset.status_code == 200

    # a MESMA sessão (mesmo cookie), agora, é recusada
    resp_depois = sessao_do_operador.get("/api/v1/portal/me")
    assert resp_depois.status_code == 401


@pytest.mark.unit
def test_inativar_continua_derrubando_sessao_ativa(
    logado_admin: TestClient, client: TestClient, operador: User
) -> None:
    """Regressão: a inativação (mecanismo pré-existente) não pode ter sido
    afetada pela mudança em ``current_user`` para o reset.
    """
    sessao_do_operador = TestClient(client.app, raise_server_exceptions=False)
    login_operador = sessao_do_operador.post(
        "/api/v1/portal/login", json={"email": operador.email, "password": "op-s3cr3t"}
    )
    assert login_operador.status_code == 200

    token = _csrf(logado_admin)
    logado_admin.post(f"{USUARIOS}/{operador.id}/inativar", headers={"X-CSRF-Token": token})

    assert sessao_do_operador.get("/api/v1/portal/me").status_code == 401


@pytest.mark.unit
def test_criacao_nao_tem_sessao_previa_para_derrubar(logado_admin: TestClient) -> None:
    """Sanity check da decisão: um usuário recém-criado nunca teve sessão —
    ``password_hash`` nulo na criação é o estado normal, não um efeito do
    reset. Confirma que `authenticate()` recusa login antes da primeira senha.
    """
    token = _csrf(logado_admin)
    logado_admin.post(
        USUARIOS,
        json={"email": "semsenhaainda@tecnogera.com", "role": "operador"},
        headers={"X-CSRF-Token": token},
    )
    login = logado_admin.post(
        "/api/v1/portal/login",
        json={"email": "semsenhaainda@tecnogera.com", "password": "qualquer"},
    )
    assert login.status_code == 401


# ── casos de borda adicionais ────────────────────────────────────────────────


@pytest.mark.unit
def test_criar_usuario_email_sem_arroba_422(logado_admin: TestClient) -> None:
    token = _csrf(logado_admin)
    resp = logado_admin.post(
        USUARIOS,
        json={"email": "sem-arroba", "role": "operador"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 422


@pytest.mark.unit
def test_inativar_usuario_inexistente_404(logado_admin: TestClient) -> None:
    import uuid

    token = _csrf(logado_admin)
    resp = logado_admin.post(f"{USUARIOS}/{uuid.uuid4()}/inativar", headers={"X-CSRF-Token": token})
    assert resp.status_code == 404


@pytest.mark.unit
def test_inativar_bloqueia_ramo_de_defesa_em_profundidade(db: Session) -> None:
    """Testa a ramificação de defesa em profundidade de ``inativar_usuario``
    diretamente (chamada de função, não HTTP).

    Por HTTP este ramo é inalcançável em fluxo síncrono normal: quem chama
    (``admin``, validado por ``require_admin`` a cada request) está sempre
    ativo, então sempre conta como "outro admin" em relação a um alvo
    diferente de si mesmo — o único jeito de zerar a contagem por essa rota
    seria uma corrida real entre duas requisições concorrentes (fora do
    alcance de um teste síncrono). Este teste chama a função com um `admin`
    cujo estado no banco foi manipulado para simular exatamente esse
    instante da corrida, provando que o ramo bloqueia se algum dia for
    alcançado.
    """
    alvo = _criar_usuario_direto(db, "alvo-defesa@tecnogera.com", role=ROLE_ADMIN)
    ator = _criar_usuario_direto(db, "ator-defesa@tecnogera.com", role=ROLE_ADMIN)
    ator.is_active = False  # simula a janela de corrida
    db.commit()

    with pytest.raises(HTTPException) as exc:
        inativar_usuario(user_id=alvo.id, db=db, admin=ator, _csrf=None)

    assert exc.value.status_code == 400
