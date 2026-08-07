"""Rate limiting de rotas de autenticação — dependência FastAPI reutilizável.

Mesmo espírito de ``verify_csrf`` (``app/routers/portal.py``): um guarda
exposto como dependência, sem estado escondido na rota que o usa.

## Decisão: contador em memória do processo, não Redis

Medido em ``docker-compose.yml`` + ``Dockerfile`` (ticket usuarios-portal/03):
o serviço ``api`` sobe com ``CMD ["uvicorn", "app.main:app", "--host",
"0.0.0.0", "--port", "8000"]`` — sem ``--workers`` — tanto em produção quanto
no override de dev (``docker-compose.override.yml``, que só troca o comando
por adicionar ``--reload``). Uvicorn sem ``--workers`` sobe **um processo
único**. Com 1 worker, um contador em memória é exato: não existe a
fragmentação N-worker que faria o limite efetivo virar N× (cada processo
contando separado), que é o modo de falha do "memória fura com mais de um
worker" citado no ticket.

O projeto já tem Redis (fila Arq), que resolveria a fragmentação para
qualquer N de workers — mas ao custo de acoplar a autenticação à
disponibilidade do Redis. Com Redis fora do ar, a escolha seria falhar
fechado (login para todo mundo cai junto com a fila) ou falhar aberto (login
sem limite exatamente quando um operador desatento pode não notar o Redis
caído). Nenhuma das duas opções vale o preço, dado que **hoje há só 1
worker** — o cenário em que Redis ganharia (N>1 workers) simplesmente não
existe no deploy atual.

Efeito colateral aceito: reiniciar o container ``api`` zera todos os
contadores. Não é um vetor de ataque plausível — quem tem acesso para
reiniciar o container já tem acesso ao host, que já é comprometimento maior
que burlar rate limit de login.

**Reconsiderar esta decisão se** ``docker-compose.yml``/``Dockerfile``
ganharem ``--workers``/réplicas do serviço ``api``: nesse momento o contador
em memória passa a fragmentar e a mudança para Redis (ou para um único
worker "gateway" dedicado ao rate limit) volta à mesa.

## Duas dimensões

- **Identidade** (e-mail): impede martelar uma conta específica.
- **Origem** (IP): impede varrer muitas contas a partir de um lugar só.

Cada rota que usa este módulo tem seu próprio par de limitadores
(:class:`RateLimitPair`), guardado em ``app.state`` — não há estado global
compartilhado entre rotas nem entre instâncias de app (o que mantém os
testes isolados uns dos outros, já que cada teste roda ``create_app()``
próprio).

## O IP atrás do túnel cloudflared

O portal é alcançável só via túnel ``cloudflared`` (o container do portal
faz bind em ``127.0.0.1:8094``; nada além do host consegue conectar
direto). O tráfego chega em ``nginx`` (repo ``tecnogera-portal``,
``nginx.conf``) via cloudflared e é proxied para ``http://api:8000`` na rede
Docker interna — o IP que a API vê no socket (``request.client.host``) é
sempre o do container ``portal`` (ou, sem proxy, o peer direto), nunca o do
cliente real.

O cabeçalho usado aqui é ``CF-Connecting-IP``, **não** ``X-Forwarded-For``:

- ``CF-Connecting-IP`` é escrito pela borda da Cloudflare a partir da conexão
  TCP real do visitante, e a Cloudflare **descarta qualquer valor desse
  cabeçalho vindo do cliente antes de setar o seu** — não é forjável por quem
  chama, porque só a borda da Cloudflare consegue escrevê-lo com o valor que
  o app vai ler (nginx, no meio do caminho, não seta nem reescreve esse
  header — só repassa).
- ``X-Forwarded-For``, ao contrário, é uma lista que a Cloudflare **anexa**
  ao valor recebido do cliente, sem descartar o que veio antes. Um cliente
  malicioso pode mandar ``X-Forwarded-For: 1.2.3.4`` e a Cloudflare produz
  ``1.2.3.4, <ip real>`` — se o código ingenuamente ler a primeira entrada
  (padrão comum), o valor é forjável. Por isso este módulo não usa
  ``X-Forwarded-For`` para decisão de segurança.

Sem ``CF-Connecting-IP`` (dev local, teste, ou tráfego que não passou pela
Cloudflare) o fallback é ``request.client.host`` — o peer TCP direto. É
estritamente melhor que a situação anterior (nenhum limite por IP).

**Risco residual, fora do escopo deste ticket**: o serviço ``api`` também
publica ``127.0.0.1:${API_PORT:-8000}:8000`` no host (comentado no
``docker-compose.yml`` como acesso de debug). Quem tem acesso ao loopback do
host pode falar direto com a API, pulando nginx/Cloudflare, e forjar
``CF-Connecting-IP`` à vontade. Isso já pressupõe acesso ao host — mesmo
nível de acesso que já compromete segredos em ``.env``/banco; não é um
buraco novo introduzido por este módulo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.config import Settings

#: Mensagem genérica do 429 — nunca revela se a identidade (e-mail) existe,
#: nem qual das duas dimensões (identidade ou origem) foi excedida.
RATE_LIMIT_MESSAGE = "Muitas tentativas. Aguarde alguns minutos e tente novamente."


@dataclass
class _Bucket:
    hits: list[float] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)


class SlidingWindowLimiter:
    """Limitador de janela deslizante em memória, thread-safe.

    Conta **falhas explícitas** (via :meth:`record_failure`), não todo
    request — quem chama decide o que é "falha" (ex.: credencial inválida) e
    só soma nesse caso. Uma sequência de sucessos nunca faz o contador subir,
    então login legítimo repetido não esbarra no limite.

    Implementação pequena de propósito (ver nota no ticket sobre não
    adicionar dependência externa como ``slowapi``/``limits`` para algo deste
    tamanho): um dict de buckets por chave, cada bucket com os timestamps
    (``time.monotonic()``) das falhas ainda dentro da janela. Cresce com o
    número de chaves distintas vistas recentemente (e-mails/IPs); chaves
    paradas ficam com bucket vazio após a janela expirar (purga é lazy, no
    próximo acesso àquela chave) — aceitável na escala deste portal (dezenas
    de contas, não milhões).
    """

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts precisa ser >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds precisa ser > 0")
        self._max_attempts = max_attempts
        self._window_seconds = float(window_seconds)
        self._buckets: dict[str, _Bucket] = {}
        self._buckets_lock = Lock()

    def _bucket(self, key: str) -> _Bucket:
        with self._buckets_lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket()
                self._buckets[key] = bucket
            return bucket

    def _purge(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self._window_seconds
        bucket.hits = [t for t in bucket.hits if t > cutoff]

    def retry_after(self, key: str) -> int | None:
        """Segundos até a falha mais antiga expirar, ou ``None`` se não bloqueado."""
        bucket = self._bucket(key)
        now = time.monotonic()
        with bucket.lock:
            self._purge(bucket, now)
            if len(bucket.hits) < self._max_attempts:
                return None
            oldest = bucket.hits[0]
            remaining = (oldest + self._window_seconds) - now
            return max(1, int(remaining) + 1)

    def record_failure(self, key: str) -> None:
        bucket = self._bucket(key)
        now = time.monotonic()
        with bucket.lock:
            self._purge(bucket, now)
            bucket.hits.append(now)

    def reset(self, key: str) -> None:
        """Limpa o rastro de falhas de uma chave (chamar em sucesso legítimo)."""
        bucket = self._bucket(key)
        with bucket.lock:
            bucket.hits = []


@dataclass
class RateLimitPair:
    """Par identidade + origem que protege uma rota de autenticação.

    Reutilizável por qualquer rota de autenticação (login hoje; a rota de
    definir senha por código do ticket 02 amanhã): instancie um par por
    rota/uso — via :func:`new_rate_limit_pair` — e registre em ``app.state``
    (ver ``app/main.py::create_app`` para o exemplo do login). Não compartilhe
    o mesmo par entre rotas diferentes: elas protegem contas diferentes e
    devem poder ser calibradas (limite/janela) e testadas independentemente.
    """

    identity: SlidingWindowLimiter
    origin: SlidingWindowLimiter

    def retry_after(self, *, identity_key: str | None, origin_key: str) -> int | None:
        candidates = [self.origin.retry_after(f"origin:{origin_key}")]
        if identity_key:
            candidates.append(self.identity.retry_after(f"identity:{identity_key}"))
        values = [c for c in candidates if c is not None]
        return max(values) if values else None

    def record_failure(self, *, identity_key: str | None, origin_key: str) -> None:
        self.origin.record_failure(f"origin:{origin_key}")
        if identity_key:
            self.identity.record_failure(f"identity:{identity_key}")

    def record_success(self, *, identity_key: str | None) -> None:
        """Chamar em toda tentativa bem-sucedida.

        Só limpa a dimensão de **identidade**: provar que sabe a senha limpa
        o próprio histórico de falhas daquela conta. A dimensão de **origem**
        (IP) NÃO é limpa aqui de propósito — um IP compartilhado (NAT de
        escritório) pode ter um usuário legítimo acertando enquanto outro,
        no mesmo IP, está testando credenciais roubadas; um sucesso alheio
        não deveria resetar a proteção contra varredura daquele IP.
        """
        if identity_key:
            self.identity.reset(f"identity:{identity_key}")


def new_rate_limit_pair(
    *,
    identity_max_attempts: int,
    identity_window_seconds: int,
    origin_max_attempts: int,
    origin_window_seconds: int,
) -> RateLimitPair:
    return RateLimitPair(
        identity=SlidingWindowLimiter(identity_max_attempts, identity_window_seconds),
        origin=SlidingWindowLimiter(origin_max_attempts, origin_window_seconds),
    )


def new_login_rate_limit_pair(settings: Settings) -> RateLimitPair:
    """Par de limitadores do ``POST /login``, calibrado pelas Settings."""
    return new_rate_limit_pair(
        identity_max_attempts=settings.login_rate_limit_identity_max_attempts,
        identity_window_seconds=settings.login_rate_limit_identity_window_seconds,
        origin_max_attempts=settings.login_rate_limit_origin_max_attempts,
        origin_window_seconds=settings.login_rate_limit_origin_window_seconds,
    )


def client_ip(request: Request) -> str:
    """IP de origem, confiável mesmo atrás do túnel cloudflared.

    Ver docstring do módulo para o porquê de ``CF-Connecting-IP`` (e não
    ``X-Forwarded-For``) ser a fonte usada quando presente.
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip and cf_ip.strip():
        # Defensivo: não confiar em valor absurdo mesmo vindo de um cabeçalho
        # confiável (múltiplos IPs separados por vírgula não é o formato
        # esperado desse header específico, mas custa nada truncar).
        return cf_ip.split(",")[0].strip()[:100]
    if request.client:
        return request.client.host
    return "unknown"


