"""Contrato da inspeção de UMA vista — taxonomia v0.2, ticket ``mvp-c54-c57/08``.

Este módulo é o vocabulário: prompt, schema da tool ``emit_inspecao``, tipos de
saída e a normalização de coerência. Ele **não fala com provedor nenhum** — os
providers em ``llm_provider.py`` importam daqui e só fazem a chamada HTTP. A
separação existe porque a parte que mais erra (interpretar o JSON que o modelo
devolveu) precisa ser testável sem chave e sem rede.

Fonte do texto: ``docs/avarias/taxonomia-v0.md`` §12, validado empiricamente no
ticket 15 (3 chamadas reais, formato aceito, ``tool_choice`` forçado).

Três fatos do domínio estão codificados aqui e quebram implementação ingênua:

* **``c56`` é a face do PAINEL DE COMANDO, não o radiador.** É rotina o técnico
  fotografar com a porta do painel aberta — o prompt proíbe explicitamente
  emitir ``porta_tampa_aberta`` nessa vista.
* **``c57`` é opcional.** O F180 não a emite desde set/2025. O prompt manda
  julgar só a foto recebida e nunca inferir nada sobre vistas ausentes.
* **Uma chamada por vista.** Achado atribuível à vista e falha isolada valem
  mais do que o contexto do equipamento inteiro numa chamada só.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: Nome da tool. `tool_choice` é forçado nela — o modelo não pode responder texto.
TOOL_NAME = "emit_inspecao"

#: Vistas do MVP, em ordem canônica. `c57` é opcional (F180 parou em set/2025).
CAMPOS_VISTA: tuple[str, ...] = ("c54", "c55", "c56", "c57")

#: Rótulo da vista declarada, injetado na mensagem de usuário.
VISTA_DECLARADA: dict[str, str] = {
    "c54": "lateral direita (vista de conjunto)",
    "c55": "lateral esquerda (vista de conjunto)",
    "c56": "frontal — face do painel de comando (vista de conjunto)",
    "c57": "traseira — face do radiador (vista de conjunto)",
}

#: Rótulo curto da vista, para a tela do operador (ticket 09). Separado de
#: ``VISTA_DECLARADA`` de propósito: aquele é prompt (frase inteira, lida pelo
#: modelo), este é rótulo de moldura (lido por humano em duas palavras).
#: ``c56`` diz "(painel)" porque é a face do painel de comando e o técnico
#: costuma fotografar com a porta aberta — rótulo honesto evita o operador achar
#: que a IA se confundiu de vista.
ROTULO_VISTA: dict[str, str] = {
    "c54": "Lateral direita",
    "c55": "Lateral esquerda",
    "c56": "Frontal (painel)",
    "c57": "Traseira",
}

#: Motivo de "não processável" em português de tela. A taxonomia usa
#: ``snake_case``; o operador lê frase.
ROTULO_MOTIVO_NAO_PROCESSAVEL: dict[str, str] = {
    "foto_escura": "Foto escura",
    "foto_estourada": "Contraluz / superexposição",
    "foto_desfocada": "Foto desfocada",
    "enquadramento_insuficiente": "Enquadramento insuficiente",
    "obstrucao": "Obstrução",
    "orientacao_invalida": "Orientação inválida",
}

#: Enum fechado de tipos de defeito (taxonomia v0.2 §6). Os 8 últimos dependem
#: de componente interno; o prompt proíbe inferi-los sem o componente visível.
TIPOS_DEFEITO: tuple[str, ...] = (
    "amassado_deformacao",
    "pintura_danificada",
    "corrosao_ferrugem",
    "porta_tampa_aberta",
    "componente_ausente",
    "fixacao_solta",
    "veneziana_grade_danificada",
    "vazamento_oleo",
    "vazamento_combustivel",
    "vazamento_arrefecimento",
    "mancha_fluido_seca",
    "cabo_isolacao_exposta",
    "conexao_oxidada",
    "bateria_danificada",
    "componente_queimado",
    "led_alarme_aceso",
    "mangueira_solta",
    "escapamento_danificado",
    "plaqueta_ausente",
    "plaqueta_ilegivel",
    "etiqueta_manutencao_ausente",
    "sujeira_grosseira",
    "pneu_chassi_danificado",
)

#: Por que a foto não é julgável (taxonomia v0.2 §8). O modelo é o segundo
#: portão: a validação técnica só barra quadro degenerado — contraluz severo
#: passa no Laplacian e continua inútil (caso real: checklist 278154 `c57`).
MOTIVOS_NAO_PROCESSAVEL: tuple[str, ...] = (
    "foto_escura",
    "foto_estourada",
    "foto_desfocada",
    "enquadramento_insuficiente",
    "obstrucao",
    "orientacao_invalida",
)

CLASSES: tuple[str, ...] = ("ausencia_item", "fora_padrao_visual", "dano_visivel")

#: Rótulo de tela da classe e do tipo de defeito (ticket 10). Existem porque
#: ``classe`` e ``tipo_defeito`` eram os dois únicos campos do laudo sem
#: ``*_rotulo`` no contrato, e o front supria isso trocando ``_`` por espaço
#: ("Amassado deformação"). Com o formulário de correção o front passa a
#: **escolher** uma classe, e uma lista de opções em ``snake_case`` seria pior
#: ainda — o vocabulário do domínio precisa vir do backend, ou passa a viver em
#: dois repositórios e diverge na primeira mudança de taxonomia.
ROTULO_CLASSE: dict[str, str] = {
    "ausencia_item": "Ausência de item",
    "fora_padrao_visual": "Fora do padrão visual",
    "dano_visivel": "Dano visível",
}

ROTULO_TIPO_DEFEITO: dict[str, str] = {
    "amassado_deformacao": "Amassado / deformação",
    "pintura_danificada": "Pintura danificada",
    "corrosao_ferrugem": "Corrosão / ferrugem",
    "porta_tampa_aberta": "Porta ou tampa aberta",
    "componente_ausente": "Componente ausente",
    "fixacao_solta": "Fixação solta",
    "veneziana_grade_danificada": "Veneziana ou grade danificada",
    "vazamento_oleo": "Vazamento de óleo",
    "vazamento_combustivel": "Vazamento de combustível",
    "vazamento_arrefecimento": "Vazamento de arrefecimento",
    "mancha_fluido_seca": "Mancha de fluido seca",
    "cabo_isolacao_exposta": "Cabo com isolação exposta",
    "conexao_oxidada": "Conexão oxidada",
    "bateria_danificada": "Bateria danificada",
    "componente_queimado": "Componente queimado",
    "led_alarme_aceso": "LED de alarme aceso",
    "mangueira_solta": "Mangueira solta",
    "escapamento_danificado": "Escapamento danificado",
    "plaqueta_ausente": "Plaqueta ausente",
    "plaqueta_ilegivel": "Plaqueta ilegível",
    "etiqueta_manutencao_ausente": "Etiqueta de manutenção ausente",
    "sujeira_grosseira": "Sujeira grosseira",
    "pneu_chassi_danificado": "Pneu ou chassi danificado",
}


def _rotulo_fallback(valor: str) -> str:
    """``_`` → espaço, primeira letra maiúscula — o mesmo humanizador que o
    front (`ChecklistDetailPage.tsx`) aplicava antes do ticket
    ``v1-entregavel/02``. Existe para o dia em que a taxonomia ganha um valor
    novo antes do rótulo correspondente: a tela não pode quebrar nem mostrar
    ``snake_case`` cru enquanto ninguém atualiza ``ROTULO_TIPO_DEFEITO``.
    """
    texto = valor.replace("_", " ")
    return texto[:1].upper() + texto[1:] if texto else texto


def rotulo_classe(valor: str | None) -> str | None:
    """Rótulo de tela de ``classe``. Nunca lança exceção: valor fora do mapa
    (taxonomia evoluiu antes do rótulo) cai em ``_rotulo_fallback``, nunca em
    branco e nunca em ``snake_case`` na tela."""
    if not valor:
        return None
    return ROTULO_CLASSE.get(valor) or _rotulo_fallback(valor)


def rotulo_tipo_defeito(valor: str | None) -> str | None:
    """Rótulo de tela de ``tipo_defeito``. Mesmo contrato de ``rotulo_classe``."""
    if not valor:
        return None
    return ROTULO_TIPO_DEFEITO.get(valor) or _rotulo_fallback(valor)


Conformidade = Literal["conforme", "nao_conforme", "nao_processavel"]

#: Mapa ``ValidationReason`` → ``motivo_nao_processavel`` da taxonomia. A
#: validação técnica e o modelo falam vocabulários diferentes; a tela do
#: operador só deve ver um.
MOTIVO_POR_VALIDACAO: dict[str, str] = {
    "foco_inadequado": "foto_desfocada",
    "resolucao_baixa": "enquadramento_insuficiente",
    "formato_invalido": "obstrucao",
    "metadados_ausentes": "obstrucao",
}


SYSTEM_PROMPT_V02 = """\
Você é inspetor de qualidade de grupos motor-geradores (GMG) da Tecnogera, empresa que \
loca esses equipamentos. Você recebe UMA foto de VISTA DE CONJUNTO tirada por técnico em \
campo, com o código do campo Sisloc (cN) e a vista que aquele campo deve mostrar. Emita o \
laudo pela ferramenta emit_inspecao.

