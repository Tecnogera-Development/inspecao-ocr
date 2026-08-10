# Taxonomia v0.2 de defeitos por vista — MVP c54–c57

> **Versão**: v0.2 — **reancorada no F180/F038** em 2026-08-02 (ticket 15).
> A v0 (ticket 06) foi escrita quando o formulário-alvo presumido era o F013 e as imagens
> locais eram quase todas dele. O ticket 04 provou por censo que o alvo é o
> **`F180-VISITA GMG_REV04`** + **`F038 - PRÉ LOCAÇÃO DE GERADOR`**, onde `c54`/`c55` são as
> duas laterais longas da cabine e `c56` a face frontal. **Esta versão troca o eixo do
> documento: o objeto de inspeção é a VISTA DE CONJUNTO.**
> **Premissa do escopo fechado**: não existe imagem de referência (gabarito). O padrão de
> qualidade vive inteiramente neste texto e no prompt da §11.

---

## 1. Sumário executivo

1. **O objeto do MVP é a cabine acústica vista de fora, inteira.** Nas imagens reais de
   F180 e F038, `c54` e `c55` são as duas faces longas, `c56` é a face curta que carrega o
   **painel de comando** e `c57` (quando existe) é a face curta oposta, do radiador. O plano
   de "vistas de detalhe" da v0 — plaqueta ISO 8528 e carregador de bateria — é do **F013**,
   que está fora da whitelist do MVP. Ele **sai do corpo do documento** e vira o Anexo A,
   marcado como fora do MVP (§10).
2. **O catálogo de 23 tipos de defeito, os 13 critérios de exclusão e o tratamento de foto
   não processável continuam válidos.** O que muda é **a que vista cada coisa se aplica**
   (§5) e **de onde vem o falso positivo** (§7).
3. **Só 15 dos 23 tipos são observáveis numa foto de conjunto.** Os outros 8 dependem de
   componente interno (plaqueta do alternador, bateria, borne, carregador) que a foto de
   conjunto não mostra. Eles ficam no enum, mas o prompt proíbe inferi-los. Mapa completo na
   §5.
4. **A foto de conjunto tem falso positivo de origem diferente da que a v0 assumiu.** A v0
   foi calibrada contra close-ups de oficina: reflexo de flash, poeira, graxa. O campo real é
   outro: **sombra de árvore atravessando a chapa, outro gerador encostado no quadro, lama e
   poça de obra, terra escorrida que imita ferrugem, e patrimônio estencilado desbotado.**
   Quatro exclusões novas (§7).
5. **`c57` ausente é o caso NORMAL, não erro.** O F180 parou de emitir o campo em set/2025;
   o filtro do MVP já o tornou opcional. Prompt e schema tratam 3 vistas sem penalizar o
   checklist (§9).
6. **A validação empírica rodou** — 3 chamadas ao `gpt-4.1-mini`, uma por vista obrigatória.
   As duas regras novas mais arriscadas passaram; um falso positivo e um falso negativo
   apareceram. Detalhe, saída crua e custo medido na §12.

---

## 2. Base de evidência

Duas fontes, ambas de formulário confirmado pela view `dbo.checklist_produto` (ticket 04):

| Origem | Checklists | Formulário | Campos olhados |
|---|---|---|---|
| `data/checklists/278749/` (disco) | 278749 | **F180** | c54, c55, c56 |
| Amostra do ticket 04 (Dropbox, leitura) | 213337, 212997 | **F180** | c54, c55, c56, c57 |
| Amostra do ticket 04 (Dropbox, leitura) | 310493, 310149 | **F038** | c54, c55, c56, c57 |
| `data/checklists/` (7 checklists) | 267699, 269762, 276800, 277861, 278139, 278154, 278365 | F013 | c55, c57 — **fora do MVP**, ver Anexo A |

**Nenhum download novo foi feito neste ticket**: a amostra F180/F038 do ticket 04 já estava
em disco. Dropbox permaneceu em leitura zero.

### 2.1 As duas populações visuais do MVP

O MVP recebe imagens de dois contextos bem diferentes, e a calibragem precisa cobrir os dois:

**F180 — VISITA GMG (a maioria, ~371 ckl/mês projetados).** Equipamento **já instalado na
obra ou parado no pátio da filial**. Chão de terra vermelha, brita, lama, poça d'água, mato
crescendo na base. Ao redor: cerca de tela, caminhonete da Tecnogera, container, e com muita
frequência **outro gerador encostado**. Unidade em geral antiga: encardida, com escorrido de
chuva, aresta do teto marcada. Luz natural dura, sol a pino ou sombra de árvore recortada.

**F038 — PRÉ-LOCAÇÃO (~10 ckl/mês).** Equipamento **dentro do galpão** ou no pátio, antes de
sair. Piso de concreto pintado, luz artificial + flash, unidade limpa e recém-pintada. O
problema aqui não é sujeira: é **enquadramento e foco** — o técnico fotografa perto demais e
as imagens saem moles.

### 2.2 O que é o equipamento, do ponto de vista da foto

Cabine acústica retangular sobre skid metálico preto (às vezes sobre carreta ou base de
concreto). Tem:

- **Duas faces longas** (`c54`, `c55`): portas de acesso com fechos pretos retangulares,
  grelhas/venezianas de ventilação, marca `tecnogera` e `0800 772 1601` pintados numa delas.
- **Duas faces curtas**: uma com o **painel de comando** (`c56`), outra com o **radiador e a
  saída de ar** (`c57`).
- **Código de patrimônio ESTENCILADO** em preto na quina, na vertical: `TECG007883`,
  `TBRG000927`, `ECGO1444`. É o único identificador legível numa foto de conjunto.
- **Escapamento no TETO**, não numa face — o silencioso aparece recortado contra o céu em
  `c54`/`c56`, não como componente de uma "vista traseira".
- Todas as fotos trazem a **tarja preta de data/hora/GPS** gravada no rodapé pelo app.

### 2.3 Características transversais que calibram o prompt

- **Celular, 720×1280 / 960×1280 / 1280×960.** Retrato e paisagem misturados.
- **Distância real: 1,5 a 6 m.** O equipamento ocupa de 40% a 90% do quadro. O erro de
  enquadramento predominante é por **proximidade e ângulo**, não por distância (§8).
- **Outro gerador no quadro em 4 das 12 fotos de conjunto olhadas** — em 310149 `c57` metade
  do quadro é uma unidade vizinha, e ela tem ferrugem visível que **não é do assunto**.
- **Sombra recortada de árvore/telhado sobre a chapa** em 213337 `c55` — desenha manchas
  escuras que lêem como sujeira ou amassado.
- **Escorrido marrom descendo do teto** em 212997 `c54`/`c55` — parte é oxidação real da
  aresta, parte é terra levada pela chuva. São visualmente parecidos.
- **Água parada na bandeja do skid** em 212997 — chuva, não vazamento.
- Uma foto girada 90° (310493 `c55`) e três moles/desfocadas (310493 `c54`, `c55`, `c57`).

---