def rate_limit_dependency(
    state_attr: str,
    identity_from_body: Callable[[dict[str, Any]], str | None] | None = None,
) -> Callable[[Request], Awaitable[None]]:
    """Fábrica de dependência FastAPI — mesmo espírito de ``verify_csrf``.

    ``state_attr``: nome do atributo em ``request.app.state`` que guarda o
    :class:`RateLimitPair` da rota (registrado em ``create_app``).

    ``identity_from_body``: extrai a chave de identidade (e-mail, código de
    uso único...) do corpo JSON da requisição. ``None`` pula a dimensão de
    identidade e limita só por origem — útil se a identidade não estiver no
    corpo (ex.: código de uso único vem em outro campo).

    A dependência só **verifica** — nunca incrementa. Quem chama a rota
    decide, via ``pair.record_failure``/``pair.record_success`` dentro do
    corpo da rota (depois de saber o resultado real da tentativa), o que
    conta como falha. Isso é o que garante que sucesso nunca soma tentativa
    (ver :meth:`RateLimitPair.record_success`).
    """

    async def _dependency(request: Request) -> None:
        pair: RateLimitPair = getattr(request.app.state, state_attr)
        origin_key = client_ip(request)
        identity_key: str | None = None
        if identity_from_body is not None:
            try:
                payload = await request.json()
            except Exception:  # noqa: BLE001 — corpo inválido não é problema do limiter, é 422 depois
                payload = None
            if isinstance(payload, dict):
                identity_key = identity_from_body(payload)

        retry_after = pair.retry_after(identity_key=identity_key, origin_key=origin_key)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail=RATE_LIMIT_MESSAGE,
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency


