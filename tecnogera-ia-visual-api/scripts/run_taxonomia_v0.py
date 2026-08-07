"""Roda a taxonomia v0.2 (tickets 06 + 15) contra imagens reais de c54-c56.

Reancorado no F180/F038 (vistas de conjunto), não no F013 (close-ups).
Provider: OpenAI gpt-4.1-mini, function calling forçado.

Não toca em nada de produção: prompt + tool são locais a este script.
O wiring da esteira é do ticket 08.

Uso:
    set -a && . ./.env && set +a
    uv run python scripts/run_taxonomia_v0.py saida_v02.json IMG:c54 IMG:c55 ...

Sem argumentos de imagem, roda o trio canônico de validação (3 chamadas).
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image

ROOT = Path("/Users/qrz/Documents/Github/Classified/tecnogera")

# ── prompt v0.2 ──────────────────────────────────────────────────────────────

SYSTEM_V02 = """\
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
- Extintor, ferramenta, calço, cabo estendido ou pallet no chão ao redor.
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

TIPOS = [
    "amassado_deformacao", "pintura_danificada", "corrosao_ferrugem",
    "porta_tampa_aberta", "componente_ausente", "fixacao_solta",
    "veneziana_grade_danificada", "vazamento_oleo", "vazamento_combustivel",
    "vazamento_arrefecimento", "mancha_fluido_seca", "cabo_isolacao_exposta",
    "conexao_oxidada", "bateria_danificada", "componente_queimado",
    "led_alarme_aceso", "mangueira_solta", "escapamento_danificado",
    "plaqueta_ausente", "plaqueta_ilegivel", "etiqueta_manutencao_ausente",
    "sujeira_grosseira", "pneu_chassi_danificado",
]

MOTIVOS = [
    "foto_escura", "foto_estourada", "foto_desfocada",
    "enquadramento_insuficiente", "obstrucao", "orientacao_invalida",
]

PARAMS = {
    "type": "object",
    "properties": {
        "processavel": {"type": "boolean"},
        "motivo_nao_processavel": {
            "type": ["string", "null"],
            "enum": [*MOTIVOS, None],
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
                    "classe": {
                        "type": "string",
                        "enum": ["ausencia_item", "fora_padrao_visual", "dano_visivel"],
                    },
                    "tipo_defeito": {"type": "string", "enum": TIPOS},
                    "severidade": {"type": "integer", "minimum": 1, "maximum": 4},
                    "local": {
                        "type": "string",
                        "description": "Onde na imagem (quadrante, componente).",
                    },
                    "observacao": {"type": "string"},
                    "confianca": {"type": "number"},
                },
                "required": [
                    "classe", "tipo_defeito", "severidade", "local",
                    "observacao", "confianca",
                ],
            },
        },
    },
    "required": [
        "processavel", "conteudo_observado", "vista_confere",
        "conformidade", "achados",
    ],
}

TOOL = {
    "type": "function",
    "function": {
        "name": "emit_inspecao",
        "description": "Emite o laudo de inspeção visual de uma foto de gerador.",
        "parameters": PARAMS,
    },
}

VISTA = {
    "c54": "lateral direita (vista de conjunto)",
    "c55": "lateral esquerda (vista de conjunto)",
    "c56": "frontal — face do painel de comando (vista de conjunto)",
    "c57": "traseira — face do radiador (vista de conjunto)",
}

SCRATCH = Path(
    "/private/tmp/claude-501/-Users-qrz-Documents-Github-Classified-tecnogera"
    "/02e33cfa-8dbb-4aa4-bab2-54f70396fabc/scratchpad/amostra"
)

# Trio canônico: uma imagem por vista obrigatória, cada uma testando uma regra nova.
TRIO = [
    (SCRATCH / "F180_212997_c54.jpg", "c54"),          # ferrugem real vs lama/poça de obra
    (SCRATCH / "F180_213337_c55.jpg", "c55"),          # sombra de árvore + gerador vizinho
    (
        ROOT / "data/checklists/278749"
        / "153667042_checklist_278749_c56_0_15_04_2026 16_48_01.jpeg",
        "c56",
    ),                                                  # painel de comando aberto = normal
]


def resize(b: bytes) -> bytes:
    with Image.open(io.BytesIO(b)) as im:
        im.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        if im.mode != "RGB":
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()


def main() -> None:
    import openai as sdk

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("saida_v02.json")
    if len(sys.argv) > 2:
        targets = [(Path(a.rsplit(":", 1)[0]), a.rsplit(":", 1)[1]) for a in sys.argv[2:]]
    else:
        targets = TRIO

    client = sdk.OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=1)
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

    results = []
    for path, field in targets:
        data = base64.standard_b64encode(resize(path.read_bytes())).decode()
        completion = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_V02},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Campo Sisloc: {field}. "
                                f"Vista declarada: {VISTA[field]}.\nEmita o laudo."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{data}"},
                        },
                    ],
                },
            ],
            tools=[TOOL],
            tool_choice={"type": "function", "function": {"name": "emit_inspecao"}},
        )
        call = completion.choices[0].message.tool_calls[0]
        usage = completion.usage
        row = {
            "arquivo": path.name,
            "campo": field,
            "modelo": model,
            "in_tok": usage.prompt_tokens,
            "out_tok": usage.completion_tokens,
            "laudo": json.loads(call.function.arguments),
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n{len(results)} imagens -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