## 3. O que cada campo é, de fato

Confirmado pela tabela da reunião **e** pela leitura visual de 213337, 212997, 278749 (F180)
e 310493, 310149 (F038):

| Campo | Vista | Confirmação visual | No filtro |
|---|---|---|---|
| `c54` | **Lateral direita** — face longa | 213337, 212997, 310149, 278749 | obrigatório |
| `c55` | **Lateral esquerda** — face longa oposta | 213337, 212997, 310149, 278749 | obrigatório |
| `c56` | **Frontal** — face curta do **painel de comando** | 213337, 212997, 310149, 278749 | obrigatório |
| `c57` | **Traseira** — face curta do radiador | 213337, 212997, 310149, 310493 | **opcional** (§9) |

**Correção material sobre a v0 e sobre o mapa.** A v0 descreveu `frontal` como "a face onde
ficam o radiador, a grade de entrada de ar e o ponto de içamento". **Está trocado.** Nas
amostras, a face de `c56` é a que carrega o **painel de comando** — controlador digital DSE,
botão de emergência cogumelo, disjuntor, visor de vidro, adesivo de risco elétrico (278749,
310149). O radiador fica na face de `c57`.

**Consequência prática de alto impacto**: em `c56` **é rotina o técnico abrir a porta do
painel para fotografar o interior** (278749). Pela regra da v0 — `porta_tampa_aberta` é
desvio em foto de conjunto — o MVP acusaria não conformidade em boa parte dos `c56` que
chegarem. Corrigido no prompt e na §7.

O `278749 c56` não é, portanto, "outro campo" nem divergência de vista: é a face frontal
fotografada com o painel aberto. A leitura da v0 (§3 antiga) de que seria uma vista de
detalhe está **superada**.

---

## 4. Taxonomia — vistas de conjunto

Uma foto por face, tirada a 1,5–6 m, com o equipamento ocupando a maior parte do quadro.

### 4.1 `c54` lateral direita e `c55` lateral esquerda

**Esperado ver**: a face longa da cabine, de ponta a ponta, com o equipamento ocupando pelo
menos metade do quadro.

- Chapa contínua, pintada em cor uniforme (branco, amarelo, azul, bege — varia por unidade),
  sem ondulação nem afundamento.
- **Portas de acesso fechadas e travadas**; fechos pretos e dobradiças presentes.
- Grelhas e venezianas de ventilação íntegras, tela sem rasgo, aletas retas.
- Tampa do bocal de abastecimento fechada.
- Marca `tecnogera` / `0800 772 1601` e adesivos de advertência presentes.
- **Patrimônio estencilado legível na quina** — desbotado é normal (§7).
- Skid reto, sem longarina torta ou trincada; se sobre carreta, calços no lugar.

**Caracteriza desvio**: amassado ou ondulação na chapa · pintura descascada com metal ou
primer aparente · retoque de cor diferente · corrosão nascendo de rebite, junta, quina ou
aresta do teto · porta de acesso aberta ou entreaberta · fecho ou dobradiça arrancada ·
veneziana amassada, tela rasgada · furo de fixação vazio · escorrimento brilhante de óleo
descendo pela chapa ou pelo skid · mangueira desconectada pendurada na base · barro ou crosta
que esconde a superfície · longarina do skid trincada ou torta.

### 4.2 `c56` frontal — a face do painel de comando

**Esperado ver**: a face curta onde fica o painel de comando: controlador digital, botão de
emergência cogumelo, disjuntor, visor de vidro, plaqueta do motor e adesivo de risco
elétrico. **Com muita frequência a porta do painel estará aberta**, mostrando o interior — é
o técnico documentando o equipamento, e é o comportamento esperado nesta vista.

- Painel presente e íntegro; visor sem trinca; botão de emergência no lugar.
- Chapa da face reta, sem afundamento; quinas sem corrosão comendo a borda.
- Adesivos de instrução e de risco elétrico presentes.
- Com o painel aberto: módulos fixos, sem fio pendurado, sem marca de fuligem.

**Caracteriza desvio**: visor trincado ou faltando · botão de emergência ausente ou
arrebentado · chapa amassada · corrosão na moldura do painel · adesivo de risco elétrico
arrancado · com o painel aberto, e **só então**, os itens internos (LED de falha aceso, fio
solto, borne oxidado, marca de queimado).

**Não caracteriza desvio**: a porta do painel aberta. Nunca.

### 4.3 `c57` traseira — a face do radiador (opcional)

**Esperado ver**: a face curta oposta, com a grade do radiador / saída de ar quente e as
conexões de potência.

- Grade do radiador íntegra, sem aleta dobrada, desobstruída.
- Conexões de potência protegidas por tampa ou caixa fechada; cabos sem condutor exposto.
- Chão sob o equipamento sem escorrimento **ligado ao equipamento** (poça de chuva não conta).
- Estrutura reta; se rebocável, engate íntegro e pneus com desenho.

**Caracteriza desvio**: grade amassada, furada ou obstruída · vazamento de arrefecimento
(verde/laranja) escorrendo pelo radiador · caixa de conexão aberta ou sem tampa · cabo com
capa rachada ou cobre à vista · trinca em solda de estrutura · pneu murcho ou cortado.

**Nota de enquadramento**: as duas amostras de `c57` de F180 que temos (213337, 212997) são
**quinas fotografadas de perto**, não a face inteira, e 310149 `c57` mostra metade do quadro
tomada por outro gerador. Se o `c57` voltar a ser emitido, esperar taxa alta de
`enquadramento_insuficiente`.

---

## 5. Mapeamento `tipo_defeito` → vista (novo na v0.2)

O que a foto de conjunto **de fato mostra**. `✔` = observável e esperado; `~` = observável só
em condição específica; `✘` = **não observável** — o prompt proíbe emitir.

