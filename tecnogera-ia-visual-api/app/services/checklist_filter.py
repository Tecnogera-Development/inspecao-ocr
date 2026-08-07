"""Filtro **qualificado por formulário** dos checklists de gerador.

Regra fechada em 2026-08-02, estendida pelo
recorte de status e, na v1.1,
(2026-08-03), estreitada para um único formulário::

    formulario ∈ FORMULARIOS_ALVO  E  status = 'Concluído'  E  c54 ∧ c55 ∧ c56
                                                              (c57 OPCIONAL)

``FORMULARIOS_ALVO`` hoje é só ``{F038}`` — o F180 foi o formulário dominante
até a v1 (2026-08-03: pedido explícito da Tecnogera de mostrar só o
F038), mas nunca teve laudo validado por humano, e sai da esteira **e** da
consulta do portal sem apagar a infraestrutura que ele deixa para trás (ver
``VISTAS_ESPERADAS_POR_FORMULARIO``, que continua mapeando as 3 vistas do F180
— dormente, não morto).

A ordem de avaliação é obrigatória e este módulo a impõe: **primeiro** o
formulário, **depois** o status, **depois** os campos. Filtrar por código de
campo solto é erro grave — ``cN`` é código *por formulário*. No F013 o ``c55`` é
a plaqueta de dados do alternador e o ``c57`` é o carregador de bateria; um
filtro por campo solto mandaria plaqueta e carregador para a IA rotulados como
"lateral esquerda" e "traseira". Prova por censo em
``docs/exploracao/dicionario-campos-sisloc.md``.

Fatos medidos que quebram implementação ingênua:

* ``formulario`` é ``varchar(30)`` e vem **truncado** no banco
  (``F066 - CHECKLIST TRANSPORTE EX`` é "…EXPEDIÇÃO"). Casa-se por **prefixo
  ``F0NN``**, nunca por igualdade de string.
* **36%** dos checklists têm ``formulario`` vazio. São descartados, mas contados
  à parte — misturá-los com "formulário errado" esconderia um problema de ERP.
* ``c57`` é **opcional**: o F180 parou de emitir em set/2025. Exigi-la zeraria o
  formulário de maior volume.
* **14,8%** dos F180/F038 estão ``A Executar`` (2.022) ou ``A Conferir`` (1.164)
  — checklists **abertos**, todos com ``data_conclusao`` NULL e fotos
  possivelmente parciais. Exigir ``Concluído`` leva ``data_conclusao`` de 85,2%
  para 100% e o resto para 93–99%, ao custo de uma cláusula. O descarte tem
  motivo **próprio** (``status_nao_concluido``) e **não é terminal**: um
  ``A Conferir`` de hoje fecha amanhã, e tratá-lo como definitivo perderia
  14,8% do volume em silêncio.

Por que ``formulario_ausente`` continua descartando (decisão do ticket 17):
1,10% dos checklists com foto no Dropbox (291 de 26.365, ≈2/mês) **nunca**
aparecem na view, e não é atraso do ERP — o mais recente é de mais de um mês
antes da leitura. Sem formulário não há como afirmar que é gerador, e ``cN`` só
tem significado dentro de um formulário: processar assim aplicaria taxonomia de
gerador a um equipamento possivelmente diferente. Descarta-se, com contador
próprio, e **sem terminalidade** — a linha pode aparecer depois. O caminho de
exceção para o humano que sabe o que está fazendo é ``POST /pipeline/run``, que
não aplica o filtro.

Módulo puro: nenhuma I/O, nenhuma chamada de LLM, nenhum acesso a banco.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

#: Fonte única do corte de produto (decisão de produto,
#: 2026-08-03): só o F038 aparece na esteira e no portal. F277 (plataforma
#: elevatória) já ficava fora por decisão de escopo do MVP — outro
#: equipamento, outra taxonomia. F180 sai aqui: não há literal ``"F038"`` em
#: nenhum outro lugar do repo que decida isso — quem precisar do conjunto
#: alvo importa este nome.
FORMULARIOS_ALVO: frozenset[str] = frozenset({"F038"})

#: As três vistas sem as quais não há inspeção.
CAMPOS_OBRIGATORIOS: tuple[str, ...] = ("c54", "c55", "c56")

#: Traseira — entra na inspeção quando existe, nunca reprova o checklist.
CAMPOS_OPCIONAIS: tuple[str, ...] = ("c57",)

#: Ordem canônica das vistas do MVP.
CAMPOS_ORDEM: tuple[str, ...] = (*CAMPOS_OBRIGATORIOS, *CAMPOS_OPCIONAIS)

#: Quantas vistas cada formulário **espera** emitir hoje. É o que separa
#: "faltou foto" de "este formulário não tem essa foto":
#:
#: * **F180** — 3 vistas. Parou de emitir ``c57`` em set/2025 (ticket 16: a
#:   traseira SAIU do formulário, não migrou de código). Desenhar uma moldura
#:   vazia de traseira num F180 é dizer ao operador que o técnico esqueceu uma
#:   foto que ninguém pediu — ele perde a confiança na tela na primeira semana.
#: * **F038** — 4 vistas. Manteve a ``c57``; ali a ausência é lacuna real.
#:
#: Formulário desconhecido cai nas três obrigatórias, o piso da esteira.
VISTAS_ESPERADAS_POR_FORMULARIO: dict[str, tuple[str, ...]] = {
    "F180": CAMPOS_OBRIGATORIOS,
    "F038": CAMPOS_ORDEM,
}

_RE_PREFIXO = re.compile(r"^\s*(F\d{3})")

#: Único valor de ``status_checklist`` que libera a inspeção. Comparado sem
#: acento e sem caixa: a coluna é ``varchar(10)`` e a acentuação depende do
#: collation do servidor, o que não pode decidir se um checklist é processado.
STATUS_CONCLUIDO = "Concluído"


def _dobrar(texto: str) -> str:
    """Minúsculas, sem acento, sem espaço nas bordas."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).strip().casefold()


