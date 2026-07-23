# Escala de Severidade — Não Conformidades

> Versão: v0.1 — rascunho para validação com a Tecnogera.

A severidade é atribuída pelo Modelo 3 com base em (a) classificação do
Modelo 1 (foto correta?) + (b) score do Modelo 2 (qualidade da evidência) +
(c) interpretação visual feita pela própria Vision API do Modelo 3.

## Tabela de severidades

| Nível | Rótulo | Significado | Ação esperada | Bloqueia liberação? |
|-------|--------|-------------|---------------|----------------------|
| 1 | **Crítica** | Risco imediato à segurança, integridade do equipamento ou ao operador | Equipamento **não pode** ser liberado até correção | Sim |
| 2 | **Alta** | Degrada operação ou indica falha próxima | Corrigir em até 48h ou antes da próxima locação | Em geral, sim |
| 3 | **Média** | Não conformidade relevante, sem risco imediato | Programar manutenção corretiva no próximo ciclo | Não |
| 4 | **Baixa** | Desgaste cosmético, leve fora de padrão | Anotar para inspeção futura | Não |
| 5 | **Info** | Observação de contexto, não é não-conformidade | Apenas registrar | Não |

## Exemplos por faixa

### Crítica
- Vazamento de combustível ou óleo visível (poça, escorrimento contínuo).
- Conexão elétrica com isolamento exposto, queimado ou com sinais de arco.
- Estrutura de fixação rompida, parafusos cisalhados.
- Tanque de combustível com deformação severa ou corrosão profunda.
- Sistema de exaustão obstruído ou com furo perto da cabine.
- Bateria estufada, com vazamento de eletrólito.

### Alta
- Nível de óleo, água ou combustível abaixo do mínimo recomendado.
- Filtro de ar com saturação visível ou rompido.
- Desgaste avançado de correias, mangueiras com microfissuras.
- Componente de proteção (tampa, grade) faltando ou solto.
- Cabos de bateria com corrosão moderada nos terminais.
- Etiqueta de calibração / certificação vencida.

### Média
- Vazamento mínimo (gota seca, mancha antiga sem escorrimento).
- Sujeira excessiva impedindo leitura de painel ou etiqueta.
- Pintura com corrosão superficial localizada.
- Pequeno acúmulo de detritos no compartimento.
- Tag de identificação parcialmente legível.

### Baixa
- Arranhões, manchas e desgaste cosmético.
- Limpeza geral em nível básico (pó, terra).
- Marcações antigas que não comprometem leitura.

### Info
- Foto correta e equipamento conforme — registro positivo.
- Indicador no nível esperado.
- Equipamento recém-revisado (etiqueta de manutenção dentro do prazo).

## Casos especiais

### Foto faltante / inválida
Quando o Modelo 1 indica que um campo obrigatório **não tem foto** ou tem
**foto irrecuperável** (Modelo 2 score ≤ 1):
- Severidade atribuída: **Alta** por padrão.
- Justificativa no relatório: "Sem evidência fotográfica — não foi possível
  validar este item."
- Recomendação: refazer a foto antes da liberação.

### Conflito entre modelos
Quando Modelo 1 classifica como correto mas Modelo 2 dá score baixo
(< 2 de 5):
- Severidade: **Média**.
- Observação: "Foto presente porém com qualidade insuficiente para
  inspeção remota confiável."

## Critério de status geral

Sumarizado no resumo executivo do relatório:

| Status geral | Critério |
|--------------|----------|
| **Aprovado** | Zero crítica, zero alta. |
| **Aprovado com ressalvas** | Zero crítica e zero alta; pelo menos uma média. |
| **Reprovado** | Pelo menos uma alta ou crítica. |

> Validar com Tecnogera: faz sentido o limiar "alta também reprova"? Em alguns
> setores, "alta" é apenas observação obrigatória, não bloqueio.