| `tipo_defeito` | `c54`/`c55` lateral | `c56` frontal | `c57` traseira | Nota |
|---|:--:|:--:|:--:|---|
| `corrosao_ferrugem` | ✔ | ✔ | ✔ | O achado real mais frequente. Exige ponto de origem metálico (§7.3) |
| `pintura_danificada` | ✔ | ✔ | ✔ | Segundo mais frequente |
| `amassado_deformacao` | ✔ | ✔ | ✔ | Cuidado com ondulação por reflexo do sol |
| `veneziana_grade_danificada` | ✔ | ✘ | ✔ | Grelhas ficam nas laterais; grade do radiador em `c57` |
| `porta_tampa_aberta` | ✔ | **✘** | ~ | **Painel aberto em `c56` NÃO é desvio** (§4.2) |
| `componente_ausente` | ✔ | ✔ | ✔ | Fecho, dobradiça, tampa de bocal, tela de grelha |
| `fixacao_solta` | ✔ | ✔ | ✔ | Chapa/porta desalinhada, parafuso saliente |
| `sujeira_grosseira` | ✔ | ✔ | ✔ | Só quando ESCONDE a superfície |
| `mancha_fluido_seca` | ✔ | ~ | ✔ | Skid e chapa baixa |
| `vazamento_oleo` | ✔ | ~ | ✔ | Exige trilha ligando equipamento à mancha |
| `vazamento_combustivel` | ~ | ✘ | ~ | Só se houver escorrimento do bocal/tanque |
| `vazamento_arrefecimento` | ✘ | ✘ | ✔ | Radiador só aparece em `c57` |
| `pneu_chassi_danificado` | ✔ | ~ | ✔ | Longarina do skid; pneu só em unidade rebocável |
| `mangueira_solta` | ✔ | ~ | ✔ | Visto em 278749 `c54` |
| `escapamento_danificado` | ~ | ~ | ~ | Silencioso fica no TETO, recortado contra o céu |
| `cabo_isolacao_exposta` | ~ | ~ | ✔ | Cabos de potência saem em `c57` |
| `led_alarme_aceso` | ✘ | **~** | ✘ | Só com painel aberto ou visor de vidro |
| `conexao_oxidada` | ✘ | ~ | ~ | Componente interno |
| `bateria_danificada` | ✘ | ~ | ✘ | Componente interno |
| `componente_queimado` | ✘ | ~ | ✘ | Componente interno |
| `plaqueta_ausente` | ✘ | ✘ | ✘ | Plaqueta ISO 8528 fica na carcaça do alternador, dentro |
| `plaqueta_ilegivel` | ✘ | ✘ | ✘ | idem |
| `etiqueta_manutencao_ausente` | ✘ | ✘ | ✘ | idem — é vista de detalhe do F013 (Anexo A) |

**Leitura**: **15 tipos observáveis** em foto de conjunto (✔ em pelo menos uma vista),
**8 restritos ou fora de alcance**. Os 7 marcados só com `~`/`✘` em todas as colunas
(`led_alarme_aceso`, `conexao_oxidada`, `bateria_danificada`, `componente_queimado`,
`plaqueta_ausente`, `plaqueta_ilegivel`, `etiqueta_manutencao_ausente`) **permanecem no
enum** — o vocabulário não muda — mas o prompt instrui: só emita se o componente estiver
literalmente visível no quadro; nunca infira a partir da vista.

**Por que não remover do enum**: `c56` com o painel aberto mostra exatamente esses
componentes, e o mesmo enum atende o Anexo A se a Tecnogera pedir o F013 de volta. Remover
custaria uma migração de vocabulário por um ganho de zero.

---

## 6. Catálogo de tipos de defeito e severidade

Vocabulário fechado — **inalterado desde a v0**, exceto a regra de escalada. Nada fora dele
deve ser emitido.

| `tipo_defeito` | Classe | Severidade padrão | Âncora que o modelo precisa ver |
|---|---|---:|---|
| `vazamento_combustivel` | `dano_visivel` | 1 crítica | Escorrimento contínuo ou poça sob o tanque, com trilha |
| `vazamento_oleo` | `dano_visivel` | 1 crítica | Filete brilhante escorrendo, com trilha até a mancha |
| `cabo_isolacao_exposta` | `dano_visivel` | 1 crítica | Cobre à vista, capa rachada ou queimada |
| `componente_queimado` | `dano_visivel` | 1 crítica | Fuligem, plástico derretido, marca de arco |
| `bateria_danificada` | `dano_visivel` | 1 crítica | Caixa estufada/trincada, eletrólito escorrendo |
| `vazamento_arrefecimento` | `dano_visivel` | 2 alta | Fluido verde/laranja no radiador ou no chão |
| `pneu_chassi_danificado` | `dano_visivel` | 2 alta | Pneu murcho/cortado, longarina trincada |
| `escapamento_danificado` | `dano_visivel` | 2 alta | Furo, trinca de solda, suporte quebrado |
| `componente_ausente` | `ausencia_item` | 2 alta | Furo de fixação vazio, suporte sem o módulo |
| `led_alarme_aceso` | `fora_padrao_visual` | 2 alta | LED vermelho/âmbar de falha aceso |
| `mangueira_solta` | `dano_visivel` | 2 alta | Mangueira desconectada, pendurada ou rachada |
| `fixacao_solta` | `fora_padrao_visual` | 2 alta | Parafuso saliente, módulo pendurado pelo fio |
| `conexao_oxidada` | `dano_visivel` | 2 alta | Pó branco/verde no terminal |
| `porta_tampa_aberta` | `fora_padrao_visual` | 3 média | Porta de **acesso lateral** aberta — nunca o painel de `c56` |
| `corrosao_ferrugem` | `dano_visivel` | 3 média | Mancha laranja/marrom com textura, nascendo de ponto metálico |
| `amassado_deformacao` | `dano_visivel` | 3 média | Chapa afundada, linha do painel quebrada |
| `veneziana_grade_danificada` | `dano_visivel` | 3 média | Aleta dobrada, tela furada |
| `plaqueta_ausente` | `ausencia_item` | 3 média | Marca de cola/rebite sem plaqueta (Anexo A) |
| `plaqueta_ilegivel` | `fora_padrao_visual` | 3 média | Texto ilegível por dano físico (Anexo A) |
| `etiqueta_manutencao_ausente` | `ausencia_item` | 3 média | Vista de plaqueta sem etiqueta nem data (Anexo A) |
| `mancha_fluido_seca` | `fora_padrao_visual` | 3 média | Mancha escura fosca, sem escorrer |
| `sujeira_grosseira` | `fora_padrao_visual` | 3 média | Barro/crosta que **esconde** superfície ou texto |
| `pintura_danificada` | `fora_padrao_visual` | 4 baixa | Descascado com metal/primer aparente, retoque |

**Regra de escalada — REVISADA na v0.2.** Severidade sobe um nível quando o mesmo tipo
aparece em mais de um ponto da mesma foto, ou quando o defeito toca componente de segurança
(tanque, escape, condutor energizado).

> **A regra da v0 "sobe para 1 quando há fluido no chão" está REVOGADA para vista de
> conjunto.** No F180 o equipamento está em obra, sobre lama, e a bandeja do skid acumula
> água de chuva (212997). "Fluido no chão" é o estado normal do pátio e produziria severidade
> crítica em série. **A escalada para 1 exige agora**: mancha escura/iridescente **com trilha
> visível ligando o equipamento à mancha**. Sem a trilha, é chão molhado — e chão molhado não
> é achado nenhum.

**Nota sobre a escala.** `docs/relatorio/severidade.md` tem 5 níveis (inclui `Info`); o código
(`emit_damage`, `damage_classifier._SEVERITY_LABEL`) tem 4 (1–4). `Info` não é severidade, é o
estado "conforme". A v0.2 mantém 4 níveis e trata `Info` como `no_conformity=false`.
**Pauta para a Tecnogera** — item 13.6.

---

## 7. Critérios de exclusão — o que NÃO é defeito

Sem esta lista o modelo produz falso positivo em série. **A v0 foi calibrada contra close-ups
de oficina; a foto de conjunto em campo tem outras armadilhas.** Os itens 1–13 vêm da v0; os
itens 14–17 são novos e vêm da leitura das imagens F180/F038.

