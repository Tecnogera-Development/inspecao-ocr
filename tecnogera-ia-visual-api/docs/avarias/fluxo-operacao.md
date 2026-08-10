# Fluxo de Operação — Tela de Avarias

**Data**: 2026-06-13
**Status**: proposta para validação de negócio (Tecnogera)
**Tela**: `/avarias` no `tecnogera-portal` — **requisito principal do sistema** (Anexo I §2.1)

> Este documento amarra o fluxo de ponta a ponta da tela de avarias e serve
> para alinhar com Célio (Operações), Edelmar (TI) e João (PM) **antes** da
> subida. Responde as quatro perguntas de operação e define as melhorias
> visuais/de fluxo necessárias.

---

## 1. A ideia central — o checklist de entrega é a base de comparação

O sistema não precisa de um "gabarito" curado à parte. **O checklist de
entrega/liberação do equipamento já existe no Sisloc e já tem fotos do
equipamento no estado bom** — ele **é** o "exemplo certo".

O `checklist_id` (agora presente em cada evento de avaria) é a **chave de
junção** entre os dois produtos:

```
Foto de RETORNO + checklist_id
        │
        ▼
  Puxa as fotos do checklist de ENTREGA (Sisloc) via checklist_id   ◄── "como saiu"
        │
  Vision LLM compara retorno × entrega:
        ├─ item que estava e sumiu       → ausência de item
        ├─ estado que mudou / avariou    → dano visível
        └─ fora do que a entrega mostra  → fora do padrão visual
        │
  Tela: [ENTREGA]  |  [RETORNO]  +  veredito  +  ação do operador
```

**Consequências:**
- Elimina a dependência do dataset de gabarito (IAVS-059) como referência de runtime.
- Elimina a captura separada de "saída" — a entrega já documentou o estado inicial.
- Reusa `DropboxService.list_checklist_images(checklist_id)`, que já existe.

---

## 2. As quatro perguntas de operação

### 2.1 Como o operador acessa?
- Login no portal (sessão de 8h) → link **"Avarias"** no header → `/avarias`.
- Visão única compartilhada. Filtro por **filial** (derivado do ativo) para navegação.
- RBAC por filial fica para v1.1 (só se o Edelmar pedir segregação).

### 2.2 O que aparece nela?
- **Lista**: um registro por par entrega×retorno, com **ID Checklist**,
  **Ativo**, **Indicador** (Conforme / Não conforme), **Severidade** e
  **Data de processamento**. Filtros por indicador, filial e ID Checklist.
- **Detalhe**: comparação **entrega | retorno** lado a lado, veredito, a lista de
  não conformidades (classe, confiança, severidade, observação) e a origem da
  base (qual checklist de entrega).

### 2.3 O que é processado nela?
- A tela é **read-only** para os dados de IA — o processamento acontece no
  worker, **antes**, disparado por ingestão **automática** (job agendado que
  varre o Dropbox a cada ~10 min). O operador não dispara nada na mão.
- A **única escrita** na tela é a **validação humana** (§2.4 abaixo).

### 2.4 A comparação usa o que de exemplo certo e errado?
- **Certo** = as fotos do **checklist de entrega** (Sisloc), puxadas pelo `checklist_id`.
- **Errado / avaliado** = a foto de **retorno** enviada para `/Avarias`.
- O modelo recebe as duas e emite o veredito por classe. Sem base de entrega
  vinculada → cai em **avaliação apenas visual** (só exibe, operador julga).

---

## 3. Fluxo de negócio ponta a ponta

```
1. Locação passa pelo checklist de entrega no Sisloc  →  fotos no /Sisloc
2. Na devolução, o operador fotografa o equipamento e envia para
   /Avarias/{ativo}/{...}_{uploader}_{checklist_id}.jpg
3. Ingestão automática materializa o evento de retorno (status=queued)
4. Worker:
     a. valida (formato, resolução, foco)
     b. puxa as fotos de entrega pelo checklist_id  (a base)
     c. Vision LLM classifica o retorno contra a entrega
     d. gera o composto entrega|retorno com o veredito queimado
5. Operador abre /avarias, revisa o par, e CONFIRMA ✔ ou CORRIGE ✘ o veredito
     → grava ground_truth → alimenta a métrica de aceite (F1 por classe)
```

O passo 5 é o que **tira a tela do "solto"**: o operador não só olha — ele
**fecha o julgamento**, e cada validação vira o dado que o contrato exige medir.

---

## 4. Melhorias visuais e de fluxo

### 4.1 Lista (`/avarias`)

```
┌────────────────────────────────────────────────────────────────────┐
│ Avarias                                                             │
│                                                                    │
│ [Indicador ▾] [Filial ▾] [ID Checklist____]     3 não conformes ● │  ← contadores operacionais
│                                                                    │
│ ID CHECKLIST │ ATIVO   │ INDICADOR      │ SEVER. │ PROCESSADO      │
│ ┃ 117183887  │ GER-AV02 │ ● Não conforme │ alta  │ 13/06 10:30    │  ← faixa vermelha à esquerda
│   276800     │ GER-REAL │ ○ Conforme     │  —    │ 13/06 09:00    │
│   —          │ GER-XX   │ ⚠ Sem base     │  —    │ 13/06 08:00    │  ← sem checklist de entrega
└────────────────────────────────────────────────────────────────────┘
```

