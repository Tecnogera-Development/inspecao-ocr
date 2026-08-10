# Notas de release — IA Visual v1.2.1-entregável

**Data**: 03/08/2026
**Para**: time de operação Tecnogera (quem usa o portal e quem sobe a stack)
**Sobre**: v1.2-entregável (gerenciamento de usuários) e v1.1-entregável (só F038, PDF, Excel)

Esta é uma entrega pequena de interface **mais** a configuração de produção revisada.
Se você já leu as notas do v1.2, o que muda de código são três itens (1 a 3). **O resto
desta página é sobre subir em produção com a esteira funcionando de verdade — leia a
seção 4, é onde mora o erro caro.**

---

## 1. O menu tem só Checklists e Usuários

Saíram da navegação **Avarias** e **Relatórios**. As telas continuam existindo e
alcançáveis por URL direta — o que saiu foi a oferta no menu, não o código. Se alguém do
time usava `/avarias` no dia a dia, o link continua funcionando; só não está mais no menu.

**Usuários** só aparece para quem tem papel `admin`.

## 2. A foto da vista amplia ao clicar

No relatório do checklist, clicar em qualquer foto abre a imagem em tela cheia.
Fecha com **Esc**, clicando fora da foto, ou no **X**. No grid a foto cabe em pouco
mais de 250px de altura — suficiente para notar que há algo, insuficiente para
decidir se é ferrugem ou sombra.

## 3. A tela inicial passou a ser a fila de Checklists

Ao entrar no portal, o operador cai direto em `/checklists`. Antes caía em `/avarias`.

---

## 4. Para a esteira rodar em produção, DUAS flags precisam estar ligadas

Este é o ponto mais importante desta entrega. São duas variáveis diferentes, e ligar só
uma entrega meio sistema — de um jeito que **não parece erro**:

| Variável | Valor em produção | O que acontece se ficar desligada |
|---|---|---|
| `CHECKLIST_INGEST_ENABLED` | `true` | Nenhum checklist novo entra. O portal fica parado no que já existe |
| `LLM_DISPATCH_ENABLED` | `true` | Os checklists **entram**, mas nunca são analisados: todos ficam com indicador **"sem análise"** e a fila não anda |

O default do código para `LLM_DISPATCH_ENABLED` é **`false`**, de propósito — é um kill
switch para a stack poder subir sem gastar. **Em produção ele precisa ser ligado
explicitamente.** O `.env.production.example` deste pacote já vem com os dois em `true`.

O sintoma de esquecer o segundo é traiçoeiro: o portal enche de checklists, nenhum com
laudo, e parece "a IA não está achando nada" em vez de "a IA não rodou".

### O cron

Roda a cada **30 minutos**, varrendo o Dropbox por delta de cursor. Ele **não reprocessa**
checklist que já tem job — a validação feita por um operador nunca é sobrescrita pelo cron.

Volume esperado: **~71 checklists/mês**, ou cerca de **3 por dia útil** (o corte para F038
foi feito no v1.1). Se o volume ficar perto de zero, aí sim é sintoma de problema.

Custo de LLM medido: **≈US$ 0,50/mês** no parque projetado. Os freios
(`LLM_MAX_CALLS_PER_RUN=60`, `LLM_MONTHLY_BUDGET_USD=25`) são folgados de propósito — não
cortam a operação normal, mas limitam o estrago de um loop.

## 5. Qual IA roda: quem decide é a CHAVE, não o `LLM_PROVIDER`

Armadilha de configuração que vale conhecer antes de mexer no `.env`:

```
OPENAI_API_KEY preenchida   -> OpenAI (gpt-4.1-mini)
senão ANTHROPIC_API_KEY     -> Anthropic
senão                       -> Fake (laudo fictício)
```

**`LLM_PROVIDER=fake` com a chave preenchida NÃO desliga o gasto.** Para rodar sem gastar
(só em teste), zere as duas chaves.

Em produção existe guarda-corpo: com `APP_ENV=production` e nenhuma chave configurada, a
**API e o worker recusam subir**. É deliberado — laudo fictício é indistinguível de laudo
real na tela do operador e no PDF que vai ao cliente com o logo da Tecnogera.

---

## 6. O que veio no v1.2 e continua valendo (resumo)

- **Não existe admin de fábrica.** O primeiro nasce pelo CLI, na VM:
  `python -m app.cli create_user --email <e-mail> --password '<senha>' --role admin`.
  Todos os usuários que já existiam viram **operador** na migration.
- **Criar usuário**: o código de uso único aparece **uma vez só** e expira em **30 minutos**.
  Fechou a tela sem copiar, o caminho é resetar.
- O código é repassado **fora de banda** (WhatsApp, pessoalmente) — não há canal de e-mail.
- **Resetar senha é o mesmo caminho.** Não existe "esqueci minha senha" sem admin.
- **Resetar e inativar derrubam a sessão ativa na hora**, não no próximo login.
- **Rate limiting**: 5 tentativas/15 min por e-mail, 20/15 min por origem, resposta **429**.
  Não trate como bug quando alguém errar a senha cinco vezes.
- Dois papéis: `admin` e `operador`. A única diferença é gerenciar usuários.
- **Migration `0014`** precisa rodar (`alembic upgrade head`).

## 7. O que veio no v1.1 e continua valendo

- **A esteira só processa F038.** F180 saiu da ingestão e do portal (os dados antigos
  continuam no banco, alcançáveis por SQL).
- **Exportar PDF** do laudo e **Exportar Excel** da lista.
- ⚠ O **PDF é documento externo**, com o logo da Tecnogera, e **exporta mesmo com o laudo
  ainda não validado por humano**. A única coisa dizendo que a leitura foi automática é o
  rodapé.
- ⚠ O **PDF não avisa quando o checklist cobre mais de um ativo** (78 casos medidos de
  geradores gêmeos, em que o laudo pode nomear o equipamento errado). Esse aviso existe só
  na tela interna do portal.