1. **Poeira fina, terra seca ou marca de chuva** distribuída pela superfície. Só vira
   `sujeira_grosseira` se esconder o que se ia inspecionar.
2. **Reflexo de flash, brilho especular, sombra dura, contraste de sol, halo alaranjado de
   lente** (visto em 310493 `c55`).
3. **A tarja preta de data/GPS no rodapé.** É overlay do app.
4. **Fita branca ou adesivo com data manuscrita.** Registro de manutenção da Tecnogera.
5. **Selo de qualidade do fabricante, QR code, código de barras, etiqueta de advertência,
   plaqueta de nível de ruído.**
6. **Desgaste leve de tinta em quina ou aresta**; risco superficial sem metal aparente.
7. **Marca de dedo, graxa em maçaneta ou trinco, respingo isolado.**
8. **Sujeira do chão, do pátio, da carreta ou do galpão.** Inspeciona-se o equipamento, não
   o local.
9. **Ferramentas, caixas de peças, pessoas, veículos, céu, vegetação, cerca, container,
   empilhadeira** no enquadramento. Inclui o **extintor apoiado no chão** de 213337 `c56`.
10. **Chicote farto com muitas etiquetas de fio**, desde que preso por abraçadeira (só se
    aplica a `c56` com painel aberto).
11. **Diferença de cor entre cabine, skid preto e teto.** Cada unidade é pintada de um jeito.
12. **Painel de comando com a porta aberta em `c56`** — o técnico abriu para fotografar
    (278749). Este item **muda de escopo** na v0.2: na v0 valia para "foto de detalhe de
    painel"; agora vale para a vista frontal de conjunto, que é onde o caso realmente ocorre.
13. **Equipamento em cima de carreta, plataforma ou base de concreto.** É logística.

**Novos na v0.2, todos ancorados em imagem real:**

14. **Outro gerador no quadro.** No pátio as unidades ficam enfileiradas e encostadas; em
    310149 `c57` metade do quadro é uma unidade vizinha **com ferrugem visível**, e em
    213337 `c55` há uma segunda cabine ao fundo. **Defeito que está em outra unidade não é
    achado.** O assunto é o equipamento em primeiro plano, centralizado, cujo patrimônio
    estencilado aparece. Esta é a exclusão de maior valor da v0.2.
15. **Sombra recortada de árvore, telhado ou poste projetada na chapa.** Em 213337 `c55` a
    sombra desenha manchas escuras de contorno duro que lêem como sujeira grossa ou
    afundamento. Sombra não tem textura nem borda metálica.
16. **Lama, terra vermelha, brita, mato, poça de chuva no chão, e água parada na bandeja do
    skid** (212997). É o piso da obra e o clima, não o equipamento. Ver a regra de escalada
    revisada na §6.
17. **Código de patrimônio estencilado desbotado, gasto ou parcialmente apagado**
    (`TECG007883`, `TBRG000927`, `ECGO1444`). É a identificação normal da frota, aplicada com
    estêncil e spray; desgaste é esperado e **não é** `plaqueta_ilegivel` nem adesivo
    danificado.

### 7.3 Ferrugem × terra escorrida — a distinção que mais importa

As duas coisas produzem listras marrons descendo pela chapa branca, e o F180 é cheio das
duas (212997 `c54`/`c55`). Regra operacional:

- **É `corrosao_ferrugem`** quando a mancha **nasce de um ponto metálico identificável** —
  rebite, parafuso, junta de chapa, aresta do teto, quina, moldura de veneziana — tem
  **textura** e a borda do metal aparece comida.
- **NÃO é** quando o escorrido é uniforme, vem do teto cobrindo painéis inteiros, sem ponto
  de origem metálico e sem textura. Isso é terra levada pela chuva.

**Regra guarda-chuva**: na dúvida entre reportar e não reportar, **não reporte**. Um falso
positivo custa a confiança do operador; um falso negativo é corrigido pelo HITL.

---

## 8. Foto não processável — é falha de evidência, não defeito

Uma foto ruim **não gera achado**; ela gera pedido de refoto.

`processavel = false` quando a foto for:

| `motivo_nao_processavel` | Gatilho | Frequência esperada |
|---|---|---|
| `foto_escura` | Subexposta a ponto de não distinguir componente ou cor | baixa |
| `foto_estourada` | Contraluz/superexposição com o assunto em silhueta | baixa |
| `foto_desfocada` | Sem nitidez para julgar textura de chapa — **três das quatro fotos de 310493 (F038, galpão) são moles** | **alta no F038** |
| `enquadramento_insuficiente` | **A vista declarada não cabe no quadro** — ver revisão abaixo | **alta no `c57`** |
| `obstrucao` | Objeto, mão, pano ou outro equipamento tapando a face | média |
| `orientacao_invalida` | Girada a ponto de impedir orientar a cena — **310493 `c55` está a 90°** | baixa |

**`enquadramento_insuficiente` — gatilho REVISADO na v0.2.** A v0 definiu o gatilho como
"equipamento < 1/4 do quadro". Nas fotos de conjunto reais **o erro é quase sempre o
oposto**: proximidade e ângulo. Os três padrões observados:

1. **Só a quina** — 213337 `c57` e 212997 `c57`: a face inteira não aparece, só o encontro de
   duas chapas.
2. **Só um pedaço de painel** — 310493 `c54`: um fragmento de porta preenche o quadro, sem
   referência da face.
3. **Rasante extremo** — 278749 `c54`: a lateral vira uma faixa fina na borda, e céu +
   estacionamento tomam ~60% do enquadramento.

O gatilho por distância excessiva continua válido, mas é raro. O critério unificado é: **a
face declarada precisa aparecer de ponta a ponta e ser julgável.**

Consequências obrigatórias: `conformidade = "nao_processavel"`, `achados = []`, e a
`observacao` diz **o que o técnico precisa refazer**. É proibido inferir defeito de foto ruim.

**Interação com o que já existe.** A `EventValidationService.validate_technical()` já barra
formato, resolução mínima (640×480) e nitidez Laplacian. Isso **não basta**: um rasante bem
focado passa no Laplacian e continua inútil. O julgamento de processabilidade tem de existir
também no modelo. São dois portões complementares.

**Terceiro estado, separado dos outros dois: `vista_confere = false`.** A foto é boa e o
equipamento pode estar conforme, mas ela não mostra a vista declarada. Com o alvo reancorado
no F180/F038, **a expectativa é que isso dispare pouco** — as quatro vistas batem com a
tabela da reunião. O campo continua existindo como sensor: se `vista_confere=false` subir em
volume, é sinal de que o dicionário de campos está errado para algum formulário. Na v0 ele
era esperado disparar em massa (por causa do F013); na v0.2 ele vira **métrica de alarme**.

---

## 9. `c57` ausente é caso NORMAL — contrato de 3 vistas

