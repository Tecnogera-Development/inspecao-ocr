"""Rotas de gerenciamento de usuários e primeira senha — ticket ``usuarios-portal/02``.

Arquivo separado de ``app/routers/portal.py`` (que já passa de 1300 linhas)
de propósito — reaproveita, sem recriar, os padrões de lá:
``current_user``/``verify_csrf`` (guarda de sessão e CSRF) e o motor de rate
limiting do ticket 03 (``app/core/ratelimit.py``).

Duas famílias de rota:

- **Admin** (``/api/v1/portal/usuarios``...): exigem ``require_admin`` (novo
  aqui, em cima de ``current_user``) e CSRF nas de escrita, como o resto do
  portal.
- **Pública, não autenticada** (``POST /api/v1/portal/definir-senha``): o
  próprio usuário define a senha com e-mail + código de uso único. Protegida
  só pelo rate limit — não há sessão para exigir CSRF.

## A decisão do ticket: resetar senha derruba a sessão ativa

**Sim.** ``resetar_senha`` chama ``open_password_setup_window``
(``app/services/user_management.py``), que zera ``password_hash`` — o mesmo
estado de um usuário recém-criado. ``current_user``
(``app/routers/portal.py``) passou a recusar sessão quando ``password_hash``
é nulo, do mesmo jeito que já recusava quando ``is_active`` era falso — é o
padrão que o próprio mapa documenta (revalidação a cada request), estendido
por um campo, não um mecanismo novo (sem tabela de sessão, sem versionar
token).

Por quê: se um admin reseta a senha por suspeitar de conta comprometida e a
sessão do invasor sobrevive porque só ``is_active`` era checado, o gesto do
reset não protege nada — o invasor continua com acesso até a sessão expirar
sozinha (TTL de 8h, ``app/main.py``). Zerar ``password_hash`` no reset é
seguro sem falso positivo: ``authenticate()`` já recusa login quando
``password_hash`` é nulo, então nenhum usuário legítimo consegue estar com
uma sessão ativa nesse estado por nenhum caminho que não seja o próprio
reset (ou a criação, mas aí não existe sessão prévia para derrubar).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 — FastAPI resolve a anotação em runtime (path param)

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.core.ratelimit import (
    check_password_setup_rate_limit,
    record_password_setup_failure,
    record_password_setup_success,
)
from app.db.session import get_db
from app.models.user import ROLE_ADMIN, ROLE_OPERADOR, ROLES, User
from app.routers.portal import current_user, verify_csrf
from app.services.user_management import (
    MIN_PASSWORD_LENGTH,
    consumir_codigo_definir_senha,
    open_password_setup_window,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/portal", tags=["usuarios"])
_log = get_logger(__name__)


# ── schemas ───────────────────────────────────────────────────────────────────


class UsuarioListItem(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    janela_aberta: bool


class UsuarioCriadoResponse(BaseModel):
    """Resposta de criação/reset — a ÚNICA vez que ``codigo`` aparece em claro.

    Nenhuma outra rota deste módulo (``UsuarioListItem``, ``UsuarioAcaoResponse``)
    tem campo equivalente — ver ``tests/routers/test_usuarios.py`` para o
    teste que varre as respostas das demais rotas procurando o valor.
    """

    id: UUID
    email: str
    role: str
    codigo: str


class UsuarioAcaoResponse(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool


class CriarUsuarioRequest(BaseModel):
    email: str
    role: str = ROLE_OPERADOR

    @field_validator("email")
    @classmethod
    def _email_com_arroba(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or not v:
            raise ValueError("e-mail inválido")
        return v


class DefinirSenhaRequest(BaseModel):
    email: str
    codigo: str
    senha: str = Field(min_length=MIN_PASSWORD_LENGTH)


class DefinirSenhaResponse(BaseModel):
    ok: bool = True


#: Mensagem genérica de falha em /definir-senha — não pode diferenciar
#: e-mail inexistente de código errado de janela expirada de tentativas
#: estouradas de usuário inativo (ticket 02, risco 2). O motivo real só
#: existe no log estruturado de ``consumir_codigo_definir_senha``.
_DEFINIR_SENHA_ERRO_GENERICO = (
    "Não foi possível definir a senha. Confira e-mail e código, e se a janela "
    "de 30 minutos ainda não expirou. Se o problema continuar, peça um novo "
    "código ao administrador."
)


# ── dependências ──────────────────────────────────────────────────────────────


def require_admin(user: User = Depends(current_user)) -> User:
    """Exige papel ``admin`` — em cima de ``current_user``, não em vez dele.

    ``current_user`` já garante sessão válida e ``is_active``; este guarda
    só acrescenta a checagem de papel. 403 (não 404) — a rota existe, o
    usuário só não tem permissão, e é isso que o teste de aceite
    "operador recebe 403 em todas as rotas de gerenciamento" espera.
    """
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Requer papel admin")
    return user


def _usuario_ou_404(db: Session, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return user


def _utc(momento: datetime) -> datetime:
    """Datas do banco em ordem comparável — mesmo problema documentado em
    ``app/services/checklist_validation.py::_utc`` e
    ``app/services/user_management.py::_utc``: o Postgres devolve tudo com
    fuso; o SQLite dos testes devolve naive o que já estava gravado.
    """
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _outros_admins_ativos(db: Session, excluir_id: UUID) -> int:
    return (
        db.query(User)
        .filter(User.role == ROLE_ADMIN, User.is_active.is_(True), User.id != excluir_id)
        .count()
    )


# ── rotas de admin ───────────────────────────────────────────────────────────


@router.get("/usuarios", response_model=list[UsuarioListItem])
def listar_usuarios(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[UsuarioListItem]:
    agora = datetime.now(UTC)
    usuarios = db.query(User).order_by(User.email).all()
    return [
        UsuarioListItem(
            id=u.id,
            email=u.email,
            role=u.role,
            is_active=u.is_active,
            last_login_at=u.last_login_at,
            janela_aberta=bool(
                u.password_setup_expires_at is not None
                and _utc(u.password_setup_expires_at) > agora
            ),
        )
        for u in usuarios
    ]


@router.post("/usuarios", response_model=UsuarioCriadoResponse, status_code=201)
def criar_usuario(
    body: CriarUsuarioRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _csrf: None = Depends(verify_csrf),
) -> UsuarioCriadoResponse:
    if body.role not in ROLES:
        raise HTTPException(
            status_code=422, detail=f"papel inválido: {body.role!r} — use um de {sorted(ROLES)}"
        )

    user = User(email=body.email, role=body.role, password_hash=None, is_active=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # mesmo padrão de app.cli.create_user_in_db: deixa o índice único do
        # banco decidir, em vez de um SELECT prévio que teria race condition
        # entre duas criações concorrentes do mesmo e-mail.
        db.rollback()
        raise HTTPException(status_code=409, detail="e-mail já cadastrado") from None
    db.refresh(user)

    opened = open_password_setup_window(db, user)
    _log.info("portal_usuario_criado", user_id=str(user.id), admin_id=str(admin.id))
    return UsuarioCriadoResponse(id=user.id, email=user.email, role=user.role, codigo=opened.code)


@router.post("/usuarios/{user_id}/inativar", response_model=UsuarioAcaoResponse)
def inativar_usuario(
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _csrf: None = Depends(verify_csrf),
) -> UsuarioAcaoResponse:
    user = _usuario_ou_404(db, user_id)

    # Regra literal do ticket: admin não se auto-inativa, sob nenhuma
    # circunstância (mesmo havendo outros admins ativos) — evita que alguém
    # corte o próprio acesso no meio de uma tarefa por engano.
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Você não pode inativar a própria conta")

    # Defesa em profundidade além do auto-bloqueio acima: nenhuma inativação
    # de admin pode zerar o total de admins ativos — cobre o caso de um
    # admin inativando OUTRO admin que por acaso é o último além de si.
    if user.role == ROLE_ADMIN and user.is_active and _outros_admins_ativos(db, user.id) == 0:
        raise HTTPException(
            status_code=400, detail="Não é possível inativar o único admin ativo restante"
        )

    user.is_active = False
    db.commit()
    db.refresh(user)
    _log.info("portal_usuario_inativado", user_id=str(user.id), admin_id=str(admin.id))
    return UsuarioAcaoResponse(
        id=user.id, email=user.email, role=user.role, is_active=user.is_active
    )


@router.post("/usuarios/{user_id}/reativar", response_model=UsuarioAcaoResponse)
def reativar_usuario(
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _csrf: None = Depends(verify_csrf),
) -> UsuarioAcaoResponse:
    user = _usuario_ou_404(db, user_id)
    user.is_active = True
    db.commit()
    db.refresh(user)
    _log.info("portal_usuario_reativado", user_id=str(user.id), admin_id=str(admin.id))
    return UsuarioAcaoResponse(
        id=user.id, email=user.email, role=user.role, is_active=user.is_active
    )


@router.post("/usuarios/{user_id}/resetar-senha", response_model=UsuarioCriadoResponse)
def resetar_senha(
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _csrf: None = Depends(verify_csrf),
) -> UsuarioCriadoResponse:
    """Mesma mecânica da criação: nova janela, novo código, devolvido uma vez.

    Também zera ``password_hash`` (dentro de ``open_password_setup_window``)
    — é o que derruba qualquer sessão ativa daquele usuário. Ver decisão no
    topo do módulo.
    """
    user = _usuario_ou_404(db, user_id)
    opened = open_password_setup_window(db, user)
    _log.info("portal_usuario_resetou_senha", user_id=str(user.id), admin_id=str(admin.id))
    return UsuarioCriadoResponse(id=user.id, email=user.email, role=user.role, codigo=opened.code)


# ── rota pública: primeira senha / reset ────────────────────────────────────


@router.post("/definir-senha", response_model=DefinirSenhaResponse)
def definir_senha(
    body: DefinirSenhaRequest,
    request: Request,
    db: Session = Depends(get_db),
    _rate_limit: None = Depends(check_password_setup_rate_limit),
) -> DefinirSenhaResponse:
    usuario = consumir_codigo_definir_senha(
        db, email=body.email, codigo=body.codigo, nova_senha=body.senha
    )
    if usuario is None:
        # Falha soma nas duas dimensões do limitador (identidade + origem) —
        # mesmo padrão de record_login_failure em POST /login. O motivo real
        # já foi para o log dentro de consumir_codigo_definir_senha; aqui só
        # a resposta genérica.
        record_password_setup_failure(request, body.email)
        raise HTTPException(status_code=400, detail=_DEFINIR_SENHA_ERRO_GENERICO)

    record_password_setup_success(request, body.email)
    _log.info("portal_definir_senha_sucesso", user_id=str(usuario.id))
    return DefinirSenhaResponse(ok=True)
