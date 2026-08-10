# Convenção de ingestão — Eventos de Avaria no Dropbox

## Estrutura de pastas

```
/Avarias/
  {asset_code}/
    {YYYYMMDD}_{HHMMSS}_{moment}_{angle}_{uploaded_by}.{ext}
```

### Campos

| Campo | Descrição | Exemplos |
|---|---|---|
| `asset_code` | Código do ativo (pasta) | `FROTA001`, `CAM-027` |
| `YYYYMMDD_HHMMSS` | Data e hora da captura | `20260601_143022` |
| `moment` | Momento da vistoria | `saida` ou `retorno` |
| `angle` | Ângulo canônico (sem underscores) | `frontal`, `traseira`, `lateralesq`, `lateraldir`, `teto`, `interior` |
| `uploaded_by` | Identificador do responsável (sem underscores) | `joao`, `maria`, `tec01` |
| `ext` | Extensão | `jpg`, `jpeg`, `png` |

### Exemplos válidos

```
/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg
/Avarias/CAM-027/20260610_083000_retorno_traseira_tec01.jpg
/Avarias/FROTA001/20260601_144500_retorno_lateralesq_maria.png
```

### Exemplos inválidos (→ `metadata_missing`)

```
/Avarias/FROTA001/foto.jpg              ← sem campos estruturados
/Avarias/FROTA001/20260601.jpg          ← sem moment/angle/uploader
/Avarias/20260601_143022_saida_frontal_joao.jpg  ← sem pasta de ativo
```

## Regras de formação

1. **asset_code** é o nome exato da pasta diretamente sob `/Avarias/`. Pode conter letras, dígitos e hífens (`-`). **Sem** underscores.
2. **angle** e **uploaded_by** **não** contêm underscores — use camelCase ou abreviações (ex.: `lateralesq`, `lateraldir`).
3. Arquivos fora desse padrão são ingeridos com `status="metadata_missing"` e não seguem para classificação.
4. O Dropbox path completo é armazenado em `source_path` (UNIQUE) — reuploads do mesmo arquivo são silenciosamente ignorados (dedup).

## Ciclo de vida do Evento

```
Ingest ──► metadata ok? ──► status=queued  ──► worker ──► validate_technical
                    └──► no ──► status=metadata_missing (parado aqui)

worker:
  validate_technical ok? ──► status=done (futuro: classificar em IAVS-063)
                    └──► no ──► status=nao_processavel + validation_reason
```

## Concorrência e fila

O endpoint `POST /api/v1/events/ingest` enfileira eventos para o worker Arq.
Capacidade: `EVENT_QUEUE_CONCURRENCY` (default 30, §3.4 do Anexo I).
Eventos além da capacidade aguardam na fila Redis (comportamento natural do broker).