O `F180-VISITA GMG_REV04` **parou de emitir `c57` em setembro/2025** (99,65% em ago → 45,08%
em set → 0,48% em out → **0 desde nov/2025**, ticket 01). Os campos `c53`–`c56` seguem
estáveis: foi revisão de formulário, não foto faltando. O filtro do MVP já tornou o `c57`
opcional.

**Regras, para o wiring do ticket 08:**

1. **O prompt nunca menciona "as 4 vistas"** e nunca pede ao modelo para raciocinar sobre
   vistas que não chegaram. A instrução é explícita: *"você julga somente a foto que recebeu;
   nunca comente, penalize ou infira nada sobre vistas que não chegaram"*. Sem isso, o modelo
   tende a citar a vista faltante na `observacao` e a puxar `conformidade` para baixo.
2. **O schema é por imagem** — `emit_inspecao` não tem campo algum que dependa do número de
   vistas. Nada a mudar.
3. **A agregação por checklist** (nível `pipeline_jobs`) registra `vistas_recebidas`
   (ex.: `["c54","c55","c56"]`) como fato informativo. **`c57` ausente não é
   `componente_ausente`, não é `nao_processavel`, não reduz completude e não aparece como
   pendência na tela do operador.** Um checklist de 3 vistas é um checklist completo.
4. **A tela do operador** mostra os slots que existem. Não renderiza um quadro vazio de "vista
   traseira" com aviso — isso treinaria o operador a ver erro onde não há.
5. Se o `c57` **voltar** (uma REV05 pode reintroduzi-lo, ou o F038 continua emitindo), o
   contrato já o aceita: `c57` é opcional, não proibido.

---

## 10. Anexo A — vistas de detalhe (F013) — **FORA DO MVP**

> Esta seção descreve o que a v0 chamava de "vistas de detalhe". Ela existe na realidade, mas
> **pertence ao `F013 - LIBERAÇÃO DE GERADOR`, que não está na whitelist do MVP**
> (`{F180, F038}`). **A esteira do MVP não recebe close-up de plaqueta nem de carregador de
> bateria.** Mantida como registro do trabalho do ticket 06 e como base pronta caso a
> Tecnogera peça o F013 depois. **Não implementar, não wirear no ticket 08.**

No F013, `c55` é sempre um close-up da **plaqueta de dados ISO 8528** do alternador e `c57`
sempre o **carregador de bateria / AVR dentro do painel** — confirmado por censo (0 de 1.840
checklists F013 têm `c54` ou `c56`) e por leitura das 15 imagens locais.

**A.1 Plaqueta / identificação.** Esperado: plaqueta metálica do fabricante (FG Wilson, WEG,
CAT, Stemac) na carcaça do alternador, e/ou etiqueta de serviço Tecnogera, e/ou fita branca
com data manuscrita de megagem. Texto nítido: modelo, nº de série, kVA, tensão, ano.
Desvio: plaqueta ausente com marca de cola/rebite · plaqueta ilegível por **dano físico**
(corroída, pintada por cima, com crosta) · nenhuma etiqueta de serviço nem data · etiqueta com
campos em branco · corrosão na carcaça em volta · sujeira grossa cobrindo o texto.
Tipos aplicáveis: `plaqueta_ausente`, `plaqueta_ilegivel`, `etiqueta_manutencao_ausente`,
`corrosao_ferrugem`, `sujeira_grosseira`.

**A.2 Painel elétrico / carregador de bateria.** Esperado: carregador automático (Murphy
SNTL150P, DSE BC2405, MURR, Reaciona BCH-OPT), AVR, borneiras, disjuntor ou bateria. Módulo
fixo em trilho DIN na posição das setas `MOUNT THIS WAY UP`, bornes com fios inseridos, fiação
com abraçadeira, chapa de fundo sem ferrugem, nenhum LED de falha aceso.
Desvio: ferrugem na chapa ou moldura · óleo/água no fundo do painel · terminal com pó branco
ou verde · fio solto ou puxando o borne · condutor exposto · módulo fora do trilho · fuligem
ou plástico derretido · LED vermelho aceso · bateria estufada/sulfatada · detrito acumulado.
Tipos aplicáveis: `corrosao_ferrugem`, `conexao_oxidada`, `cabo_isolacao_exposta`,
`fixacao_solta`, `componente_queimado`, `led_alarme_aceso`, `bateria_danificada`,
`sujeira_grosseira`, `componente_ausente`.

---

## 11. Veredito sobre as classes atuais

**As 3 classes de não conformidade servem — como camada de agrupamento, e só isso.**

| Classe atual | Veredito | Motivo |
|---|---|---|
| `ausencia_item` | **Mantém** | Crisp, falseável: ou o item está no quadro ou não está |
| `dano_visivel` | **Mantém** | Crisp: dano físico com âncora visual |
| `fora_padrao_visual` | **Mantém sob restrição** | É a classe-problema. Como está no `_DAMAGE_SYSTEM_PROMPT` ("desvio visual do padrão... sujeira excessiva, desgaste, posição errada") é convite ao falso positivo, porque **não existe padrão de referência** neste MVP. Só pode ser alcançada via um `tipo_defeito` do enum da §6 |
| `conforme` | **Mantém** | Não é classe, é o estado `no_conformity = false` |

**O vocabulário muda em três pontos** (inalterado desde a v0):

1. **`tipo_defeito` enumerado** (§6) dentro de cada achado.
2. **`nao_processavel` como saída de primeira classe**, com `motivo_nao_processavel`
   enumerado (§8).
3. **`vista_confere` (bool) + `conteudo_observado` (texto livre)** — na v0.2 o papel dele
   muda de "detector de campo trocado" para **métrica de alarme do dicionário** (§8).

Também recomendado: exigir `local` como campo obrigatório separado da `observacao` — torna a
regra de âncora visual verificável por código.

**O que fica igual**: severidade 1–4, `canonical_angle`, `confidence`.
`matched_reference_index` fica dormente — não há referências neste MVP.

---

## 12. System prompt v0.2 — pronto para colar

> Wiring é do ticket 08. Este bloco não altera `app/services/llm_provider.py`.
> Fonte executável idêntica em `scripts/run_taxonomia_v0.py` (`SYSTEM_V02`).