_STATUS_CONCLUIDO_DOBRADO = _dobrar(STATUS_CONCLUIDO)


def status_concluido(status: str | None) -> bool:
    """``True`` só para ``Concluído``. ``None``/vazio é **não** concluído.

    Ausência de status não é permissão: ``status_checklist`` está 100%
    preenchido na medição, então um vazio é anomalia — e a anomalia não pode
    gastar chave paga sobre evidência que talvez esteja pela metade.
    """
    if not status or not status.strip():
        return False
    return _dobrar(status) == _STATUS_CONCLUIDO_DOBRADO


class MotivoDescarte(str, Enum):
    """Por que um checklist não virou job. É o contador que a Tecnogera lê."""

    #: Não existe linha em ``dbo.checklist_produto`` para este ``checklist_id``.
    #: 1,10% dos checklists com foto (291 de 26.365, ≈2/mês). Não é atraso do
    #: ERP — mas segue não-terminal: a linha ainda pode aparecer.
    FORMULARIO_AUSENTE = "formulario_ausente"
    #: Linha existe mas a coluna está vazia — 36% do parque.
    FORMULARIO_VAZIO = "formulario_vazio"
    #: Formulário identificado, fora de ``FORMULARIOS_ALVO``. Ex.: F180 (saiu
    #: na v1), F013, F277.
    FORMULARIO_FORA_WHITELIST = "formulario_fora_whitelist"
    #: Formulário certo, checklist ainda **aberto** (``A Executar``/``A Conferir``).
    #: Contador SEPARADO de ``campo_faltante`` de propósito: a ação da Tecnogera
    #: é outra — ali falta uma foto, aqui falta fechar o checklist no ERP.
    STATUS_NAO_CONCLUIDO = "status_nao_concluido"
    #: Formulário certo, checklist fechado, faltam vistas obrigatórias.
    CAMPO_FALTANTE = "campo_faltante"


@dataclass(frozen=True, slots=True)
class Veredito:
    """Resultado da avaliação de um checklist."""

    aprovado: bool
    formulario_codigo: str | None
    motivo: MotivoDescarte | None = None
    campos_faltantes: tuple[str, ...] = ()
    campos_utilizados: tuple[str, ...] = ()
    #: Valor cru de ``status_checklist``, para qualificar o contador.
    status_bruto: str | None = None

    @property
    def rotulo(self) -> str:
        """Chave de contagem para o log estruturado.

        Qualifica o motivo com o detalhe que importa: qual formulário foi
        recusado (``formulario_fora_whitelist:F013``), qual vista faltou
        (``campo_faltante:c55``) ou em que estado o checklist parou
        (``status_nao_concluido:A Conferir``). Sem essa qualificação o contador
        não diz à Tecnogera se o problema é formulário errado, campo não
        fotografado ou checklist que ninguém fechou.
        """
        if self.motivo is None:
            return "aprovado"
        if self.motivo is MotivoDescarte.CAMPO_FALTANTE and self.campos_faltantes:
            return f"{self.motivo.value}:{'+'.join(self.campos_faltantes)}"
        if self.motivo is MotivoDescarte.FORMULARIO_FORA_WHITELIST and self.formulario_codigo:
            return f"{self.motivo.value}:{self.formulario_codigo}"
        if self.motivo is MotivoDescarte.STATUS_NAO_CONCLUIDO and self.status_bruto:
            return f"{self.motivo.value}:{self.status_bruto}"
        return self.motivo.value

    @property
    def terminal(self) -> bool:
        """True quando o desfecho não muda com o tempo.

        Formulário fora da whitelist é definitivo — o formulário de um checklist
        não é reescrito. Os demais **não** são: a foto que falta pode chegar no
        próximo delta, a linha do ERP pode aparecer depois da foto, e um
        ``A Conferir`` vira ``Concluído`` quando alguém confere. Só o terminal
        deixa de ser reavaliado — marcar o status como terminal descartaria
        14,8% do volume em silêncio.
        """
        return self.motivo is MotivoDescarte.FORMULARIO_FORA_WHITELIST


