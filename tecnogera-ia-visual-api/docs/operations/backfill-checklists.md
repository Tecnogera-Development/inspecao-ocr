# Backfill — reprocessar um checklist antigo

Código: `app/services/checklist_backfill.py`, `app/routers/checklists.py`

## Para que serve

A esteira automática (cron de 30 min, ticket 07) descobre checklists por **delta
de cursor do Dropbox**. O cursor é fixado no "agora" quando a esteira é ativada,
e isso **é** o marco de corte: nada anterior à ativação entra, nunca. É
deliberado — varrer `/Sisloc` inteiro levou 67 min na medição do ticket 01, o
que não cabe num cron de 30.

A consequência é que "quero rodar aquele checklist de junho" não tem caminho.
Este endpoint é esse caminho. Ele entra por `checklist_id` explícito (busca
textual no Dropbox, não delta) e **ignora o marco de corte** — não lê nem
escreve `ingest_cursors` e não aplica `CHECKLIST_INGEST_SINCE`.

Não existe backfill por intervalo de datas, por decisão de escopo. Se você
precisa de "tudo de junho", isso é `CHECKLIST_INGEST_BOOTSTRAP_FULL` — outra
ferramenta, outro perfil de risco.

## Como chamar

```bash
curl -X POST https://<host>/api/v1/checklists/backfill \
  -H "X-API-Key: $PIPELINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"checklist_ids": ["278749"]}'
```

O id é o número que aparece no nome do arquivo no Dropbox, entre `checklist_` e
o código da vista: `153269005_checklist_278749_c54_0_15_02_2026 09_00_00.jpeg`.

### Resposta

`202` quando **ao menos um** id virou job; `422` com o mesmo detalhamento
quando **nenhum** qualificou (para que uma recusa não passe por sucesso na
automação de quem chama).

```json
{
  "solicitados": 2,
  "aceitos": 1,
  "recusados": 1,
  "duplicados_na_requisicao": 0,
  "teto_por_requisicao": 20,
  "chamadas_visao_estimadas": 3,
  "job_ids": ["8f2c…"],
  "itens": [
    {"checklist_id": "278749", "aceito": true, "job_id": "8f2c…",
     "formulario": "F180-VISITA GMG_REV04", "campos": ["c54","c55","c56"],
     "reprocessamento": false, "tentativa": 1,
     "detalhe": "Job criado em 'pending' com 3 vista(s): …"},
    {"checklist_id": "278750", "aceito": false,
     "motivo": "campo_faltante:c55", "campos_faltantes": ["c55"],
     "detalhe": "Formulário F180 aceito, mas faltam as vistas obrigatórias: c55 (lateral esquerda)…"}
  ],
  "aviso": "Jobs criados em status 'pending'. …"
}
```

`chamadas_visao_estimadas` é o número que vira dinheiro: uma chamada de visão
por vista aceita.

## O que o endpoint NÃO faz

**Não chama LLM e não despacha nada.** Ele materializa `pipeline_jobs` em
`pending`, exatamente como o cron faz. A execução — e com ela o custo de token —
é do despacho da análise (ticket 08), que aplica o kill switch
(`LLM_DISPATCH_ENABLED`, default `false`), o teto de chamadas por rodada
(`LLM_MAX_CALLS_PER_RUN`) e o orçamento mensal (`LLM_MONTHLY_BUDGET_USD`).

Corolário prático: com `LLM_DISPATCH_ENABLED=false` — o default — um backfill é
completamente gratuito. Os jobs ficam em `pending` até alguém ligar o despacho
de propósito.

Isso é intencional: um caminho de backfill que despachasse direto driblaria o
único lugar do sistema que mede gasto real.

**Não escreve no Dropbox.** Só `files_search_v2` / `files_get_metadata`.
**Não escreve no SQL Server.** Uma única consulta `SELECT` em lote por
requisição — nunca uma query por id atravessando a VPN.

## Guarda-corpo de lote

`CHECKLIST_BACKFILL_MAX_IDS` (default **20**) limita quantos ids uma requisição
aceita. Acima disso: `422`, nada é criado e nem Dropbox nem SQL Server são
tocados.