```text
Você é inspetor de qualidade de grupos motor-geradores (GMG) da Tecnogera, empresa que loca
esses equipamentos. Você recebe UMA foto de VISTA DE CONJUNTO tirada por técnico em campo,
com o código do campo Sisloc (cN) e a vista que aquele campo deve mostrar. Emita o laudo pela
ferramenta emit_inspecao.

## 0. O que você está olhando

As fotos vêm de dois formulários Sisloc, os dois só de gerador:
- F180 (VISITA GMG): equipamento já instalado na obra/cliente ou parado no pátio da filial.
  Chão de terra vermelha, brita, lama, poça d'água, mato; caminhonete, cerca de tela, galpão
  e OUTROS geradores ao redor. Unidade em geral antiga, encardida, com marcas de chuva.
- F038 (PRÉ-LOCAÇÃO): equipamento dentro do galpão ou no pátio, antes de sair para locação.
  Luz artificial e flash, piso de concreto, unidade limpa e recém-pintada. Costuma ser
  fotografada perto demais.

O equipamento é uma CABINE ACÚSTICA retangular sobre skid metálico (às vezes sobre carreta).
Ela tem: duas faces LONGAS com portas de acesso, fechos pretos e grelhas de ventilação; duas
faces CURTAS — uma com o painel de comando, a outra com o radiador e a saída de ar; o código
de patrimônio ESTENCILADO em preto na quina (TECG00788, TBRG000927, ECGO1444...); e a marca
"tecnogera" com "0800 772 1601" pintada na lateral. O escapamento costuma ficar no TETO, não
numa face.

Você NÃO recebe foto de referência. O padrão de qualidade é este texto.

## 1. Ordem de julgamento (nesta ordem, sem pular)

1. QUAL equipamento é o assunto? É o que está em primeiro plano, centralizado, e cujo
   patrimônio estencilado aparece. É MUITO comum haver outro gerador encostado ao lado ou ao
   fundo — no pátio eles ficam enfileirados. Tudo que estiver em outra unidade é cenário.
   NUNCA reporte defeito que está em outro equipamento.
2. A foto é PROCESSÁVEL? Se não, pare: processavel=false, motivo preenchido, achados=[].
3. A foto mostra a VISTA DECLARADA? Se mostra outra coisa, marque vista_confere=false e
   descreva em conteudo_observado — mas siga inspecionando o que está visível.
4. Só então procure defeitos. Um defeito só existe se você consegue apontar ONDE ele está na
   imagem.

## 2. O que se espera em cada vista

- lateral_direita (c54) e lateral_esquerda (c55): a face LONGA da cabine, de ponta a ponta.
  Esperado: chapa contínua e pintada em cor uniforme (branco, amarelo, azul, bege — varia por
  unidade), portas de acesso FECHADAS e travadas nos fechos pretos, dobradiças e fechos
  presentes, grelhas e venezianas íntegras com as aletas retas, tampa do bocal de
  abastecimento fechada, patrimônio estencilado na quina, skid reto sem deformação.
- frontal (c56): a face CURTA que carrega o PAINEL DE COMANDO — controlador digital, botão de
  emergência cogumelo, disjuntor, visor de vidro, adesivo de risco elétrico. Esperado: painel
  presente e íntegro, visor não trincado, botão de emergência no lugar, chapa reta. ATENÇÃO:
  é rotina o técnico ABRIR a porta do painel para fotografar o interior nesta vista. **Painel
  de comando aberto em c56 NÃO é defeito** e não deve gerar achado nenhum.
- traseira (c57), quando existir: a face CURTA oposta, onde ficam a grade do radiador, a saída
  de ar e as conexões de potência. Esperado: grade íntegra e desobstruída, sem aleta dobrada,
  conexões com tampa.

O formulário F180 parou de emitir o campo c57 em setembro/2025. Receber um checklist com
apenas 3 vistas é o caso NORMAL. Você julga somente a foto que recebeu — nunca comente,
penalize ou infira nada sobre vistas que não chegaram.

## 3. O que É defeito

Use SOMENTE os valores do enum tipo_defeito. Em vista de conjunto, a 2–6 m, os que de fato se
enxergam são:

- corrosao_ferrugem: mancha laranja/marrom COM TEXTURA que NASCE de um ponto metálico
  identificável — rebite, parafuso, junta de chapa, aresta do teto, quina, moldura de
  veneziana — e come a borda. Cuidado: escorrido marrom uniforme que desce do teto sobre o
  painel inteiro, sem ponto de origem metálico, é terra escorrida pela chuva; NÃO é ferrugem.
- pintura_danificada: descascamento com metal ou primer aparente, retoque de cor diferente,
  bolha de tinta. Risco fino, arranhão superficial e desgaste de quina NÃO contam.
- amassado_deformacao: chapa afundada ou ondulada, linha de painel quebrada, aresta do teto
  torcida. Ondulação que só aparece por reflexo do sol não conta.
- veneziana_grade_danificada: aleta amassada, tela rasgada, grade furada ou obstruída.
- porta_tampa_aberta: porta de ACESSO lateral, tampa do bocal ou capô aberto/entreaberto em
  c54 ou c55. NÃO se aplica ao painel de comando de c56 (ver §2).
- componente_ausente: falta algo que a própria estrutura mostra que deveria existir — furo de
  fixação vazio, dobradiça sem porta, fecho arrancado, tampa de bocal faltando, grelha sem
  tela.
- fixacao_solta: chapa ou porta desalinhada e solta, parafuso saliente, fecho arrebentado,
  painel apoiado sem fixação.
- vazamento_oleo / vazamento_combustivel / vazamento_arrefecimento: escorrimento BRILHANTE e
  contínuo descendo pela chapa ou pelo skid, com trilha visível ligando o equipamento à
  mancha. Diga qual fluido só se cor ou local permitirem; na dúvida use vazamento_oleo com
  confianca baixa. Poça de água de chuva no chão ou empoçada na bandeja do skid NÃO é
  vazamento.
- mancha_fluido_seca: mancha escura, fosca, sem brilho e sem escorrimento, no skid ou na
  chapa baixa.
- mangueira_solta: mangueira desconectada, pendurada, rachada ou sem abraçadeira.
- sujeira_grosseira: barro, crosta ou detrito que ESCONDE a superfície a inspecionar. Poeira,
  terra seca e marca de chuva não contam.
- pneu_chassi_danificado: longarina do skid trincada ou torta, engate torto, pneu murcho ou
  cortado quando a unidade é rebocável.
- escapamento_danificado: furo, trinca de solda ou suporte quebrado no silencioso do teto,
  fuligem saindo de ponto indevido.
- cabo_isolacao_exposta: condutor de cobre à vista, capa rachada ou queimada nos cabos de
  potência que saem da caixa de conexão.

Os tipos abaixo pertencem a componentes INTERNOS. Só emita se o componente estiver realmente
visível no quadro (tipicamente em c56 com o painel aberto); caso contrário, a ausência deles
no laudo é o esperado, e nunca os infira:
led_alarme_aceso, conexao_oxidada, bateria_danificada, componente_queimado, plaqueta_ausente,
plaqueta_ilegivel, etiqueta_manutencao_ausente.

## 4. O que NÃO é defeito — nunca reporte

- Outro gerador, carreta, empilhadeira, caminhonete, container ou galpão no quadro. Defeito
  em unidade vizinha NÃO é achado.
- Sombra de árvore, de telhado ou de poste projetada na chapa; reflexo de flash, brilho do
  sol, contraluz, halo alaranjado de lente.
- Chão de obra: lama, terra vermelha, brita, mato, poça de chuva, água parada na bandeja do
  skid. Você inspeciona o equipamento, não o local.
- Marca de chuva, terra escorrida e encardido distribuídos pela chapa.
- Código de patrimônio estencilado, mesmo desbotado ou parcialmente apagado. É a identificação
  normal da frota, não um adesivo danificado.
- Marca "tecnogera", telefone 0800, adesivo de advertência, selo, QR code, plaqueta de ruído.
- A tarja preta com data, hora e GPS gravada no rodapé da foto — é overlay do aplicativo.
- Desgaste leve de tinta em quina ou aresta; risco superficial sem metal aparente.
- Extintor, ferramenta, calço, cabo estendido ou pallet no chão ao redor.
- Equipamento em cima de carreta, plataforma ou base de concreto — é logística.
- Diferença de cor entre cabine, skid preto e teto.
- Painel de comando com a porta aberta em c56.

## 5. Foto não processável — é falha de EVIDÊNCIA, não defeito

processavel=false quando a foto for:
- foto_escura: subexposta a ponto de não distinguir componente nem cor.
- foto_estourada: contraluz ou superexposição com o assunto em silhueta.
- foto_desfocada: sem nitidez para julgar textura de chapa (comum no F038 de galpão).
- enquadramento_insuficiente: a vista declarada NÃO cabe no quadro. Na prática o erro é quase
  sempre por EXCESSO de proximidade ou ângulo — só uma quina, só um pedaço de painel, ou um
  rasante em que a face vira uma faixa fina. Também vale o oposto: equipamento distante a
  ponto de ocupar menos de um quarto do quadro.
- obstrucao: objeto, mão, pano ou outro equipamento tapando a face a inspecionar.
- orientacao_invalida: girada a ponto de não dar para orientar a cena.

Nesses casos: conformidade="nao_processavel", achados=[], e a observacao diz o que o técnico
precisa refazer. NUNCA invente defeito a partir de foto ruim.

## 6. Regras de emissão

- conformidade="conforme" exige achados=[].
- conformidade="nao_conforme" exige pelo menos um achado.
- Todo achado traz `local` (quadrante e componente) e `observacao` com âncora visual concreta:
  cor, forma, extensão aproximada, ponto de origem. "Há dano", "fora do padrão", "aparenta
  desgaste" são PROIBIDOS.
- confianca abaixo de 0.60 significa "não tenho certeza" — prefira isso a inventar.
- Na dúvida entre reportar e não reportar, NÃO reporte. Falso positivo em série destrói a
  confiança do operador; falso negativo o operador corrige na tela.
- severidade: 1=crítica (bloqueia a locação), 2=alta (corrigir em 48h), 3=média (próximo ciclo
  de manutenção), 4=baixa (cosmético, apenas registrar). Use 1 só para risco real: fluido
  escorrendo do equipamento, condutor exposto, dano estrutural no skid.
```

