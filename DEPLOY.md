# Deploy — Inspeção Visual (inspecao.polarisprod.com.br)

Stack: `docker compose` → **api** (FastAPI) + **worker** (arq) + **postgres** + **redis** + **portal** (nginx/React).
Só o **portal** é exposto: serve o SPA e faz proxy de `/api/` → `api:8000`. Publica em `127.0.0.1:8094`.

**Polyrepo:** os dois diretórios ficam lado a lado (compose builda o portal de `../tecnogera-portal`). Não separar.

## Deploy

```bash
cd tecnogera-ia-visual-api

# 1. .env de produção
cp .env.production.example .env
#    preencher: POSTGRES_PASSWORD (forte), DROPBOX_APP_KEY/APP_SECRET/REFRESH_TOKEN,
#    ANTHROPIC_API_KEY, OPENAI_API_KEY, SESSION_SECRET, PIPELINE_API_KEY, CORS_ALLOW_ORIGINS
#    (o boot em produção FALHA se SESSION_SECRET/POSTGRES_PASSWORD forem default,
#     PIPELINE_API_KEY faltar, ou CORS='*'. Gere segredos: openssl rand -hex 32)
chmod 600 .env

# 2. sobe a stack  (SEMPRE com -f explícito — o override.yml é modo dev)
docker compose -f docker-compose.yml up -d --build

# 3. migrations (não rodam no startup)
docker compose -f docker-compose.yml exec api alembic upgrade head

# 4. usuário do portal
docker compose -f docker-compose.yml exec api \
  python -m app.cli create_user --email admin@tecnogera.com --password '<senha-forte>'
```

## Cloudflare Tunnel (cloudflared = systemd no host)

Ingress em `/etc/cloudflared/config.yml` → alvo **`http://localhost:8094`** (host-published), **não** o nome do container:

```yaml
ingress:
  - hostname: inspecao.polarisprod.com.br
    service: http://localhost:8094
  - service: http_status:404   # catch-all por último
```
```bash
sudo systemctl restart cloudflared
```
DNS: `inspecao.polarisprod.com.br` CNAME → `<tunnel-id>.cfargotunnel.com`.

## Verificação

```bash
docker compose -f docker-compose.yml ps                 # api = healthy
curl -sI http://127.0.0.1:8094/                          # 200 + headers de segurança
curl -s -o/dev/null -w '%{http_code}\n' -X POST \        # 200 (proxy nginx→api)
  http://127.0.0.1:8094/api/v1/portal/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@tecnogera.com","password":"<senha>"}'
curl -sI https://inspecao.polarisprod.com.br             # 200 via túnel
```

## Notas

- **Portas:** portal `127.0.0.1:8094`, api `127.0.0.1:8000` (só loopback — nada exposto na LAN/VPN).
- **Não usar** `docker-compose.override.yml` / `.faketest.yml` (modo dev/fake) — sempre `-f docker-compose.yml`.
- **`POSTGRES_PASSWORD`** é gravada no volume `postgres_data` no 1º `up`; trocar depois = recriar o volume.
- **Reset de senha de usuário:** não há comando; hoje é via banco.
- Logs: `docker compose -f docker-compose.yml logs -f api worker portal`.
- Testado ponta-a-ponta (build, migrations, admin, login via proxy, headers, rate limit) em Docker 29 / Compose v2.
