"""Schemas do enriquecimento vindo de ``dbo.checklist_produto``.

Sobre a
medição de ``docs/exploracao/enriquecimento-checklist-produto.md``.

Dois modelos e um parser, todos puros (nenhuma I/O, nenhum acesso a banco):

``SislocChecklist``
    Uma linha da view, **já desempatada** pelo ``ROW_NUMBER()`` da consulta. É o
    que o filtro e o enriquecimento consomem — os dois saem do mesmo
    ida-e-volta.

``SislocSnapshot``
    O que fica congelado em ``pipeline_jobs.sisloc_snapshot`` (JSONB). Congela o
    que o operador viu no momento da análise: o ERP muda, o laudo não deveria.
    O ``ground_truth`` do HITL alimenta o F1 do contrato, e um julgamento de
    seis meses atrás precisa ser lido contra o cliente que estava na tela
    **naquele dia**, não contra o que o ERP diz hoje. Daí ``lido_em``: sem ele
    não se distingue "o dado era esse" de "o dado foi lido antes da correção".

``parse_projeto``
    ``projeto`` é o **cliente**, o campo de nome mais opaco da view:
    ``035514/2026-EBAZAR.COM.BR. LTDA`` = ``<contrato>/<ano>-<CLIENTE>``. O
    padrão bate em **19.758 de 19.763 (99,97%)** sobre 695 clientes distintos —
    por isso o bruto é preservado sempre: os 0,03% que não casam não podem
    sumir.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: ``<6 dígitos>/<4 dígitos>-<CLIENTE>``. Os quantificadores são exatos de
#: propósito: foi essa a forma medida (``LIKE '[0-9]{6}/[0-9]{4}-%'``), e afrouxá-la
#: faria valores estranhos passarem como se fossem contrato válido.
_RE_PROJETO = re.compile(r"^(?P<contrato>\d{6})/(?P<ano>\d{4})-(?P<cliente>.*)$")


class ProjetoParseado(BaseModel):
    """``projeto`` decomposto — **com o bruto sempre preservado**."""

    model_config = ConfigDict(frozen=True)

    #: Valor cru da coluna, tal como veio da view. Nunca descartado.
    bruto: str | None = None
    contrato: str | None = None
    ano: int | None = None
    cliente: str | None = None
    #: ``False`` quando o valor não casa com ``<contrato>/<ano>-<CLIENTE>``.
    #: 0,03% dos casos medidos — a tela deve exibir o bruto nesses.
    padrao_reconhecido: bool = False


def parse_projeto(bruto: str | None) -> ProjetoParseado:
    """Separa contrato, ano e cliente. Nunca levanta, nunca perde o bruto.

    Valores como ``999999/9999-PETROBRAS NACIONAL 2020`` (contrato
    guarda-chuva) e ``000000/2016-TECNOGERA`` (estoque próprio) casam com o
    padrão e são preservados como qualquer outro: interpretá-los é decisão de
    tela, não de parser.
    """
    limpo = (bruto or "").strip()
    if not limpo:
        return ProjetoParseado()
    match = _RE_PROJETO.match(limpo)
    if match is None:
        return ProjetoParseado(bruto=limpo)
    cliente = match.group("cliente").strip()
    return ProjetoParseado(
        bruto=limpo,
        contrato=match.group("contrato"),
        ano=int(match.group("ano")),
        cliente=cliente or None,
        padrao_reconhecido=True,
    )


class SislocSnapshot(BaseModel):
    """Congelamento auditável da linha do ERP no instante da materialização.

    ``extra="forbid"``: o snapshot é contrato, não saco de dados. JSON sem
    validação apodrece — este modelo é o que impede isso.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    codigo_checklist: str
    formulario: str | None = None
    filial: str | None = None
    patrimonio: str | None = None
    projeto: ProjetoParseado = Field(default_factory=ProjetoParseado)
    responsavel: str | None = None
    data_conclusao: datetime | None = None
    status: str | None = None
    origem: str | None = None
    numero_om: int | None = None
    #: Sequência da linha dentro do checklist — é o desempate das duplicatas.
    ordem: int | None = None
    #: Quantas linhas a view tem para este ``codigo_checklist``. ``> 1`` significa
    #: que o checklist cobre mais de um ativo (78 casos medidos divergem em
    #: ``patrimonio``: geradores gêmeos em paralelo). A tela **avisa**.
    n_linhas: int = 1
    #: Quando a linha foi lida do ERP. Sem isto o snapshot não se distingue de
    #: uma leitura feita antes de uma correção no Sisloc.
    lido_em: datetime

    @property
    def multi_ativo(self) -> bool:
        return self.n_linhas > 1

    def como_json(self) -> dict[str, Any]:
        """Forma serializável para a coluna JSONB (datas em ISO-8601)."""
        return self.model_dump(mode="json")


class SislocChecklist(BaseModel):
    """Uma linha de ``dbo.checklist_produto``, já desempatada por ``ordem``.

    Só as 11 colunas que informam alguma coisa. Ficaram de fora, por medição:
    ``tipo_checklist`` e ``tarefa_inventario`` (constantes em F180/F038),
    ``local_inventario`` (função determinística do formulário),
    ``descricao_origem`` (= ``numero_om`` com zeros à esquerda, 20.398/20.398) e
    ``id_origem`` (chave de uma tabela cuja leitura é negada à credencial).
    """

    model_config = ConfigDict(frozen=True)

    codigo_checklist: str
    #: ``varchar(30)`` truncado no banco — casar SEMPRE por prefixo ``F0NN``.
    formulario: str = ""
    filial: str | None = None
    patrimonio: str | None = None
    #: Valor **bruto** de ``projeto``; use ``projeto_parseado`` para os pedaços.
    projeto: str | None = None
    responsavel: str | None = None
    data_conclusao: datetime | None = None
    #: ``Concluído`` | ``A Executar`` | ``A Conferir`` (domínio medido).
    status: str | None = None
    origem: str | None = None
    numero_om: int | None = None
    ordem: int | None = None
    n_linhas: int = 1

    @property
    def projeto_parseado(self) -> ProjetoParseado:
        return parse_projeto(self.projeto)

    def snapshot(self, *, lido_em: datetime | None = None) -> SislocSnapshot:
        """Congela esta linha para persistência em ``sisloc_snapshot``."""
        return SislocSnapshot(
            codigo_checklist=self.codigo_checklist,
            formulario=self.formulario or None,
            filial=self.filial,
            patrimonio=self.patrimonio,
            projeto=self.projeto_parseado,
            responsavel=self.responsavel,
            data_conclusao=self.data_conclusao,
            status=self.status,
            origem=self.origem,
            numero_om=self.numero_om,
            ordem=self.ordem,
            n_linhas=self.n_linhas,
            lido_em=lido_em or datetime.now(UTC),
        )
