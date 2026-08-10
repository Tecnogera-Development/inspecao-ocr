<!--
TEMPLATE DO RELATÓRIO DE INSPEÇÃO — IA Visual Tecnogera v0.1

Placeholders entre {{ ... }} são preenchidos pelo Modelo 3.
Comentários HTML como este são instruções para o gerador e NÃO devem
aparecer no PDF final.

Convenções:
- Datas no formato DD/MM/AAAA, horários HH:MM (fuso de Brasília).
- Severidades conforme docs/relatorio/severidade.md.
- Tom: técnico-formal, terceira pessoa, sem juízo de valor sobre o operador.
-->

# Relatório de Inspeção Visual — {{equipamento.tipo}}

**Checklist nº {{checklist.id}}** · Filial {{filial.nome}} · {{checklist.data}}

---

## 1. Identificação

| Campo | Valor |
|-------|-------|
| Checklist ID | {{checklist.id}} |
| Data da inspeção | {{checklist.data}} |
| Hora | {{checklist.hora}} |
| Filial | {{filial.nome}} ({{filial.codigo}}) |
| Tipo de equipamento | {{equipamento.tipo}} |
| Modelo / Tag | {{equipamento.modelo}} · {{equipamento.tag}} |
| Técnico responsável | {{tecnico.nome}} |
| Ordem de Serviço | {{os.numero}} |
| Inspeção gerada por | IA Visual Tecnogera v{{sistema.versao}} |

---

## 2. Resumo executivo

**Status geral:** {{resumo.status}}
<!-- Aprovado / Aprovado com ressalvas / Reprovado — ver severidade.md -->

{{resumo.frase_curta}}
<!-- 1-2 frases sumarizando o que foi inspecionado e o veredito. -->

| Severidade | Quantidade |
|------------|-----------:|
| Crítica | {{resumo.contagem.critica}} |
| Alta | {{resumo.contagem.alta}} |
| Média | {{resumo.contagem.media}} |
| Baixa | {{resumo.contagem.baixa}} |
| Info | {{resumo.contagem.info}} |

**Itens inspecionados:** {{resumo.total_itens}}
**Cobertura fotográfica:** {{resumo.cobertura_pct}}% ({{resumo.itens_com_foto}} de {{resumo.itens_obrigatorios}} obrigatórios)

---

## 3. Análise por item

<!--
Repetir o bloco abaixo para cada item inspecionado, na ordem dos campos
obrigatórios definidos em equipment_profiles.yaml (IAVS-009).
-->

### 3.{{item.indice}}. {{item.campo_legivel}} — `{{item.field_name}}`

- **Foto presente:** {{item.foto_presente}}
- **Qualidade da evidência:** {{item.qualidade.label}}
- **Classificação:** {{item.classificacao.resultado}} (confiança {{item.classificacao.confianca_pct}}%)
- **Severidade da observação:** {{item.severidade}}

**Observação:** {{item.observacao}}
<!-- Descrição objetiva do que foi visto. Sem juízo. -->

<!-- Se severidade >= Média, descrever justificativa: -->
**Justificativa técnica:** {{item.justificativa}}

![{{item.campo_legivel}}]({{item.foto_path}})

---

## 4. Inconclusivas

<!--
Listar fotos com confiança entre 0,40 e 0,70 (classificação incerta).
Repetir o bloco abaixo para cada foto inconclusiva.
Substituir a seção pela frase "Nenhuma foto inconclusiva nesta inspeção."
quando não houver fotos nesse intervalo de confiança.
-->

### 4.{{inconc.indice}}. `{{inconc.filename}}`

- **Melhor palpite:** {{inconc.melhor_palpite}}
- **Confiança:** {{inconc.confianca_pct}}%
- **Observação:** {{inconc.observacao}}

![{{inconc.filename}}]({{inconc.foto_path}})

---

## 5. Não conformidades

<!--
Listar todas as não-conformidades de severidade Média ou superior, ordenadas
por gravidade (crítica → média).
-->

| # | Item | Severidade | Descrição | Ação corretiva sugerida | Prazo |
|---|------|------------|-----------|-------------------------|-------|
| {{nc.numero}} | {{nc.item}} | {{nc.severidade}} | {{nc.descricao}} | {{nc.acao}} | {{nc.prazo}} |

> Quando não houver não-conformidades de severidade Média ou superior,
> substituir esta tabela pela frase: *"Nenhuma não conformidade identificada
> nesta inspeção."*

---

## 6. Recomendações

### 6.1 Curto prazo (até a próxima locação)
{{recomendacoes.curto_prazo}}

### 6.2 Médio prazo (próximo ciclo de manutenção)
{{recomendacoes.medio_prazo}}

### 6.3 Preventivas
{{recomendacoes.preventivas}}

---

## 7. Conclusão

{{conclusao.texto}}
<!--
Parágrafo final reafirmando o status, citando o item de maior severidade (se
houver) e a próxima ação esperada. 3-5 linhas.
-->

---

## 8. Limitações da análise

Este relatório foi gerado por sistema automatizado (IA Visual Tecnogera) a
partir das fotos enviadas no checklist nº {{checklist.id}}. As limitações
inerentes são:

- A análise depende exclusivamente das fotos disponíveis; itens não
  fotografados não foram avaliados.
- A qualidade da inspeção remota é proporcional à qualidade da evidência
  fotográfica.
- Achados críticos devem ser confirmados por inspeção presencial antes da
  liberação do equipamento.

---

<small>Relatório gerado automaticamente em {{sistema.gerado_em}} por IA Visual Tecnogera v{{sistema.versao}}.</small>