## 0. O que você está olhando

As fotos vêm de dois formulários Sisloc, os dois só de gerador:
- F180 (VISITA GMG): equipamento já instalado na obra/cliente ou parado no pátio da
  filial. Chão de terra vermelha, brita, lama, poça d'água, mato; caminhonete, cerca de
  tela, galpão e OUTROS geradores ao redor. Unidade em geral antiga, encardida, com
  marcas de chuva.
- F038 (PRÉ-LOCAÇÃO): equipamento dentro do galpão ou no pátio, antes de sair para
  locação. Luz artificial e flash, piso de concreto, unidade limpa e recém-pintada.
  Costuma ser fotografada perto demais.

O equipamento é uma CABINE ACÚSTICA retangular sobre skid metálico (às vezes sobre
carreta). Ela tem: duas faces LONGAS com portas de acesso, fechos pretos e grelhas de
ventilação; duas faces CURTAS — uma com o painel de comando, a outra com o radiador e a
saída de ar; o código de patrimônio ESTENCILADO em preto na quina (TECG00788,
TBRG000927, ECGO1444...); e a marca "tecnogera" com "0800 772 1601" pintada na lateral.
O escapamento costuma ficar no TETO, não numa face.

Você NÃO recebe foto de referência. O padrão de qualidade é este texto.

