#!/usr/bin/env bash
# backup_postgres.sh — pg_dump diário comprimido + retenção local
#
# Uso manual:
#   bash scripts/backup_postgres.sh
#
# Crontab VPS (0 3 * * *):
#   0 3 * * * /opt/tecnogera/scripts/backup_postgres.sh >> /var/log/tecnogera/backup_cron.log 2>&1
#
# Requer na VPS:
#   - Variáveis POSTGRES_USER e POSTGRES_DB disponíveis (via .env ou export)
#   - Diretórios /var/backups/tecnogera/ e /var/log/tecnogera/ criados

set -euo pipefail

# ── Configuração ──────────────────────────────────────────────────────────────

BACKUP_DIR="${BACKUP_DIR:-/var/backups/tecnogera}"
LOG_DIR="${LOG_DIR:-/var/log/tecnogera}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-tecnogera-ia-visual-postgres-1}"
POSTGRES_USER="${POSTGRES_USER:-ia_visual}"
POSTGRES_DB="${POSTGRES_DB:-ia_visual}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

# ── Criar diretórios se necessário ────────────────────────────────────────────

mkdir -p "$BACKUP_DIR"
mkdir -p "$LOG_DIR"

# ── Gerar nome do arquivo de backup ───────────────────────────────────────────

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DUMP_FILE="$BACKUP_DIR/pg_dump_${TIMESTAMP}.sql.gz"

# ── pg_dump ───────────────────────────────────────────────────────────────────

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Iniciando pg_dump: $DUMP_FILE"
docker exec "$POSTGRES_CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DUMP_FILE"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] pg_dump concluído"

# ── Log JSON ──────────────────────────────────────────────────────────────────

SIZE_BYTES=$(stat -c%s "$DUMP_FILE" 2>/dev/null || stat -f%z "$DUMP_FILE" 2>/dev/null || echo 0)
TS_NOW=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
printf '{"ts":"%s","size_bytes":%s,"path":"%s"}\n' "$TS_NOW" "$SIZE_BYTES" "$DUMP_FILE" \
    >> "$LOG_DIR/backups.log"

# ── Retenção local (apaga arquivos com mais de RETENTION_DAYS dias) ────────────

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Aplicando retenção local de $RETENTION_DAYS dias"
find "$BACKUP_DIR" -name '*.sql.gz' -mtime +"$RETENTION_DAYS" -delete

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup concluído com sucesso"