def _email_from_body(payload: dict[str, Any]) -> str | None:
    email = payload.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    return None


#: Dependência pronta para ``POST /login`` — ``Depends(check_login_rate_limit)``.
check_login_rate_limit = rate_limit_dependency("login_rate_limit", _email_from_body)


def _login_pair(request: Request) -> RateLimitPair:
    pair: RateLimitPair = request.app.state.login_rate_limit
    return pair


def record_login_failure(request: Request, email: str) -> None:
    """Chamar quando ``authenticate()`` falhar (credencial inválida ou usuário inativo)."""
    _login_pair(request).record_failure(
        identity_key=email.strip().lower(), origin_key=client_ip(request)
    )


def record_login_success(request: Request, email: str) -> None:
    """Chamar quando o login for aceito — limpa o histórico de falhas da conta."""
    _login_pair(request).record_success(identity_key=email.strip().lower())


# ── POST /definir-senha (ticket usuarios-portal/02) ─────────────────────────
#
# Mesmo motor do login acima, par próprio (identidade = e-mail do corpo,
# origem = CF-Connecting-IP), registrado em ``app.state.password_setup_rate_limit``
# por ``new_password_setup_rate_limit_pair`` (chamado em ``app/main.py``, no
# mesmo lugar que já registra o par do login). Ver docstring do módulo — a
# decisão de contador em memória vale igual aqui, é o mesmo processo.
#
# Este par protege contra tentativa **rápida/distribuída** de adivinhar o
# código de uso único. A camada que não decai com o tempo — teto rígido
# amarrado ao código específico, sobrevivendo a reinício do processo — é
# ``password_setup_attempts`` em ``app/services/user_management.py``.