Mudanças:
- **Indicador como âncora visual**: linha não-conforme ganha faixa vermelha à esquerda; não é só um pill.
- **Estado "Sem base"** explícito quando não há checklist de entrega vinculado — o operador entende *por que* não houve comparação.
- **Contadores operacionais** (não-conformes, a validar) — contagem simples, **sem gráficos** (guarda-corpo §2.7).
- **Filtro por filial** e busca por **ID Checklist**.

### 4.2 Detalhe (`/avarias/:id`) — o coração da tela

```
┌────────────────────────────────────────────────────────────────────┐
│ ← Avarias   GER-AV02 · Checklist 117183887 · 13/06/2026           │
│                                                                    │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │  ● NÃO CONFORME  —  Dano visível · severidade ALTA (87%)      │ │  ← banner de veredito
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│ ┌─────────────── ENTREGA ──────────┐ ┌───────── RETORNO ────────┐ │
│ │  "como saiu"                     │ │  "como voltou"           │ │
│ │  [ foto do checklist de entrega ]│ │  [ foto de avaria        ]│ │  ← comparação lado a lado
│ │  Checklist 117183887 · 01/09/22  │ │  13/06/2026 · lat. dir.  │ │
│ └──────────────────────────────────┘ └──────────────────────────┘ │
│                                                                    │
│ Não conformidades detectadas:                                      │
│  • Dano visível — lateral direita — conf. 87% — sev. alta          │
│    "Amassado visível na lateral direita"                           │
│                                                                    │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │  Esta avaliação está correta?   [ Confirmar ✔ ] [ Corrigir ✘ ]│ │  ← HITL: grava ground-truth
│ └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

Mudanças:
- **Banner de veredito** no topo: CONFORME / NÃO CONFORME grande, com classe + severidade + confiança.
- **Comparação entrega | retorno** rotulada como "como saiu" vs "como voltou", cada lado com a origem/data.
- **Origem da base** explícita: "Checklist 117183887 · 01/09/2022".
- **Lista de não conformidades** a partir do JSON (classe, confiança, severidade, observação).
- **Ação HITL**: Confirmar ✔ / Corrigir ✘ → grava `ground_truth_class`; mostra quem/quando validou.
- **Sem base de entrega**: painel esquerdo vira "Sem checklist de entrega vinculado — avaliação apenas visual", mas o HITL continua disponível.
- O **composto anotado** continua acessível para download (evidência/auditoria).

### 4.3 Melhorias de fluxo (fora da tela)
- **Ingestão automática** (job agendado) — resultados aparecem sem trigger manual.
- **Loop de validação** (confirmar/corrigir) alimentando o ground-truth → eval (F1).
- **Vínculo pelo `checklist_id`** no nome do arquivo de retorno — automatiza a base.

---

## 5. Fases de entrega

| Fase | Escopo | Depende de |
|---|---|---|
| **Fase 1** (subível) | Comparação **entrega × retorno** via `checklist_id`; ingestão automática; tela com banner + comparação + HITL confirmar/corrigir | Chave Anthropic válida; fix do bug de ingest |
| **Fase 2** | Casamento fino ângulo↔campo; múltiplas fotos de entrega como contexto cacheado; relatório de métricas §8 | Volume de validações HITL (ground-truth acumulado) |

---

## 6. Pré-requisitos técnicos para subir

1. **Corrigir o bug do ingest**: o scan varre `/Avarias` recursivamente e engole
   subpastas de sistema (`_anotados/`, futuramente `_gabaritos/`) como se fossem
   ativos. Ignorar pastas com prefixo `_`.
2. **Chave Anthropic válida** no `.env` (a atual está expirada — hoje roda em modo fake).
3. **Ligar a base de entrega**: no `process_event`, puxar as fotos do checklist
   via `list_checklist_images(event.checklist_id)` e passá-las como referência
   ao classificador (`saida_bytes`/contexto).
4. **Ingestão agendada** (Arq cron) chamando `scan_and_ingest`.
5. **Ação HITL** no detalhe (front) → `PATCH /events/{id}/ground-truth` (já existe).

---

## 7. A confirmar com a Tecnogera (fecha o negócio)

- **"Saída" no contrato = o checklist de entrega/liberação?** Se sim, o
  pareamento §3.7 é **checklist↔retorno** (mais simples), não retorno×retorno.
- O operador **tem o `checklist_id` em mãos** ao enviar a foto de retorno?
- A **entrega tem foto do ângulo** relevante para a avaria (senão o casamento
  ângulo↔campo falha e cai em avaliação visual)?
- **Nível de severidade por classe** — mapear `docs/relatorio/severidade.md` às 3 classes.

---

## 8. Definition of ready (validação de negócio)

- [ ] Bug do ingest (`_` folders) corrigido
- [ ] Chave Anthropic válida configurada
- [ ] `process_event` puxa a base de entrega pelo `checklist_id`
- [ ] Ingestão automática agendada
- [ ] Tela: banner de veredito + comparação entrega|retorno + origem da base
- [ ] Tela: ação confirmar/corrigir gravando ground-truth
- [ ] Estado "Sem base" tratado na lista e no detalhe
- [ ] Fluxo validado ponta a ponta com fotos reais + 1 checklist real