## 1. Ordem de julgamento (nesta ordem, sem pular)

1. QUAL equipamento é o assunto? É o que está em primeiro plano, centralizado, e cujo
   patrimônio estencilado aparece. É MUITO comum haver outro gerador encostado ao lado
   ou ao fundo — no pátio eles ficam enfileirados. Tudo que estiver em outra unidade é
   cenário. NUNCA reporte defeito que está em outro equipamento.
2. A foto é PROCESSÁVEL? Se não, pare: processavel=false, motivo preenchido, achados=[].
3. A foto mostra a VISTA DECLARADA? Se mostra outra coisa, marque vista_confere=false e
   descreva em conteudo_observado — mas siga inspecionando o que está visível.
4. Só então procure defeitos. Um defeito só existe se você consegue apontar ONDE ele
   está na imagem.

## 2. O que se espera em cada vista

- lateral_direita (c54) e lateral_esquerda (c55): a face LONGA da cabine, de ponta a
  ponta. Esperado: chapa contínua e pintada em cor uniforme (branco, amarelo, azul,
  bege — varia por unidade), portas de acesso FECHADAS e travadas nos fechos pretos,
  dobradiças e fechos presentes, grelhas e venezianas íntegras com as aletas retas,
  tampa do bocal de abastecimento fechada, patrimônio estencilado na quina, skid reto
  sem deformação.
- frontal (c56): a face CURTA que carrega o PAINEL DE COMANDO — controlador digital,
  botão de emergência cogumelo, disjuntor, visor de vidro, adesivo de risco elétrico.
  Esperado: painel presente e íntegro, visor não trincado, botão de emergência no lugar,
  chapa reta. ATENÇÃO: é rotina o técnico ABRIR a porta do painel para fotografar o
  interior nesta vista. **Painel de comando aberto em c56 NÃO é defeito** e não deve
  gerar achado nenhum.
- traseira (c57), quando existir: a face CURTA oposta, onde ficam a grade do radiador, a
  saída de ar e as conexões de potência. Esperado: grade íntegra e desobstruída, sem
  aleta dobrada, conexões com tampa.

O formulário F180 parou de emitir o campo c57 em setembro/2025. Receber um checklist com
apenas 3 vistas é o caso NORMAL. Você julga somente a foto que recebeu — nunca comente,
penalize ou infira nada sobre vistas que não chegaram.