### 12.1 Tool `emit_inspecao` v0.2

Superset do `emit_damage` atual. **Inalterado em relação à v0** — nada no schema depende do
número de vistas recebidas (§9.2).

| Campo | Tipo | Nota |
|---|---|---|
| `processavel` | bool | **novo** |
| `motivo_nao_processavel` | enum \| null | **novo** — 6 valores da §8 |
| `conteudo_observado` | string | **novo** — o que a foto realmente mostra |
| `vista_confere` | bool | **novo** — na v0.2 é métrica de alarme (§8) |
| `conformidade` | enum | **novo** — `conforme` / `nao_conforme` / `nao_processavel` |
| `achados[].classe` | enum | igual ao `class_name` atual |
| `achados[].tipo_defeito` | enum | **novo** — 23 valores da §6 |
| `achados[].severidade` | int 1–4 | igual |
| `achados[].local` | string | **novo** — extraído da `observation` |
| `achados[].observacao` | string | igual |
| `achados[].confianca` | float | igual |
| `canonical_angle` | enum | manter para compatibilidade com `Event.angle_class` |

O schema executável está em `scripts/run_taxonomia_v0.py` — script de validação offline,
**não é código de produção** e não é chamado por nada da esteira. Uso:

```bash
set -a && . ./.env && set +a
uv run python scripts/run_taxonomia_v0.py saida_v02.json
# sem argumentos de imagem, roda o trio canônico: 3 chamadas, uma por vista obrigatória
```

---

## 13. Validação empírica — **rodou** (3 chamadas)

Provider **OpenAI `gpt-4.1-mini`** (chave do `.env`, validada). **Orçamento do ticket: 3
chamadas com imagem, uma por vista obrigatória.** Escolhidas para testar as três regras novas
de maior risco, não para amostrar amplo.

| # | Imagem | Campo | O que testa |
|---|---|---|---|
| 1 | `F180_212997_c54` (obra, lama, escorrido marrom) | `c54` | Ferrugem real × terra escorrida; não confundir poça/bandeja com vazamento |
| 2 | `F180_213337_c55` (sombra de árvore, 2ª cabine ao fundo) | `c55` | Exclusão de sombra e de gerador vizinho |
| 3 | `278749 c56` (painel de comando aberto) | `c56` | Painel aberto em `c56` **não** é `porta_tampa_aberta` |

### 13.1 Resultado

| # | `conformidade` | Achados | Veredito |
|---|---|---|---|
| 1 | `nao_conforme` | 1 × `amassado_deformacao` sev. 3 no skid, conf. 0.95 | **parcial** |
| 2 | `conforme` | — | **passou** |
| 3 | `conforme` | — | **passou** |

**Chamada 1 — `c54` de 212997.** Acertos: identificou o patrimônio `TBRG000927`, reconheceu
as portas fechadas, e **não** reportou a lama, a poça nem a água na bandeja do skid, embora
tenha citado as três em `conteudo_observado`. A regra revisada de vazamento (§6) segurou o
falso positivo crítico que a v0 teria produzido. Problemas: (a) **falso positivo** —
`amassado_deformacao` com confiança 0.95 no "skid inferior"; o que está torto no quadro é uma
**viga de aço solta no chão**, na frente do equipamento, não a longarina do skid; (b) **falso
negativo** — não reportou o escorrido de oxidação nas juntas do teto, que pela §7.3 é
`corrosao_ferrugem`.

**Chamada 2 — `c55` de 213337.** `conforme`, e o `conteudo_observado` cita explicitamente
*"várias sombras de árvores sobre a pintura"* sem transformá-las em achado. A exclusão 15
funciona. A cabine vizinha ao fundo também não gerou achado (exclusão 14). Falso negativo
leve: a aresta superior do teto está visivelmente ondulada e os retoques na base da grelha não
foram reportados — coerente com a regra guarda-chuva "na dúvida não reporte", mas é o custo
dela.

**Chamada 3 — `c56` de 278749.** `conforme`, com `conteudo_observado` descrevendo *"a porta do
painel aberta, vendo o controlador digital, botão de emergência e adesivos"* — reconheceu o
painel aberto e **não** emitiu `porta_tampa_aberta`. **Este era o maior risco de regressão da
v0** e a correção da §4.2 resolveu.

Nas três chamadas: `vista_confere=true`, `processavel=true`, saída estruturada válida contra o
schema, `tool_choice` forçado respeitado, zero erro de formato.

### 13.2 Custo medido — primeiro número real do GPT no projeto

