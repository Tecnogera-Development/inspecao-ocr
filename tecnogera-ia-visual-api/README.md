# tecnogera-ia-visual-api

API do sistema de inspeção visual automatizada por IA da Tecnogera — pipeline
de 3 modelos (classificação, qualidade, geração de relatório PDF) sobre fotos
de checklists de equipamentos industriais (geradores).

> Status: **v1.1 — Sprint 2 fechado em 2026-05-28**. Cortes de custo aplicados, portal admin integrado, smoke local validado. Deploy VPS adiado para Sprint 3.

## Arquitetura — polyrepo

Este é **um** dos repositórios do produto **Tecnogera IA Visual**. Cada serviço
mora em seu próprio repositório:

| Repo | Escopo | Status |
|------|--------|--------|
| [`tecnogera-api`](https://github.com/willy-digital/tecnogera-api) (este) | FastAPI: orquestração, BFF do portal, endpoints, pipeline | v1.1 entregue |
| [`tecnogera-portal`](https://github.com/willy-digital/tecnogera-portal) | Frontend admin (Vite + React + TS) | v1.1 entregue |
| `tecnogera-ia-visual-infra` | scripts de deploy VPS, Nginx prod | Sprint 3 |

Modelos rodam embutidos no `api`. Não há serviços de ML separados em v1.1 (rotam todos via Claude Vision). CNN dedicada para qualidade entra em Sprint 3.

**Contratos entre serviços**: a `api` expõe OpenAPI em `/openapi.json`; o portal gera tipos TS via `openapi-typescript` no CI. Sem pacote compartilhado entre repos.

## Stack

- Python 3.11, FastAPI, Pydantic v2
- PostgreSQL 16 (sessões + jobs), Redis 7 (provisionado, não usado em v1.1)
- Docker / Docker Compose (api + postgres + redis + portal)
- Anthropic Claude (Modelo 1 Vision Sonnet 4.6; Modelo 3 Texto Haiku 4.5)
- Dropbox (origem das fotos + destino dos PDFs)
- WeasyPrint + Jinja2 (PDF rendering)

## Setup local

Pré-requisitos: Docker 24+, Docker Compose v2. O repo `tecnogera-portal` precisa estar em `../tecnogera-portal/` (irmão deste repo) para o serviço `portal` do compose buildar.

```bash
cp .env.example .env
# preencher: ANTHROPIC_API_KEY, DROPBOX_*, PIPELINE_API_KEY, SESSION_SECRET
docker compose up --build       # sobe api + postgres + redis + portal
make migrate                    # aplica migrations 0001..0004

# criar usuário do portal
docker exec tecnogera-ia-visual-api-1 python -m app.cli create_user \
  --email admin@tecnogera.local --password tecnogera123
```

URLs:
- API: `http://localhost:8000` (Swagger em `/docs`, healthcheck em `/health`)
- Portal: `http://localhost:3000` (login com o user criado acima)

Runbook completo: [`docs/operations/v1.1-runbook.md`](docs/operations/v1.1-runbook.md).
Release notes v1.1: [`docs/release/v1.1-notes.md`](docs/release/v1.1-notes.md).

## Desenvolvimento

```bash
# subir ambiente com hot-reload
docker compose up

# rodar testes (dentro do container)
docker compose exec api pytest

# lint + type-check
docker compose exec api ruff check app tests
docker compose exec api ruff format --check app tests
docker compose exec api mypy app
```

## Comandos via Make

```bash
make help        # lista todos os alvos
make up          # sobe a stack
make down        # derruba (preserva volumes)
make logs        # tail dos logs da api
make test        # pytest com cobertura
make lint        # ruff check
make fmt         # ruff format
make type        # mypy --strict
make check       # pipeline completo de qualidade (DoD)
make deploy      # executa deploy.sh (precisa de .env.deploy)
```

## Deploy na VPS

Estratégia v1.0: rsync + build remoto (sem registry intermediário).

```bash
cp .env.deploy.example .env.deploy
# preencher VPS_HOST, VPS_USER, VPS_PATH (e SSH_KEY se necessário)
make deploy
```

O script `deploy.sh`:
1. Valida variáveis obrigatórias.
2. Testa conexão SSH.
3. Faz `rsync` do código (excluindo `.git`, `tests/`, caches, `.env*`).
4. Executa `docker compose up -d --build` na VPS.
5. Aguarda healthcheck da API.

> Requisitos na VPS: Docker Engine + Compose v2, `rsync`, acesso SSH.

## Especificação do Relatório (IAVS-010)

Template e golden sample do relatório de inspeção em [`docs/relatorio/`](docs/relatorio/).
Use o `README.md` da pasta como roteiro de validação com a Tecnogera.

## Documentação

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — padrões, Definition of Done, checklist de PR.
- [`docs/operations/v1.1-runbook.md`](docs/operations/v1.1-runbook.md) — operação cotidiana (disparar pipeline, ver status, abrir PDF, logs, backup).
- [`docs/operations/alerts.md`](docs/operations/alerts.md) — cron de alerta jobs failed.
- [`docs/operations/backup.md`](docs/operations/backup.md) — backup Postgres diário (local).
- [`docs/operations/cost_query.md`](docs/operations/cost_query.md) — queries SQL de custo $/checklist.
- [`docs/release/v1.0-rodada-full.md`](docs/release/v1.0-rodada-full.md) — release notes v1.0 + dívidas técnicas §8.
- [`docs/release/v1.1-notes.md`](docs/release/v1.1-notes.md) — release notes v1.1 (cortes custo, portal, schema novo).
- Vault de planejamento e ADRs: `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Tecnogera/`
- Sprint 1 (22/04 → 13/05): infra Docker + pipeline Dropbox + Modelos 1 e 3.
- Sprint 2 (27/05 → 28/05): cortes de custo v1.1 + portal admin + smoke local.
- Sprint 3 (próxima): deploy VPS + Modelo 2 CNN dedicado.

## Estrutura

```
app/
  core/         # config, logging, exceções
  routers/      # endpoints HTTP (pipeline, portal, meta)
  services/     # orchestrator, classifier, llm_provider, dropbox,
                # report_generator, evaluator, shot_bank, cost_calculator,
                # batch_poller, thumb_cache, portal_query, auth, ...
  models/       # schemas Pydantic / ORM (PipelineJob, User)
  profiles/    # equipment_profiles.yaml (perfis por formulário Sisloc)
alembic/        # migrations 0001..0004
scripts/        # eval_ab, run_eval, poll_batch, probe_descriptions, ...
tests/
  unit/ integration/ e2e/ routers/ services/ portal/
```
