# Contributing — tecnogera-ia-visual-api

Este documento define o padrão de qualidade e os critérios de revisão de PR para
este repositório (a **API** do produto **Tecnogera IA Visual**). O produto é
organizado em **polyrepo** — outros repositórios (portal, workers, infra) seguem
este mesmo padrão, com adaptações de stack quando aplicável.

Todo PR deve atender integralmente à **Definition of Done (DoD)** e passar pelos
itens do **Code Review Checklist** antes de merge em `develop`. Merge em `main`
só a partir de `develop` em release.

> Idioma do código, comentários e documentação: **português brasileiro** (exceto identificadores de código e termos técnicos consagrados).

---

## 1. Workflow

### 1.1 Branches
- `main` — produção. Protegida. Só recebe merge de `develop` via release.
- `develop` — integração. Protegida. Recebe PRs de feature branches.
- `feat/IAVS-NNN-descricao-curta` — feature branches, uma por card.
- `fix/IAVS-NNN-descricao` — bugfix.
- `chore/...`, `docs/...`, `refactor/...` — quando não há card associado.

### 1.2 Commits — Conventional Commits
Formato obrigatório:
```
<tipo>(<escopo>): <resumo no imperativo, minúsculas, sem ponto final>

<corpo opcional explicando o porquê>

Refs: IAVS-NNN
```
Tipos aceitos: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `build`, `ci`.

Exemplo:
```
feat(dropbox): adiciona download de imagens por checklist_id

Implementa DropboxService.download_checklist_batch usando refresh
token. Salva em /tmp/checklists/{id}/ preservando nomenclatura.

Refs: IAVS-004
```

### 1.3 Tamanho de PR
- **Máximo 400 LOC** alterados (excluindo testes, lockfiles e arquivos gerados).
- **Um card por PR**. PRs maiores devem ser quebrados.
- PRs com >400 LOC só com justificativa explícita no corpo do PR.

---

## 2. Definition of Done (DoD)

Um card só é considerado **Done** quando:

- [ ] **Critério de aceite do card** (em `sprint-planning-v1.md`) integralmente atendido.
- [ ] **Testes automatizados** cobrindo o código novo:
  - Unit tests para toda lógica de domínio/serviço.
  - Integration tests para integrações externas (Dropbox, APIs de IA, banco) com VCR/mocks deterministas.
  - E2E test para o fluxo principal quando aplicável (cards de pipeline).
  - **Cobertura ≥ 85% no código novo** (medida por `pytest-cov --cov=app --cov-fail-under=85`).
- [ ] **Lint e type-check verdes**: `ruff check`, `ruff format --check`, `mypy --strict app/`.
- [ ] **Docker build verde** (`make build` ou `docker compose build`).
- [ ] **CI verde** (quando configurado).
- [ ] **Sem secrets no diff** — verificar `.env`, tokens, API keys, URLs com credenciais embutidas.
- [ ] **Logs estruturados** (JSON) em todos os pontos de entrada/serviço, com `request_id` quando aplicável. Sem `print`.
- [ ] **OpenAPI atualizado**: todo endpoint novo tem `response_model`, `status_code`, `summary` e `description`.
- [ ] **README/docs atualizados** se houve mudança em setup, variáveis de ambiente, comandos, ou arquitetura.
- [ ] **ADR criado** em `decisions/` se houve decisão arquitetural relevante.
- [ ] **Sessão registrada** no vault Obsidian (`sessions/2026/session-YYYY-MM-DD-*.md`).
- [ ] **Self-review** concluído (autor leu o próprio diff antes de pedir review).
- [ ] **Template de PR** preenchido completamente.

---

## 3. Padrões de código

### 3.1 Stack base
- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.x (quando houver DB), Celery + Redis (quando houver fila), pytest.
- Gerenciamento: `pyproject.toml` + `uv` ou `pip-tools` (lockfile versionado).
- Linter/formatter: **ruff** (substitui black, isort, flake8). Type-check: **mypy --strict**.

### 3.2 Estrutura modular obrigatória
```
app/
  core/         # config, logging, exceções, dependências
  routers/      # endpoints HTTP — finos, sem lógica de negócio
  services/     # regra de negócio, integrações externas
  models/       # Pydantic schemas + ORM (se houver)
  workers/      # tasks Celery
tests/
  unit/         # mockam IO
  integration/  # tocam serviços reais (containers de teste)
  e2e/          # pipeline completo
```

### 3.3 Regras de design
- **Routers são finos**: validação + chamada a service + serialização. Zero regra de negócio.
- **Services são puros quando possível**: receber dados, retornar dados. IO encapsulado em adapters.
- **Interfaces antes de implementações** para integrações externas (Dropbox, providers de IA) — facilita teste e troca.
- **Configuração via Pydantic Settings**, lida uma vez no boot. Falhar rápido se var faltando.
- **Exceções customizadas** com hierarquia (`AppError → DomainError, IntegrationError, ValidationError`). Handler global no FastAPI mapeia para HTTP.
- **Sem `try/except: pass`**. Todo catch deve logar e/ou re-lançar com contexto.
- **Sem mutáveis globais**. Use Depends do FastAPI ou injeção explícita.

### 3.4 Comentários e docstrings
- **Por padrão, não escreva comentário**. Identificadores bem nomeados já dizem o quê.
- Comentário só quando o **porquê** não é óbvio: invariante escondida, workaround, decisão contraintuitiva.
- Docstrings curtas em serviços públicos (1-3 linhas, formato Google ou similar).
- **Não escrever docstring que repete o nome da função.**