def prefixo_formulario(formulario: str | None) -> str | None:
    """Extrai o código ``F0NN`` do texto truncado do Sisloc.

    Devolve ``None`` para ausente, vazio ou sem prefixo reconhecível.
    """
    if not formulario or not formulario.strip():
        return None
    match = _RE_PREFIXO.match(formulario)
    return match.group(1).upper() if match else None


def vistas_esperadas(
    formulario: str | None,
    recebidas: Collection[str] = (),
) -> tuple[str, ...]:
    """Vistas que **este** checklist deveria ter, em ordem canônica.

    ``formulario`` aceita o texto cru da view ou já o código ``F0NN``.

    A união com ``recebidas`` não é conveniência: um F180 **anterior** a
    set/2025 tem ``c57`` legítima, e o mapa por formulário sozinho a declararia
    inesperada — a foto existiria no laudo sem moldura para exibi-la. Quem
    manda é o que chegou; o mapa só decide o que **falta**.
    """
    codigo = prefixo_formulario(formulario) or (formulario or "").strip().upper()
    base = VISTAS_ESPERADAS_POR_FORMULARIO.get(codigo, CAMPOS_OBRIGATORIOS)
    presentes = normalizar_campos(recebidas)
    return tuple(c for c in CAMPOS_ORDEM if c in base or c in presentes)


def normalizar_campos(campos: Iterable[str]) -> frozenset[str]:
    """Normaliza códigos de campo vindos do nome do arquivo (``C54`` → ``c54``)."""
    return frozenset(c.strip().lower() for c in campos if c and c.strip())


def avaliar(
    formulario: str | None,
    campos: Collection[str],
    *,
    status: str | None = None,
    formularios_alvo: Collection[str] = FORMULARIOS_ALVO,
    obrigatorios: Collection[str] = CAMPOS_OBRIGATORIOS,
    opcionais: Collection[str] = CAMPOS_OPCIONAIS,
    tem_linha_no_erp: bool = True,
    exigir_concluido: bool = True,
) -> Veredito:
    """Aplica a regra na ordem obrigatória: formulário, status, campos.

    ``formulario`` é o texto cru da view (pode vir truncado, vazio ou ``None``).
    ``status`` é o ``status_checklist`` cru; ``None`` **não** passa no recorte
    (ver ``status_concluido``). ``tem_linha_no_erp=False`` distingue "não existe
    linha" de "linha com formulário vazio" — motivos diferentes, contadores
    diferentes.

    O status é avaliado **antes** dos campos de propósito: num checklist aberto
    a foto que falta pode simplesmente ainda não ter sido tirada, e contá-lo
    como ``campo_faltante`` inflaria o contador que a Tecnogera usa para achar
    técnico esquecendo de fotografar.
    """
    codigo = prefixo_formulario(formulario)

    if not tem_linha_no_erp:
        return Veredito(False, None, MotivoDescarte.FORMULARIO_AUSENTE)
    if codigo is None:
        return Veredito(False, None, MotivoDescarte.FORMULARIO_VAZIO)

    alvo = {f.strip().upper() for f in formularios_alvo}
    if codigo not in alvo:
        return Veredito(False, codigo, MotivoDescarte.FORMULARIO_FORA_WHITELIST)

    if exigir_concluido and not status_concluido(status):
        return Veredito(
            False,
            codigo,
            MotivoDescarte.STATUS_NAO_CONCLUIDO,
            status_bruto=(status or "").strip() or None,
        )

    # Só agora os campos importam — e só porque o formulário já os qualificou.
    presentes = normalizar_campos(campos)
    faltantes = tuple(c for c in obrigatorios if c not in presentes)
    if faltantes:
        return Veredito(
            False,
            codigo,
            MotivoDescarte.CAMPO_FALTANTE,
            faltantes,
            status_bruto=(status or "").strip() or None,
        )

    utilizados = tuple(c for c in (*obrigatorios, *opcionais) if c in presentes)
    return Veredito(
        True,
        codigo,
        None,
        (),
        utilizados,
        status_bruto=(status or "").strip() or None,
    )
