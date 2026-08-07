# Deploy — IA Visual v1.2.1-entregável (inspecao.polarisprod.com.br)

**Esta é uma ATUALIZAÇÃO da stack que já roda**, não uma instalação do zero. Se a instalação
atual subiu com o pacote anterior, o procedimento abaixo é o mesmo de sempre mais três coisas
novas: a **migration 0014**, o **primeiro admin** e **duas flags** de configuração.

Stack: `docker compose` → **api** (FastAPI) + **worker** (arq) + **postgres** + **redis** +
**portal** (nginx/React). Só o **portal** é exposto: serve o SPA e faz proxy de `/api/` →
`api:8000`. Publica em `127.0.0.1:8094`.

**Polyrepo:** os dois diretórios ficam lado a lado (o compose builda o portal de
`../tecnogera-portal`). Não separar.

---

## ⚠ Duas flags precisam estar ligadas — confira ANTES de anunciar a subida

```
CHECKLIST_INGEST_ENABLED=true    # traz o checklist do Dropbox (cron de 30 min)
LLM_DISPATCH_ENABLED=true        # manda analisar — o default do CÓDIGO é false
```

Ligar só a primeira faz o portal encher de checklists **sem laudo nenhum**, todos com
indicador "sem análise". Não parece erro de configuração: parece IA que não achou nada.
O `.env.production.example` deste pacote já vem com as duas em `true` — mas **o `.env` de
produção não é recriado** (passo 2), então essas duas linhas precisam ser conferidas à mão.

## Atualização

```bash
cd tecnogera-ia-visual-api

# 1. Código novo — substitua os dois diretórios pelo conteúdo deste pacote,
#    mantendo o .env de produção que já existe.

# 2. .env — ACRESCENTAR ao que já existe, não recriar:
#      LLM_DISPATCH_ENABLED=true          <- confira, é o que liga a análise
#      CHECKLIST_INGEST_ENABLED=true      <- confira, é o que liga o cron
#      LOGIN_RATE_LIMIT_* e PASSWORD_SETUP_RATE_LIMIT_*  (opcionais — ver
#      .env.production.example; sem setar valem 5/15min por e-mail e 20/15min por origem)
chmod 600 .env

# 3. Sobe a stack  (SEMPRE com -f explícito — o override.yml é modo dev)
docker compose -f docker-compose.yml up -d --build

# 4. Migration — OBRIGATÓRIA. A API não sobe sem ela (a coluna `role` ainda não existe).
docker compose -f docker-compose.yml exec api alembic upgrade head

# 5. Primeiro admin — LEIA ISTO
#    A migration põe TODOS os usuários existentes como `operador`. Sem este passo,
#    ninguém consegue gerenciar usuários no portal.
docker compose -f docker-compose.yml exec api \
  python -m app.cli create_user --email <email-do-admin> --password '<senha-forte>' --role admin
```

## Verificação

```bash
docker compose -f docker-compose.yml ps                  # api = healthy
curl -sI http://127.0.0.1:8094/                          # 200 + headers de segurança
curl -s -o/dev/null -w '%{http_code}\n' -X POST \        # 200 (proxy nginx -> api)
  http://127.0.0.1:8094/api/v1/portal/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<email-do-admin>","password":"<senha>"}'
curl -sI https://inspecao.polarisprod.com.br             # 200 via túnel
```

Depois de logar no portal, confira que a esteira está viva de verdade: um checklist novo
deve sair de **"sem análise"** dentro de uma rodada do cron (30 min). Se todos ficarem em
"sem análise", é o `LLM_DISPATCH_ENABLED` — volte ao topo deste arquivo.

## Cloudflare Tunnel (cloudflared = systemd no host)

**Nada muda nesta entrega.** Registrado só para não se perder: o ingress em
`/etc/cloudflared/config.yml` aponta para **`http://localhost:8094`** (porta publicada no
host), **não** para o nome do container — `inspecao-web:8094` não resolve a partir do host.

```yaml
ingress:
  - hostname: inspecao.polarisprod.com.br
    service: http://localhost:8094
  - service: http_status:404   # catch-all por último
```

```bash
sudo systemctl restart cloudflared
```

## O que muda para quem usa o portal

Está no `RELEASE_NOTES.md`, mas os dois itens que geram mensagem de "quebrou" se ninguém
avisar antes:

1. **O menu tem só Checklists e Usuários.** Avarias e Relatórios saíram da navegação (as
   telas continuam funcionando por URL direta). A tela inicial passou a ser Checklists.
2. **Clicar na foto da vista amplia a imagem** em tela cheia (fecha com Esc, clique fora ou X).
3. **A esteira só processa F038** (desde a v1.1): ~71 checklists/mês, ~3 por dia útil.

## Notas

- **Portas:** portal `127.0.0.1:8094`, api `127.0.0.1:8000` — só loopback, nada na LAN/VPN.
- **Não usar** `docker-compose.override.yml` nem `docker-compose.local-validacao.yml` /
  `.faketest.yml`: são modo dev/fake. Sempre `-f docker-compose.yml`.
- **`POSTGRES_PASSWORD`** foi gravada no volume `postgres_data` no primeiro `up`; trocar
  depois exige recriar o volume.
- **Reset de senha agora existe no portal** (um admin abre uma janela de 30 min com código de
  uso único). O caminho antigo, via banco, deixa de ser necessário.
- **SQL Server do Sisloc só é alcançável via VPN.** Sem a rota, a API sobe normalmente e a
  ingestão fica sem efeito — falha por timeout de login, não por credencial.
- **Segredos não vêm neste pacote.** Chaves de Dropbox, OpenAI/Anthropic e SQL Server chegam
  por canal seguro à parte. A API **recusa subir** em produção sem nenhuma chave de LLM.
- Logs: `docker compose -f docker-compose.yml logs -f api worker portal`.
