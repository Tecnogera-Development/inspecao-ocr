"""Gerenciamento de usuários do portal — ticket ``usuarios-portal/02``.

Lógica de negócio por trás das rotas de admin (``app/routers/usuarios.py``):
gerar/validar o código de uso único da janela de primeira senha, abrir a
janela (criação e reset são o **mesmo caminho**, por decisão do mapa —
decisão de produto), e consumir o código para gravar a senha
definitiva.

Fica fora do router de propósito: pode ser testada sem TestClient/HTTP, e a
lógica de "o que conta como tentativa" é densa o bastante para merecer teste
isolado do transporte HTTP.

## Risco 1 do mapa — por que o código de uso único não é opcional

Durante a janela de 30 min, o único outro dado seria o e-mail — adivinhável
no padrão corporativo, e a janela abre num momento socialmente previsível
(alguém acabou de entrar no time). O código é o que fecha esse buraco; ele
nunca é gravado em claro (só o hash bcrypt, herdado de
``app.models.user.User.password_setup_code_hash`` — ticket 01) e só existe
em texto puro na resposta HTTP que o cria (``SetupWindowOpened.code``, aqui)
e no repasse fora de banda que o admin faz depois.

## Risco 2 do ticket — a resposta de falha não pode virar oráculo

``consumir_codigo_definir_senha`` devolve ``None`` para **todo** motivo de
falha: e-mail inexistente, usuário inativo, sem janela aberta, janela
expirada, tentativas estouradas, código errado. Quem chama (o router) não
tem como saber qual foi — só o log estruturado (não a resposta HTTP) guarda
o motivo, para operação/auditoria.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import bcrypt

from app.core.logging import get_logger
from app.models.user import User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_log = get_logger(__name__)

#: Janela de primeira senha / reset — mesma mecânica pros dois casos
#: (decisão de produto).
PASSWORD_SETUP_WINDOW_MINUTES = 30

#: Tentativas erradas de código antes de a janela morrer de vez. É um teto
#: **rígido**, amarrado ao código específico e persistido em
#: ``users.password_setup_attempts`` — ao contrário do rate limit por
#: identidade/origem (``app/core/ratelimit.py``, janela deslizante que decai
#: com o tempo), este não reseta sozinho: depois de estourado, só um reset
#: do admin (nova janela, novo código) reabre o caminho.
MAX_PASSWORD_SETUP_ATTEMPTS = 5

#: Comprimento mínimo aceito para a senha nova. Não é pedido explicitamente
#: pelo ticket, mas custa pouco recusar senha trivialmente curta na própria
#: borda do modelo Pydantic (ver ``DefinirSenhaRequest`` em
#: ``app/routers/usuarios.py``) — trade-off documentado no relato do agente.
MIN_PASSWORD_LENGTH = 8

# Alfabeto sem 0/O e 1/I — o código é repassado fora de banda (voz, chat) e
# esses pares são a fonte mais comum de erro de transcrição humana; não é
# perda de entropia relevante (32 símbolos em vez de 36 — ainda
# 32**8 ≈ 1.1e12 combinações).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_setup_code() -> str:
    """Código de uso único, formato ``XXXX-XXXX`` (8 símbolos + hífen).

    Gerado com ``secrets`` (CSPRNG) — é credencial, não identificador
    (risco 1 do mapa). Nunca reaproveitado entre janelas: cada chamada a
    :func:`open_password_setup_window` gera um código novo e substitui
    qualquer hash anterior, matando o código antigo mesmo que não tenha
    expirado nem estourado tentativas.
    """
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _hash_code(code: str) -> str:
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()


@dataclass
class SetupWindowOpened:
    """Resultado de abrir a janela — ``code`` só existe aqui e na resposta HTTP.

    Depois deste objeto ser consumido pelo router (serializado na resposta),
    nada mais no processo guarda o valor em claro: não fica em atributo de
    log, não fica em variável de módulo, não é persistido em lugar nenhum
    além do hash já gravado no ``User``.
    """

    user: User
    code: str


def open_password_setup_window(db: Session, user: User) -> SetupWindowOpened:
    """Abre (ou reabre) a janela de primeira senha — criação e reset usam isto.

    Zera tentativas, substitui qualquer código anterior (uso único de
    verdade: o código velho para de funcionar assim que um novo é gerado,
    mesmo que ainda estivesse dentro da janela) e **zera ``password_hash``**.

    O zeramento de ``password_hash`` é deliberado e é o mecanismo por trás
    da decisão "resetar derruba a sessão ativa" (ver
    ``app/routers/portal.py::current_user``, que passou a recusar sessão
    quando ``password_hash`` é nulo, do mesmo jeito que já recusa quando
    ``is_active`` é falso). Em ``criar_usuario`` isso é um no-op —
    ``password_hash`` já nasce nulo. Em ``resetar_senha`` é o que faz a
    senha antiga (e a sessão que dependia dela) deixar de valer assim que a
    janela reabre — senão "resetar" não resetaria nada.
    """
    code = generate_setup_code()
    user.password_hash = None
    user.password_setup_code_hash = _hash_code(code)
    user.password_setup_expires_at = datetime.now(UTC) + timedelta(
        minutes=PASSWORD_SETUP_WINDOW_MINUTES
    )
    user.password_setup_attempts = 0
    db.commit()
    db.refresh(user)
    return SetupWindowOpened(user=user, code=code)


def _utc(momento: datetime) -> datetime:
    """Datas do banco em ordem comparável.

    Mesmo problema documentado em ``app/services/checklist_validation.py::_utc``:
    o Postgres devolve tudo com fuso; o SQLite dos testes devolve **naive** o
    que já estava gravado e aware o que acabou de ser atribuído na sessão.
    Comparar os dois levanta ``TypeError``.
    """
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _janela_valida(user: User, agora: datetime) -> bool:
    if user.password_setup_code_hash is None or user.password_setup_expires_at is None:
        return False
    if _utc(user.password_setup_expires_at) < agora:
        return False
    return user.password_setup_attempts < MAX_PASSWORD_SETUP_ATTEMPTS


def consumir_codigo_definir_senha(
    db: Session, *, email: str, codigo: str, nova_senha: str
) -> User | None:
    """Valida e consome o código de uso único, gravando a senha nova.

    Devolve o ``User`` em caso de sucesso, ``None`` para **qualquer** falha
    — é o corte que impede a rota virar oráculo de e-mail válido (ticket
    02, risco 2). O motivo real vai só para o log estruturado, nunca para o
    valor de retorno que o router serializa em HTTP.
    """
    agora = datetime.now(UTC)
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        _log.info("password_setup_falhou", motivo="email_inexistente")
        return None
    if not user.is_active:
        _log.info("password_setup_falhou", motivo="usuario_inativo", user_id=str(user.id))
        return None
    if not _janela_valida(user, agora):
        motivo = (
            "tentativas_estouradas"
            if (user.password_setup_attempts >= MAX_PASSWORD_SETUP_ATTEMPTS)
            else "janela_ausente_ou_expirada"
        )
        _log.info("password_setup_falhou", motivo=motivo, user_id=str(user.id))
        return None

    assert user.password_setup_code_hash is not None  # _janela_valida já garantiu
    if not bcrypt.checkpw(codigo.encode(), user.password_setup_code_hash.encode()):
        user.password_setup_attempts += 1
        db.commit()
        _log.info(
            "password_setup_falhou",
            motivo="codigo_incorreto",
            user_id=str(user.id),
            tentativas=user.password_setup_attempts,
        )
        return None

    # sucesso: grava a senha nova, invalida o código (uso único de verdade),
    # zera a janela por completo.
    user.password_hash = bcrypt.hashpw(nova_senha.encode(), bcrypt.gensalt()).decode()
    user.password_setup_code_hash = None
    user.password_setup_expires_at = None
    user.password_setup_attempts = 0
    db.commit()
    db.refresh(user)
    _log.info("password_setup_ok", user_id=str(user.id))
    return user