def new_password_setup_rate_limit_pair(settings: Settings) -> RateLimitPair:
    """Par de limitadores do ``POST /definir-senha``, calibrado pelas Settings."""
    return new_rate_limit_pair(
        identity_max_attempts=settings.password_setup_rate_limit_identity_max_attempts,
        identity_window_seconds=settings.password_setup_rate_limit_identity_window_seconds,
        origin_max_attempts=settings.password_setup_rate_limit_origin_max_attempts,
        origin_window_seconds=settings.password_setup_rate_limit_origin_window_seconds,
    )


#: Dependência pronta para ``POST /definir-senha`` — ``Depends(check_password_setup_rate_limit)``.
check_password_setup_rate_limit = rate_limit_dependency(
    "password_setup_rate_limit", _email_from_body
)


def _password_setup_pair(request: Request) -> RateLimitPair:
    pair: RateLimitPair = request.app.state.password_setup_rate_limit
    return pair


def record_password_setup_failure(request: Request, email: str) -> None:
    """Chamar quando a validação do código/senha falhar, por qualquer motivo.

    Mesmo espírito de ``record_login_failure``: soma nas duas dimensões, e o
    motivo real da falha (e-mail inexistente, código errado, janela
    expirada, tentativas estouradas, usuário inativo) não influencia esta
    chamada — todos batem aqui do mesmo jeito, o que é parte do que impede a
    resposta de virar oráculo de e-mail válido.
    """
    _password_setup_pair(request).record_failure(
        identity_key=email.strip().lower(), origin_key=client_ip(request)
    )


def record_password_setup_success(request: Request, email: str) -> None:
    """Chamar quando a senha for definida com sucesso — limpa o histórico de falhas."""
    _password_setup_pair(request).record_success(identity_key=email.strip().lower())