## 3. O que É defeito

Use SOMENTE os valores do enum tipo_defeito. Em vista de conjunto, a 2–6 m, os que de
fato se enxergam são:

- corrosao_ferrugem: mancha laranja/marrom COM TEXTURA que NASCE de um ponto metálico
  identificável — rebite, parafuso, junta de chapa, aresta do teto, quina, moldura de
  veneziana — e come a borda. Cuidado: escorrido marrom uniforme que desce do teto sobre
  o painel inteiro, sem ponto de origem metálico, é terra escorrida pela chuva; NÃO é
  ferrugem.
- pintura_danificada: descascamento com metal ou primer aparente, retoque de cor
  diferente, bolha de tinta. Risco fino, arranhão superficial e desgaste de quina NÃO
  contam.
- amassado_deformacao: chapa afundada ou ondulada, linha de painel quebrada, aresta do
  teto torcida. Ondulação que só aparece por reflexo do sol não conta.
- veneziana_grade_danificada: aleta amassada, tela rasgada, grade furada ou obstruída.
- porta_tampa_aberta: porta de ACESSO lateral, tampa do bocal ou capô aberto/entreaberto
  em c54 ou c55. NÃO se aplica ao painel de comando de c56 (ver §2).
- componente_ausente: falta algo que a própria estrutura mostra que deveria existir —
  furo de fixação vazio, dobradiça sem porta, fecho arrancado, tampa de bocal faltando,
  grelha sem tela.
- fixacao_solta: chapa ou porta desalinhada e solta, parafuso saliente, fecho
  arrebentado, painel apoiado sem fixação.
- vazamento_oleo / vazamento_combustivel / vazamento_arrefecimento: escorrimento
  BRILHANTE e contínuo descendo pela chapa ou pelo skid, com trilha visível ligando o
  equipamento à mancha. Diga qual fluido só se cor ou local permitirem; na dúvida use
  vazamento_oleo com confianca baixa. Poça de água de chuva no chão ou empoçada na
  bandeja do skid NÃO é vazamento.
- mancha_fluido_seca: mancha escura, fosca, sem brilho e sem escorrimento, no skid ou na
  chapa baixa.
- mangueira_solta: mangueira desconectada, pendurada, rachada ou sem abraçadeira.
- sujeira_grosseira: barro, crosta ou detrito que ESCONDE a superfície a inspecionar.
  Poeira, terra seca e marca de chuva não contam.
- pneu_chassi_danificado: longarina do skid trincada ou torta, engate torto, pneu murcho
  ou cortado quando a unidade é rebocável.
- escapamento_danificado: furo, trinca de solda ou suporte quebrado no silencioso do
  teto, fuligem saindo de ponto indevido.
- cabo_isolacao_exposta: condutor de cobre à vista, capa rachada ou queimada nos cabos
  de potência que saem da caixa de conexão.

Os tipos abaixo pertencem a componentes INTERNOS. Só emita se o componente estiver
realmente visível no quadro (tipicamente em c56 com o painel aberto); caso contrário, a
ausência deles no laudo é o esperado, e nunca os infira:
led_alarme_aceso, conexao_oxidada, bateria_danificada, componente_queimado,
plaqueta_ausente, plaqueta_ilegivel, etiqueta_manutencao_ausente.

## 4. O que NÃO é defeito — nunca reporte

- Outro gerador, carreta, empilhadeira, caminhonete, container ou galpão no quadro.
  Defeito em unidade vizinha NÃO é achado.
- Peça de aço, viga, calço, pallet ou sucata apoiada no chão perto do equipamento. Ela
  não faz parte dele — mesmo encostada nele.
- Sombra de árvore, de telhado ou de poste projetada na chapa; reflexo de flash, brilho
  do sol, contraluz, halo alaranjado de lente.
- Chão de obra: lama, terra vermelha, brita, mato, poça de chuva, água parada na bandeja
  do skid. Você inspeciona o equipamento, não o local.
- Marca de chuva, terra escorrida e encardido distribuídos pela chapa.
- Código de patrimônio estencilado, mesmo desbotado ou parcialmente apagado. É a
  identificação normal da frota, não um adesivo danificado.
- Marca "tecnogera", telefone 0800, adesivo de advertência, selo, QR code, plaqueta de
  ruído.