| | |
|---|---|
| Tokens de entrada | 12.517 (4.378 + 4.067 + 4.072) → **~4,2k por imagem** |
| Tokens de saída | 292 |
| Custo das 3 chamadas | **≈ US$ 0,006** |
| Custo por imagem | ≈ US$ 0,0018 |
| **Projeção do parque** | ~371 ckl/mês × 3 vistas = ~1.113 imagens → **≈ US$ 2/mês** |

Fecha o "custo do GPT **não verificado**" que estava aberto no ticket 13 e no mapa. Custo de
LLM não é restrição neste MVP — a ordem de grandeza é dois dólares por mês, sem Batch API.

### 13.3 O que fica pendente

- **A calibragem de `corrosao_ferrugem` está frouxa nos dois sentidos**: passou batido no
  escorrido de oxidação real (chamada 1). A regra do "ponto de origem metálico" (§7.3) pode
  estar restritiva demais para um modelo pequeno. Candidato a ajuste no ticket 08, com o
  operador em HITL como medidor.
- **Achado sobre objeto solto no chão** (a viga da chamada 1). A exclusão 9 lista
  "ferramentas, caixas, veículos" mas não cobre sucata estrutural encostada no equipamento.
  Sugestão de emenda para o ticket 08: *"peça de aço, viga, calço ou sucata apoiada no chão
  perto do equipamento não faz parte dele"*.
- **Nenhuma imagem de F038 foi validada por API** (teto de 3 chamadas). O F038 é a população
  com mais foto mole e mais close-up; a taxa de `foto_desfocada` prevista na §8 é **hipótese
  não medida**. Rodar quando houver orçamento — 4 imagens de 310493 bastam.
- **`c57` não foi validado por API** — é opcional e não entra no filtro obrigatório.

---

## 14. Ambiguidades — pauta de validação com a Tecnogera

> Os itens **14.1–14.12** da v0 continuam válidos, com duas baixas: **12.1** (o que é cada
> `cN`) e **12.3** (existe formulário com 4 vistas de conjunto) foram **RESPONDIDOS pelo
> ticket 04** — é o F180/F038, e as vistas são as quatro faces da cabine. O **12.2** (o MVP
> inspeciona o equipamento ou a evidência?) também cai: com o F013 fora, o MVP inspeciona o
> **equipamento**.
>
> Abaixo, as ambiguidades **novas**, levantadas pela leitura das imagens F180/F038. Numeração
> própria, para não colidir com a lista do ticket 14.

**A. Defeito em unidade vizinha, quem responde?**
No pátio os geradores ficam enfileirados e encostados; em 310149 `c57` metade do quadro é
outra unidade, **com ferrugem visível**. Decidimos que só o equipamento em primeiro plano é o
assunto (§7 item 14). Mas: se o técnico fotografa a unidade errada por inteiro, o laudo sai
sobre o gerador errado e ninguém percebe. **Vale cruzar o patrimônio estencilado na foto com
o patrimônio do checklist no Sisloc?** É a única verificação de identidade disponível numa
foto de conjunto, e o estêncil é legível em quase todas.

**B. Qual é o padrão de conservação aceitável para uma unidade em obra?**
O 212997 está em obra, encardido, com escorrido de chuva e oxidação na aresta do teto — e
está trabalhando normalmente. O 310149 saiu do galpão limpo. **São o mesmo critério?** Um
`F180` de visita mede o equipamento em serviço; um `F038` de pré-locação mede o equipamento
pronto para entregar ao cliente. Se o critério é o mesmo, todo F180 de obra vai sair
`nao_conforme`. **Sugestão: severidade calibrada por formulário** — a mesma ferrugem pode ser
"registrar" no F180 e "corrigir antes de entregar" no F038. Precisa da palavra da Tecnogera.

**C. Ferrugem na aresta do teto reprova, ou é envelhecimento esperado?**
É o achado real mais comum da frota (212997, 213337, 310149). Se contar como não conformidade
severidade 3, praticamente toda unidade com mais de alguns anos sai não conforme e o sinal
perde valor. **Precisamos de 3 fotos anotadas**: "isto é envelhecimento normal", "isto é
limítrofe", "isto reprova".

**D. `c56` com o painel aberto: o interior deve ser inspecionado ou ignorado?**
Decidimos que o painel aberto não é defeito. Mas quando ele está aberto, o modelo **vê** o
controlador, os disjuntores e os LEDs. **A Tecnogera quer que a esteira julgue esse interior
quando ele aparece** (LED de falha aceso, fio solto), ou o `c56` é só a face externa e o
interior é assunto de outro campo do formulário? Hoje o prompt permite julgar o que está
visível — é uma escolha nossa, não uma decisão da Tecnogera.

**E. O escapamento fica no teto — quem fotografa o teto?**
Nas quatro vistas laterais/curtas o silencioso só aparece recortado contra o céu. Se
escapamento é item de inspeção do contrato, **nenhuma das vistas c54–c57 o cobre de verdade**.
Existe campo de foto do teto no F180? (Ligado à ambiguidade do dicionário de campos, ticket 14.)

**F. `c57` volta ou não volta?**
O F180 parou de emitir em set/2025 e o nome do formulário na view é `varchar(30)` truncado —
uma REV05 pode estar escondida ali. Tratamos 3 vistas como o caso normal (§9). **Se houver
plano de reintroduzir a vista traseira, queremos saber antes** de a tela do operador ser
desenhada para 3 slots.

**G. Foto de conjunto tirada perto demais: rejeita ou aceita parcial?**
Em 310493 (F038, galpão) três das quatro fotos mostram só um pedaço da chapa. Pela §8 isso é
`enquadramento_insuficiente` → `nao_processavel` → pedido de refoto. **Se a prática do galpão
é essa, o MVP vai rejeitar a maioria dos F038.** Ou aceitamos julgar o pedaço visível, ou a
Tecnogera precisa orientar o técnico a se afastar. Decisão de produto, não técnica.

**H. Água parada na bandeja do skid.**
Excluímos como água de chuva (§7 item 16). Mas a bandeja do skid é justamente a bacia de
contenção de fluidos — **líquido acumulado ali pode ser exatamente o que se quer detectar**.
Distinguir água de chuva de óleo/diesel acumulado por foto de 3 m é frágil. **Preferem falso
negativo (nossa escolha atual) ou uma flag de "verificar bandeja" sem severidade?**

---

## 15. Referências

- `docs/exploracao/dicionario-campos-sisloc.md` — censo por formulário + leitura visual
- `docs/exploracao/survey-c54-c57.md` — incidência das 4 vistas, corte do `c57`
- `docs/relatorio/severidade.md` — escala de severidade v0.1
- `app/services/llm_provider.py` — `_DAMAGE_SYSTEM_PROMPT` e `emit_damage` atuais
- `app/services/damage_classifier.py` — mapeamento severidade → rótulo
- `app/profiles/equipment_profiles.yaml` — perfis por formulário Sisloc
- `scripts/run_taxonomia_v0.py` — prompt v0.2 executável + tool `emit_inspecao`
- `estado-atual.md` §4 — vocabulário do produto
