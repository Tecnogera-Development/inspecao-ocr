# Relatório de Inspeção — Especificação v0.1

> **Status**: rascunho para revisão com o time Tecnogera (Edelmar / Célio).
> **Card**: IAVS-010.
> **Próxima ação**: validar em reunião e iterar antes da implementação do
> Modelo 3 (IAVS-011).

## Conteúdo desta pasta

| Arquivo | Propósito |
|---------|-----------|
| [`template.md`](template.md) | Estrutura do relatório com placeholders e instruções inline |
| [`golden-sample-276800.md`](golden-sample-276800.md) | Exemplo preenchido para o checklist 276800 (dados ilustrativos) |
| [`severidade.md`](severidade.md) | Escala de severidade de não conformidades |

## Como o template será usado

1. **Modelo 3 (IAVS-011)** recebe a `ChecklistAnalysis` (resultados dos
   Modelos 1 e 2 + metadados) e gera Markdown obedecendo este template.
2. **IAVS-012** converte o Markdown em PDF profissional (logo, cabeçalho,
   numeração).
3. **IAVS-006** publica o PDF no Dropbox em `Relatorios_IA/`.

O `golden-sample-276800.md` será incluído no system prompt do Modelo 3 como
exemplo few-shot, garantindo consistência de formato.

## Pontos para validar com a Tecnogera (reunião de hoje)

Use esta lista como roteiro:

### Conteúdo
- [ ] **Cabeçalho**: os campos cobrem o que vocês precisam? (ID, data, filial,
      equipamento, técnico responsável, número da OS — algo a remover/adicionar?)
- [ ] **Resumo executivo**: o formato (status geral + contagem por severidade)
      é suficiente? Querem indicador go/no-go para liberação do equipamento?
- [ ] **Análise por campo**: nível de detalhe está adequado? Querem ver foto
      original, score de qualidade, e justificativa por campo?
- [ ] **Não conformidades**: a tabela e descrição cobrem o necessário? Falta
      campo de "ação corretiva sugerida" / "prazo recomendado"?
- [ ] **Recomendações**: separar curto prazo / médio prazo / preventivas faz
      sentido?
- [ ] **Conclusão**: precisa de assinatura digital / responsável técnico?

### Severidade
- [ ] As 5 faixas (Crítica / Alta / Média / Baixa / Info) batem com a forma
      como vocês classificam internamente?
- [ ] Os exemplos por faixa correspondem à realidade de campo?
- [ ] Há alguma não-conformidade típica que não se encaixa nas faixas?

### Formato e identidade visual
- [ ] Logo Tecnogera deve aparecer no PDF? Algum modelo de cabeçalho/rodapé
      padrão existe?
- [ ] Idioma do relatório (PT-BR), tom (técnico-formal) está OK?
- [ ] Tamanho-alvo: 3-6 páginas. OK ou prefere mais sintético?

### Distribuição
- [ ] Quem recebe o PDF? (cliente final, time interno, ambos?) Isso muda o tom?
- [ ] Há requisito legal/normativo (NR, ISO, ABNT) que o relatório deva
      referenciar?

## Definition of Done deste card

- [x] Template Markdown criado com todas as seções essenciais.
- [x] Golden sample preenchido com dados realistas para 1 checklist.
- [x] Escala de severidade documentada com exemplos.
- [ ] **Pendente**: validação com Edelmar/Célio na reunião → registrar
      ajustes em ADR ou em nova versão deste doc.

## Histórico

- **v0.1** (2026-05-06) — primeira versão para revisão.