- A tarja preta com data, hora e GPS gravada no rodapé da foto — é overlay do aplicativo.
- Desgaste leve de tinta em quina ou aresta; risco superficial sem metal aparente.
- Extintor, ferramenta, cabo estendido no chão ao redor.
- Equipamento em cima de carreta, plataforma ou base de concreto — é logística.
- Diferença de cor entre cabine, skid preto e teto.
- Painel de comando com a porta aberta em c56.

## 5. Foto não processável — é falha de EVIDÊNCIA, não defeito

processavel=false quando a foto for:
- foto_escura: subexposta a ponto de não distinguir componente nem cor.
- foto_estourada: contraluz ou superexposição com o assunto em silhueta.
- foto_desfocada: sem nitidez para julgar textura de chapa (comum no F038 de galpão).
- enquadramento_insuficiente: a vista declarada NÃO cabe no quadro. Na prática o erro é
  quase sempre por EXCESSO de proximidade ou ângulo — só uma quina, só um pedaço de
  painel, ou um rasante em que a face vira uma faixa fina. Também vale o oposto:
  equipamento distante a ponto de ocupar menos de um quarto do quadro.
- obstrucao: objeto, mão, pano ou outro equipamento tapando a face a inspecionar.
- orientacao_invalida: girada a ponto de não dar para orientar a cena.

Nesses casos: conformidade="nao_processavel", achados=[], e a observacao diz o que o
técnico precisa refazer. NUNCA invente defeito a partir de foto ruim.

## 6. Regras de emissão

- conformidade="conforme" exige achados=[].
- conformidade="nao_conforme" exige pelo menos um achado.
- Todo achado traz `local` (quadrante e componente) e `observacao` com âncora visual
  concreta: cor, forma, extensão aproximada, ponto de origem. "Há dano", "fora do
  padrão", "aparenta desgaste" são PROIBIDOS.
- confianca abaixo de 0.60 significa "não tenho certeza" — prefira isso a inventar.
- Na dúvida entre reportar e não reportar, NÃO reporte. Falso positivo em série destrói
  a confiança do operador; falso negativo o operador corrige na tela.
- severidade: 1=crítica (bloqueia a locação), 2=alta (corrigir em 48h), 3=média (próximo
  ciclo de manutenção), 4=baixa (cosmético, apenas registrar). Use 1 só para risco real:
  fluido escorrendo do equipamento, condutor exposto, dano estrutural no skid.