### 3.5 Logging
- `structlog` ou `logging` configurado para JSON estruturado.
- Campos mínimos: `timestamp`, `level`, `event`, `request_id`, `service`.
- **Nunca logar**: tokens, senhas, payload completo de imagem (só metadados), PII de cliente.
- Níveis: `DEBUG` dev only, `INFO` eventos de negócio, `WARNING` degradação, `ERROR` falha que precisa atenção.

### 3.6 Segurança
- `.env` no `.gitignore`. **Nunca commitar**. Usar `.env.example` com placeholders.
- Validação de input em todo endpoint (Pydantic resolve a maior parte).
- Imagens baixadas: validar tamanho máximo e tipo MIME.
- Dependências auditadas: `pip-audit` ou `safety` no CI.
- Pre-commit com `gitleaks` ou `detect-secrets` para impedir vazamento.

---

## 4. Testes

### 4.1 Princípios
- **Testar comportamento, não implementação.**
- **Sem mock do banco em integration tests** — usar Postgres em container (testcontainers ou docker-compose.test).
- Mocks só para o que é caro/externo (APIs pagas, Dropbox em unit tests). Em integration, usar VCR (`pytest-recording`) para gravar e replay.
- Testes determinísticos. Sem `time.sleep`, sem rede aberta sem cassette.
- Nome de teste descreve o cenário: `test_classify_image_returns_field_name_when_image_matches_known_field`.

### 4.2 Níveis
| Nível | Escopo | Velocidade | Quando rodar |
|-------|--------|-----------|--------------|
| Unit | Funções/classes isoladas, IO mockado | <1s/teste | Toda mudança |
| Integration | Service + dependências reais (DB, container Dropbox sim, etc) | <10s/teste | Toda mudança |
| E2E | Pipeline completo via API | <60s/teste | Pre-merge e nightly |

### 4.3 Cobertura
- Mínimo **85%** no código novo (linhas + branches).
- 100% obrigatório em: parsing de configuração, validação de input, lógica de classificação/qualidade, conversão markdown→PDF.
- Cobertura medida em CI; build falha abaixo do limiar.

---

## 5. Docker

- **Multi-stage builds** (builder → runtime).
- **Imagens base oficiais e pinadas** (`python:3.11-slim`, não `latest`).
- **Usuário não-root** no estágio runtime.
- `HEALTHCHECK` definido em todo serviço.
- `.dockerignore` excluindo `.git`, `.venv`, `__pycache__`, `tests/`, `.env*`.
- `docker-compose.yml` para produção; `docker-compose.override.yml` para dev (volumes, hot-reload).
- Hot-reload em dev via `uvicorn --reload`.

---

## 6. Code Review Checklist (revisor)

O revisor deve verificar:

### Correção
- [ ] Faz o que o card descreve? Critério de aceite atendido?
- [ ] Edge cases cobertos (input vazio, erro de rede, timeout, dados malformados)?
- [ ] Não introduz regressão visível em testes existentes?

### Design
- [ ] Lógica está na camada certa (router fino / service grosso)?
- [ ] Abstrações justificadas (não é over-engineering)?
- [ ] Interfaces para integrações externas?
- [ ] Configuração externalizada?

### Testes
- [ ] Cobertura ≥85% no código novo?
- [ ] Testes testam comportamento e são legíveis?
- [ ] Sem flakiness (sleeps, dependência de ordem, rede aberta)?
- [ ] Mocks usados com parcimônia em integration?

### Segurança
- [ ] Nenhum secret no diff?
- [ ] Input validado?
- [ ] Logs sem PII/credenciais?
- [ ] Dependências sem CVE conhecido?

### Operação
- [ ] Logs estruturados nos pontos certos?
- [ ] Erros tratados e propagados com contexto?
- [ ] OpenAPI completo e correto?
- [ ] Healthcheck funcionando?

### Documentação
- [ ] README atualizado se setup mudou?
- [ ] ADR para decisão arquitetural?
- [ ] Sessão registrada no vault?

### Estilo
- [ ] Lint + type-check verdes?
- [ ] Sem comentários redundantes ou TODOs órfãos?
- [ ] Nomes claros e em PT-BR (quando aplicável a domínio)?

---

## 7. Política de aprovação

- **1 aprovação** obrigatória de outro membro do time.
- CI verde obrigatório.
- Conversas de review **resolvidas** antes do merge (ou explicitamente dispensadas com justificativa).
- **Squash merge** em `develop` para histórico limpo. Mensagem do squash deve seguir Conventional Commits.
- Autor não pode aprovar o próprio PR. Pode fazer auto-merge após aprovação + CI.

---

## 8. ADRs (Architecture Decision Records)

Criar ADR quando:
- Escolha entre alternativas técnicas com trade-offs (ex: Azure CV vs Vision LLM).
- Mudança de stack ou padrão arquitetural.
- Decisão que afeta múltiplos serviços ou contratos externos.

Local: `vault/decisions/ADR-NNN-descricao.md`. Status: `proposed → accepted → deprecated`.

---

## 9. Quando algo não se encaixa

Se uma regra deste documento atrapalhar uma entrega legítima, **abra uma discussão** (issue ou comentário no PR) e proponha uma exceção pontual ou atualização do documento. Não burlar silenciosamente.
