#!/usr/bin/env bash
# Deploy do tecnogera-ia-visual-api para a VPS Tecnogera.
#
# Estratégia (v1.0): rsync do código para a VPS e build remoto via
# `docker compose up -d --build`. Quando houver registry (planejado
# para Sprint 2), trocar por `docker push` + `docker compose pull`.
#
# Exit codes:
#   0 — sucesso
#   1 — variável obrigatória faltando
#   2 — falha de conexão SSH
#   3 — falha durante deploy remoto
#
# Requisitos: rsync, ssh, docker remoto na VPS.

set -euo pipefail

# Vars obrigatórias podem vir do ambiente OU de .env.deploy ao lado deste script.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env.deploy"

if [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    set -a; source "${ENV_FILE}"; set +a
fi

require() {
    local name="$1"
    if [[ -z "${!name:-}" ]]; then
        echo "❌ variável obrigatória ausente: ${name}" >&2
        echo "   defina no ambiente ou em .env.deploy (ver .env.deploy.example)" >&2
        exit 1
    fi
}

require VPS_HOST
require VPS_USER
require VPS_PATH

SSH_PORT="${SSH_PORT:-22}"
SSH_KEY="${SSH_KEY:-}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

ssh_opts=(-p "${SSH_PORT}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
rsync_ssh="ssh -p ${SSH_PORT} -o StrictHostKeyChecking=accept-new"
if [[ -n "${SSH_KEY}" ]]; then
    ssh_opts+=(-i "${SSH_KEY}")
    rsync_ssh="${rsync_ssh} -i ${SSH_KEY}"
fi

target="${VPS_USER}@${VPS_HOST}"
remote="${target}:${VPS_PATH}"

echo "▶ deploy: ${remote} (porta ${SSH_PORT})"

echo "▶ verificando conectividade SSH..."
if ! ssh "${ssh_opts[@]}" "${target}" "echo ok" >/dev/null 2>&1; then
    echo "❌ não foi possível conectar via SSH em ${target}" >&2
    exit 2
fi

echo "▶ garantindo diretório remoto..."
ssh "${ssh_opts[@]}" "${target}" "mkdir -p ${VPS_PATH}"

echo "▶ enviando código (rsync)..."
rsync -az --delete \
    -e "${rsync_ssh}" \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    --exclude='.ruff_cache/' \
    --exclude='.mypy_cache/' \
    --exclude='htmlcov/' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='.env' \
    --exclude='.env.deploy' \
    --exclude='docker-compose.override.yml' \
    --exclude='docker-compose.faketest.yml' \
    --exclude='tests/' \
    "${ROOT_DIR}/" "${remote}/"

echo "▶ subindo stack remota (docker compose up -d --build)..."
if ! ssh "${ssh_opts[@]}" "${target}" \
        "cd ${VPS_PATH} && docker compose -f ${COMPOSE_FILE} up -d --build"; then
    echo "❌ falha durante deploy remoto" >&2
    exit 3
fi

echo "▶ aguardando healthcheck da api..."
ssh "${ssh_opts[@]}" "${target}" "
    cd ${VPS_PATH}
    for i in \$(seq 1 30); do
        status=\$(docker compose ps --format '{{.Health}}' api 2>/dev/null || echo '')
        if [[ \"\$status\" == 'healthy' ]]; then
            echo '✓ api healthy'
            exit 0
        fi
        sleep 2
    done
    echo '⚠ api não atingiu healthy em 60s'
    docker compose logs --tail=50 api
    exit 3
"

echo "✅ deploy concluído"
