# Alerta de Jobs Failed

## Como funciona

Um cron na VPS executa a cada 15 minutos:

```
*/15 * * * * docker exec ia-visual-api python scripts/alert_failed_jobs.py >> /var/log/tecnogera/alert_failed_jobs.log 2>&1
```

O script conta jobs com `status='failed'` criados na última hora. Se a contagem exceder `ALERT_FAILED_JOBS_THRESHOLD` (padrão: 3), escreve uma linha JSON em `/var/log/tecnogera/alerts.log` e, se `ALERT_EMAIL_TO` estiver configurado, envia email via `ssmtp`.

## Configuração

Variáveis de ambiente relevantes (em `.env` na VPS):

| Variável | Default | Descrição |
|---|---|---|
| `ALERT_FAILED_JOBS_THRESHOLD` | `3` | Número mínimo de falhas por hora para disparar alerta |
| `ALERT_EMAIL_TO` | _(vazio)_ | Endereço de email para receber alertas. Se vazio, apenas grava no arquivo de log. |

### Ajustar threshold

```bash
# No .env da VPS:
ALERT_FAILED_JOBS_THRESHOLD=5

# Reiniciar o container para pegar o novo valor:
docker compose up -d --no-deps api
```

### Configurar ssmtp (opcional)

1. Instalar na VPS: `sudo apt-get install ssmtp`
2. Editar `/etc/ssmtp/ssmtp.conf`:
   ```
   root=postmaster
   mailhub=smtp.gmail.com:587
   AuthUser=seu-email@gmail.com
   AuthPass=sua-senha-de-app
   UseSTARTTLS=YES
   ```
3. Adicionar `ALERT_EMAIL_TO=devops@tecnogera.com` no `.env` e reiniciar.

## Verificar alertas

```bash
# Últimos alertas disparados:
tail -f /var/log/tecnogera/alerts.log

# Exemplo de linha JSON:
# {"ts": "2026-05-27T03:15:01.234567+00:00", "count": 5, "threshold": 3}

# Log de execução do cron (inclui saídas "ok" e "triggered"):
tail -f /var/log/tecnogera/alert_failed_jobs.log
```

## Preparar diretório na VPS

```bash
sudo mkdir -p /var/log/tecnogera
sudo chown $USER:$USER /var/log/tecnogera
```

## Adicionar ao crontab da VPS

```bash
crontab -e
# Adicionar:
*/15 * * * * docker exec ia-visual-api python scripts/alert_failed_jobs.py >> /var/log/tecnogera/alert_failed_jobs.log 2>&1
```

## Teste manual

Injetar 4 jobs failed no Postgres e aguardar a próxima execução:

```sql
-- No psql da VPS:
INSERT INTO pipeline_jobs (id, checklist_id, status, created_at, updated_at, mode)
SELECT gen_random_uuid(), '999999', 'failed', NOW(), NOW(), 'sync'
FROM generate_series(1, 4);
```

Depois executar manualmente e verificar o log:

```bash
docker exec ia-visual-api python scripts/alert_failed_jobs.py
tail -1 /var/log/tecnogera/alerts.log
```
