SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

DC := docker compose
EXEC := $(DC) exec -T api

.PHONY: help
help: ## Lista os alvos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ─── Docker / dev local ──────────────────────────────────────────────────────

.PHONY: up
up: ## Sobe a stack (api + postgres + redis) em background
	$(DC) up -d

.PHONY: down
down: ## Derruba a stack preservando volumes
	$(DC) down

.PHONY: clean
clean: ## Derruba a stack e remove volumes (cuidado!)
	$(DC) down -v

.PHONY: logs
logs: ## Stream dos logs da api
	$(DC) logs -f api

.PHONY: ps
ps: ## Status dos containers
	$(DC) ps

.PHONY: build
build: ## Builda as imagens
	$(DC) build

.PHONY: shell
shell: ## Abre shell no container da api
	$(DC) exec api bash

# ─── Qualidade / DoD ────────────────────────────────────────────────────────

.PHONY: test
test: ## Roda a suíte de testes com cobertura
	$(EXEC) pytest

.PHONY: lint
lint: ## Roda ruff check
	$(EXEC) ruff check app tests

.PHONY: fmt
fmt: ## Aplica ruff format
	$(EXEC) ruff format app tests

.PHONY: fmt-check
fmt-check: ## Verifica ruff format sem alterar arquivos
	$(EXEC) ruff format --check app tests

.PHONY: type
type: ## Roda mypy --strict
	$(EXEC) mypy app

.PHONY: check
check: lint fmt-check type test ## Pipeline completo de qualidade (DoD)

# ─── Pipeline demo ──────────────────────────────────────────────────────────

.PHONY: migrate
migrate: ## Aplica migrations Alembic no container
	$(EXEC) alembic upgrade head

.PHONY: demo
demo: ## Dispara pipeline E2E contra checklist 276800 (LLM_PROVIDER=fake)
	@echo "→ Aguardando API..."
	$(EXEC) sh -c 'until curl -sf http://127.0.0.1:8000/health > /dev/null; do sleep 1; done'
	@echo "→ Disparando pipeline para checklist 276800..."
	curl -sS -X POST http://localhost:8000/api/v1/pipeline/run \
	  -H "Content-Type: application/json" \
	  -d '{"checklist_id": "276800"}' | python3 -m json.tool
	@echo ""
	@echo "→ Use 'make logs' para acompanhar o progresso."

# ─── Deploy ──────────────────────────────────────────────────────────────────

.PHONY: deploy
deploy: ## Executa deploy.sh — exige .env.deploy preenchido
	./deploy.sh
