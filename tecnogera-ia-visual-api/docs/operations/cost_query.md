# Queries de Custo — pipeline_jobs.metrics.estimated_cost_usd

`estimated_cost_usd` é calculado pelo `Orchestrator` ao finalizar cada job e
persistido em `pipeline_jobs.metrics` como float em USD.

## Média dos últimos 5 jobs (gate semanal do PM)

```sql
SELECT AVG((metrics->>'estimated_cost_usd')::float)
FROM pipeline_jobs
WHERE status='done'
  AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC
LIMIT 5;
```

## Custo total por dia (últimos 30 dias)

```sql
SELECT
  DATE(created_at) AS dia,
  COUNT(*)         AS n_jobs,
  SUM((metrics->>'estimated_cost_usd')::float) AS custo_total_usd
FROM pipeline_jobs
WHERE status = 'done'
  AND created_at > NOW() - INTERVAL '30 days'
  AND metrics ? 'estimated_cost_usd'
GROUP BY 1
ORDER BY 1 DESC;
```

## Jobs com custo acima do threshold (outliers)

```sql
SELECT
  id,
  checklist_id,
  created_at,
  (metrics->>'estimated_cost_usd')::float AS custo_usd
FROM pipeline_jobs
WHERE status = 'done'
  AND (metrics->>'estimated_cost_usd')::float > 2.0
ORDER BY custo_usd DESC
LIMIT 20;
```

## Target de custo por release

| Release | Target | Baseline |
|---------|--------|----------|
| v1.0    | —      | $1.73/checklist |
| v1.1    | $0.45/checklist | TBD pós-deploy |
