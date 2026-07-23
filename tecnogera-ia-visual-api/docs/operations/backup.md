# Backup Postgres Diário

## Como funciona

Cron diário às 03:00 UTC na VPS executa `backup_postgres.sh`:
- `pg_dump` do banco comprimido com gzip
- Arquivo salvo em `/var/backups/tecnogera/`
- Retenção local de 7 dias (`find -mtime +7 -delete`)
- Linha JSON registrada em `/var/log/tecnogera/backups.log`

## Instalação na VPS

### 1. Criar diretórios

```bash
sudo mkdir -p /var/backups/tecnogera
sudo mkdir -p /var/log/tecnogera
sudo chown $USER:$USER /var/backups/tecnogera /var/log/tecnogera
```

### 2. Copiar script do repo

```bash
cp scripts/backup_postgres.sh /opt/tecnogera/scripts/
chmod +x /opt/tecnogera/scripts/backup_postgres.sh
```

### 3. Adicionar ao crontab

```bash
crontab -e
# Adicionar:
0 3 * * * /opt/tecnogera/scripts/backup_postgres.sh >> /var/log/tecnogera/backup_cron.log 2>&1
```

## Variáveis de ambiente

| Variável | Default | Descrição |
|---|---|---|
| `BACKUP_DIR` | `/var/backups/tecnogera` | Diretório local de backups |
| `LOG_DIR` | `/var/log/tecnogera` | Diretório de logs |
| `POSTGRES_CONTAINER` | `tecnogera-ia-visual-postgres-1` | Nome do container Docker |
| `POSTGRES_USER` | `ia_visual` | Usuário do Postgres |
| `POSTGRES_DB` | `ia_visual` | Database do Postgres |
| `RETENTION_DAYS` | `7` | Dias de retenção local |

## Monitorar backups

```bash
# Log de execução do cron:
tail -f /var/log/tecnogera/backup_cron.log

# Registro JSON por backup (ts + tamanho + caminho local):
tail -f /var/log/tecnogera/backups.log
# Exemplo: {"ts":"2026-05-27T03:00:15+00:00","size_bytes":524288,"path":"/var/backups/tecnogera/pg_dump_20260527T030000Z.sql.gz"}

# Listar backups locais:
ls -lh /var/backups/tecnogera/
```

## Smoke test manual

```bash
bash /opt/tecnogera/scripts/backup_postgres.sh
ls -lh /var/backups/tecnogera/*.sql.gz | tail -1
tail -1 /var/log/tecnogera/backups.log
```

## Restaurar (procedimento manual — IAVS-054)

> O smoke test de restore é executado após 1 semana de backups acumulados.

```bash
# 1. Subir Postgres de teste:
docker run --rm -d --name postgres-test -p 5433:5432 \
  -e POSTGRES_PASSWORD=test postgres:16-alpine

# 2. Restaurar a partir do dump local mais recente:
DUMP=$(ls -t /var/backups/tecnogera/*.sql.gz | head -1)
gunzip -c "$DUMP" | docker exec -i postgres-test psql -U postgres

# 3. Verificar:
docker exec postgres-test psql -U postgres -c "SELECT COUNT(*) FROM pipeline_jobs;"

# 4. Remover container de teste:
docker stop postgres-test
```