"""


def tool_parameters() -> dict[str, Any]:
    """JSON Schema dos argumentos de ``emit_inspecao`` (taxonomia v0.2 §12.1)."""
    return {
        "type": "object",
        "properties": {
            "processavel": {"type": "boolean"},
            "motivo_nao_processavel": {
                "type": ["string", "null"],
                "enum": [*MOTIVOS_NAO_PROCESSAVEL, None],
            },
            "conteudo_observado": {
                "type": "string",
                "description": "1-2 frases: o que a foto REALMENTE mostra.",
            },
            "vista_confere": {
                "type": "boolean",
                "description": "A foto mostra a vista declarada para este campo?",
            },
            "conformidade": {
                "type": "string",
                "enum": ["conforme", "nao_conforme", "nao_processavel"],
            },
            "achados": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "classe": {"type": "string", "enum": list(CLASSES)},
                        "tipo_defeito": {"type": "string", "enum": list(TIPOS_DEFEITO)},
                        "severidade": {"type": "integer", "minimum": 1, "maximum": 4},
                        "local": {
                            "type": "string",
                            "description": "Onde na imagem (quadrante, componente).",
                        },
                        "observacao": {"type": "string"},
                        "confianca": {"type": "number"},
                    },
                    "required": [
                        "classe",
                        "tipo_defeito",
                        "severidade",
                        "local",
                        "observacao",
                        "confianca",
                    ],
                },
            },
        },
        "required": [
            "processavel",
            "conteudo_observado",
            "vista_confere",
            "conformidade",
            "achados",
        ],
    }


def mensagem_usuario(campo: str) -> str:
    """Texto que acompanha a imagem, declarando qual vista se espera."""
    vista = VISTA_DECLARADA.get(campo, "vista de conjunto do gerador")
    return f"Campo Sisloc: {campo}. Vista declarada: {vista}.\nEmita o laudo."


class Achado(BaseModel):
    """Um defeito apontado numa vista. ``local`` é obrigatório de propósito.

    Separar ``local`` da ``observacao`` torna a regra de âncora visual
    verificável por código: achado sem lugar é achado inventado.
    """

    classe: Literal["ausencia_item", "fora_padrao_visual", "dano_visivel"]
    tipo_defeito: str
    severidade: int = Field(ge=1, le=4)
    local: str = ""
    observacao: str = ""
    confianca: float = Field(default=0.0, ge=0.0, le=1.0)


class InspecaoVista(BaseModel):
    """Laudo de uma vista — a unidade que a tela do operador exibe."""

    campo: str
    processavel: bool = True
    motivo_nao_processavel: str | None = None
    conteudo_observado: str = ""
    vista_confere: bool = True
    conformidade: Conformidade = "conforme"
    achados: list[Achado] = Field(default_factory=list)
    model_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def severidade_max(self) -> int | None:
        """Severidade mais crítica da vista — 1 é o pior, ``None`` sem achado."""
        if not self.achados:
            return None
        return min(a.severidade for a in self.achados)

    @property
    def achado_principal(self) -> Achado | None:
        """O achado que representa a vista: mais crítico, desempate por confiança."""
        if not self.achados:
            return None
        return min(self.achados, key=lambda a: (a.severidade, -a.confianca))


def parse_inspecao(raw: dict[str, Any], *, campo: str, model_version: str) -> InspecaoVista:
    """Converte os argumentos da tool em ``InspecaoVista``, já coerentes.

    Function calling forçado garante o *formato*, não a *coerência*: o modelo
    pode dizer ``conformidade="conforme"`` e mandar um achado junto, ou marcar
    ``processavel=false`` e mesmo assim listar defeitos. Deixar isso passar
    contamina o rollup do checklist com achado que a própria saída desmente,
    então a normalização acontece aqui, uma vez, e não em cada consumidor.

    Ordem das regras (a primeira que casa vence):

    1. ``processavel=false`` manda em tudo: nada de achado, conformidade
       ``nao_processavel``, motivo preenchido (``obstrucao`` como último recurso,
       porque um motivo inválido some da tela).
    2. Sem achado não existe ``nao_conforme``.
    3. Com achado não existe ``conforme``.
    """
    achados = [
        Achado(
            classe=a.get("classe", "fora_padrao_visual"),
            tipo_defeito=str(a.get("tipo_defeito", "")),
            severidade=min(4, max(1, int(a.get("severidade", 4)))),
            local=str(a.get("local", "")),
            observacao=str(a.get("observacao", "")),
            confianca=min(1.0, max(0.0, float(a.get("confianca", 0.0)))),
        )
        for a in raw.get("achados") or []
        if str(a.get("tipo_defeito", "")) in TIPOS_DEFEITO
    ]

    processavel = bool(raw.get("processavel", True))
    conformidade = str(raw.get("conformidade", "conforme"))
    motivo = raw.get("motivo_nao_processavel")
    motivo = str(motivo) if motivo else None

    if not processavel or conformidade == "nao_processavel":
        processavel = False
        conformidade = "nao_processavel"
        achados = []
        if motivo not in MOTIVOS_NAO_PROCESSAVEL:
            motivo = "obstrucao"
    else:
        motivo = None
        if conformidade not in ("conforme", "nao_conforme"):
            conformidade = "nao_conforme" if achados else "conforme"
        elif conformidade == "nao_conforme" and not achados:
            conformidade = "conforme"
        elif conformidade == "conforme" and achados:
            conformidade = "nao_conforme"

    return InspecaoVista(
        campo=campo,
        processavel=processavel,
        motivo_nao_processavel=motivo,
        conteudo_observado=str(raw.get("conteudo_observado", "")),
        vista_confere=bool(raw.get("vista_confere", True)),
        conformidade=conformidade,
        achados=achados,
        model_version=model_version,
    )


def inspecao_nao_processavel(campo: str, motivo: str, observacao: str) -> InspecaoVista:
    """Laudo de vista barrada **antes** da IA — não consome chamada nenhuma.

    Usado pela validação técnica: quadro degenerado não merece token.
    """
    return InspecaoVista(
        campo=campo,
        processavel=False,
        motivo_nao_processavel=(
            motivo if motivo in MOTIVOS_NAO_PROCESSAVEL else "obstrucao"
        ),
        conteudo_observado=observacao,
        vista_confere=True,
        conformidade="nao_processavel",
        achados=[],
        model_version="validacao_tecnica",
    )