O teto é de **gasto**, não de performance. Cada checklist aceito vira 3–4
chamadas de visão. Reprocessar 500 checklists "para testar" é o vetor de fatura
surpresa deste projeto; com o teto isso vira 25 chamadas deliberadas em vez de
um `curl` distraído.

O valor 20 foi escolhido para caber inteiro numa rodada de análise
(`CHECKLIST_ANALYSIS_MAX_JOBS_PER_RUN` = 25): o operador vê o resultado do lote
inteiro num ciclo, em vez de a fila transbordar para rodadas seguintes.

## Reprocessamento

Um id que já rodou antes gera **execução nova**. O job anterior fica intacto em
`pipeline_jobs` — é justamente com ele que se compara. O campo `tentativa` diz
qual passada é esta (`1` = primeira vez) e `reprocessamento: true` sinaliza que
já havia histórico.

`checklist_ingest_state` **não** é histórico: é o livro-razão da esteira
automática, uma linha por checklist respondendo "o cron já resolveu este id?".
O backfill atualiza essa linha para apontar ao job mais recente e a deixa
`materializado` com `motivo = "backfill"` — o que também impede o cron de criar
um terceiro job para o mesmo id na rodada seguinte.

Um checklist marcado `descartado` no ledger **pode** ser backfillado: `descartado`
é a palavra final para o cron, não para um humano que pediu explicitamente. O
filtro é reavaliado do zero, com os dados de hoje.

## Motivos de recusa

Todos vêm com um `detalhe` em português dizendo o que falta e qual é a saída.

| `motivo` | O que aconteceu | O que fazer |
|---|---|---|
| `sem_imagens` | Nenhuma imagem com esse id no Dropbox | Conferir o número; ver se a filial está sincronizada |
| `formulario_ausente` | O id não existe em `dbo.checklist_produto` | Ver abaixo |
| `formulario_vazio` | A linha existe, a coluna `formulario` está vazia (~36% do parque) | Nada a fazer pelo nosso lado; é dado do ERP |
| `formulario_fora_whitelist:F277` | Formulário fora de `{F180, F038}` | Correto — F277 é plataforma, outra taxonomia |
| `campo_faltante:c55` | Formulário certo, falta vista obrigatória | Conferir se o técnico fotografou; a foto pode chegar depois |

### `formulario_ausente` — a decisão

**Recusa explícita, sem criar job.** ~1,1% dos checklists com foto no Dropbox
nunca aparecem na view (medido: 291 de 26.365) e **não é atraso do ERP**.

O motivo de recusar em vez de processar assim mesmo: o filtro tem uma ordem
obrigatória — formulário primeiro, campos depois — porque `cN` é código *por
formulário*. Sem a linha do ERP não dá para saber se `c54` é "lateral direita"
(F180) ou outra coisa; a inspeção sairia com taxonomia de gerador sobre um
equipamento possivelmente diferente. Um laudo errado é pior que laudo nenhum.

**Saída de emergência**: `POST /api/v1/pipeline/run` com o `checklist_id`
processa sem aplicar o filtro. É deliberadamente um endpoint diferente — quem o
usa está assumindo a responsabilidade de que o equipamento é um gerador.

## Erros de integração

Ao contrário do cron, o backfill **não engole** falha de integração: Dropbox
fora do ar ou VPN caída viram `502 integration_error` e nada é criado. Aqui não
existe "próxima rodada" para corrigir em silêncio — quem pediu o backfill
precisa saber que ele não aconteceu.

## Por que endpoint e não comando de CLI

1. Quem precisa disso não tem shell. O pedido nasce de quem opera o portal; a
   alternativa seria alguém com acesso à VM `tng-brsdtcapp01` rodar
   `docker compose exec`, o que na prática vira "manda mensagem para o dev".
2. `X-API-Key` é o guarda-corpo que o ticket pede, e ele só existe no HTTP.
3. Uma superfície só, um teto de lote só. Um comando de CLI paralelo seria mais
   um caminho a auditar e mais uma chance de driblar o freio.
