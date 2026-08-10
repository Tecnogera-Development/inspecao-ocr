"""Acesso somente-leitura ao SQL Server do Sisloc (`dbsisloc_tecnogera`).

Escopo: provar que a
API abre conexão e lê uma linha real de ``[dbo].[checklist produto]``.

Regras que este módulo existe para garantir:

* **Somente leitura.** Nenhum INSERT/UPDATE/DELETE/DDL, nunca. A credencial
  ``maisacesso_read`` é read-only no servidor; o código também é.
* **A senha não vaza.** A connection string contém ``PWD=`` em claro. Ela mora
  dentro do ``Settings``/``Engine`` e nunca entra em log nem em mensagem de
  exceção — ver ``_sanitizar``.
* **Degradação, não queda.** Qualquer falha vira ``IntegrationError``
  (integração indisponível) ou ``ConfigurationError`` (credencial ausente).
  A API sobe normalmente com o SQL Server inacessível.

Síncrono de propósito (o dialeto ``mssql+pyodbc`` é síncrono). Dentro do worker
Arq, chamar via ``asyncio.to_thread`` — o Sisloc está atrás de VPN e o handshake
custa dezenas a centenas de ms, o que bloquearia o event loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import bindparam, text

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError, IntegrationError
from app.core.logging import get_logger
from app.db.sisloc import build_sisloc_engine, get_sisloc_engine
from app.models.sisloc import SislocChecklist

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from sqlalchemy.engine import Engine

    from app.core.config import Settings

_log = get_logger(__name__)

# Objeto alvo. Identificador literal e constante: nunca montado a partir de input
# externo, sempre com schema qualificado e entre colchetes.
#
# ATENÇÃO — o ticket 03 e docs/exploracao/sql-server-driver.md descreviam este
# objeto como `[dbo].[checklist produto]`, com ESPAÇO. Medido contra o servidor
# real em 2026-08-02: o nome é `checklist_produto`, com UNDERSCORE, e é uma VIEW,
# não uma tabela (INFORMATION_SCHEMA.TABLES → TABLE_TYPE='VIEW'). O nome com
# espaço não existe no banco.
TABELA_CHECKLIST_PRODUTO = "[dbo].[checklist_produto]"

# `SELECT *` é deliberado enquanto o dicionário de campos não existe (ticket 04);
# o ping só conta colunas, não depende de nome nenhum.
_SQL_PING = text(f"SELECT TOP (1) * FROM {TABELA_CHECKLIST_PRODUTO}")  # noqa: S608

# Consulta em LOTE — nunca uma query por checklist. A ingestão agendada
# (ticket 07) avalia centenas de ids por rodada; um round-trip por id
# atravessando a VPN transformaria o cron em minutos de latência pura.
#
# **Filtro e enriquecimento são a MESMA query** (ticket 17): `formulario` e
# `status_checklist` decidem se o checklist entra, as outras nove colunas viajam
# no mesmo ida-e-volta e viram o snapshot. Trazer só o formulário e voltar ao
# servidor depois dobraria a latência sem economizar nada — a linha já foi lida.
#
# `codigo_checklist` é **int** na view (docs/exploracao/dicionario-campos-sisloc.md
# §colunas), então os parâmetros vão como int — comparar int com string forçaria
# conversão implícita e descartaria o índice.
#
# As 5 colunas deixadas de fora são ruído MEDIDO, não suspeito: `tipo_checklist`
# e `tarefa_inventario` são constantes em F180/F038, `local_inventario` é função
# determinística do formulário (F180→Externo, F038→Interno), `descricao_origem`
# é o `numero_om` com zeros à esquerda (20.398 de 20.398) e `id_origem` é chave
# de uma tabela cujo SELECT é negado à credencial.
_COLS = (
    "codigo_checklist, formulario, filial, patrimonio, projeto, "
    "responsavel, data_conclusao_checklist, status_checklist, "
    "origem, numero_om, ordem"
)

# ⚠️ `codigo_checklist` **NÃO é único**: 321 códigos repetidos em F180/F038, e as
# linhas DIVERGEM — 78 em `patrimonio` (geradores gêmeos em paralelo,
# `TECG00466A` × `TECG00466B`), 162 em `responsavel`, 161 em `numero_om`.
#
# ORDER BY `ordem`, **nunca** por data: `data_conclusao_checklist` é idêntica em
# 100% das duplicatas e não desempata nada — um ROW_NUMBER() por data escolheria
# linha arbitrária, isto é, equipamento arbitrário na tela do operador.
#
# `n_linhas` viaja junto para que o caso multi-ativo seja **avisado**, não
# escondido: sem ele o sistema nomearia o equipamento errado em silêncio em
# 0,36% dos casos, que é o pior modo de falha num produto de inspeção.
_SQL_ENRIQUECIMENTO = text(
    f"SELECT {_COLS}, n_linhas FROM ("  # noqa: S608 — identificador literal e constante
    f" SELECT {_COLS},"
    "        ROW_NUMBER() OVER ("
    "          PARTITION BY codigo_checklist"
    "          ORDER BY ordem ASC, numero_om ASC) AS rn,"
    "        COUNT(*) OVER (PARTITION BY codigo_checklist) AS n_linhas"
    f" FROM {TABELA_CHECKLIST_PRODUTO}"
    "  WHERE codigo_checklist IN :ids"
    ") t WHERE rn = 1"
).bindparams(bindparam("ids", expanding=True))

# Teto de parâmetros por statement no SQL Server é 2100; 500 dá folga larga e
# mantém o plano de execução barato.
LOTE_CHECKLISTS = 500

# Redação de segredo em qualquer texto que vá para log/exceção.
# Duas grafias importam:
#  1. crua, como aparece em erro de driver: `PWD={...}` ou `PWD=...;`
#  2. percent-encoded, como aparece em `str(engine.url)` / `repr(engine)` — o
#     `render_as_string(hide_password=True)` do SQLAlchemy **não** redige isso,
#     porque o segredo está num query param (`odbc_connect`), não no campo
#     password da URL. Verificado em 2026-08-02.
_RE_SEGREDO = re.compile(r"(PWD|Password)\s*=\s*(\{[^}]*\}|[^;]*)", re.IGNORECASE)
_RE_SEGREDO_URL = re.compile(r"(PWD|Password)%3D(%7B.*?%7D|[^;&%]*)", re.IGNORECASE)


def _sanitizar(mensagem: str) -> str:
    """Remove a senha de qualquer texto antes de logar ou levantar exceção.

    O pyodbc/SQLAlchemy embutem a connection string inteira em vários erros
    (``Can't open lib``, ``Login timeout expired``) e no ``repr`` do ``Engine``.
    Sem isso, a senha do ``maisacesso_read`` iria para o log estruturado.
    """
    return _RE_SEGREDO_URL.sub(r"\1%3D***", _RE_SEGREDO.sub(r"\1=***", mensagem))


@dataclass(frozen=True, slots=True)
class SislocPing:
    """Resultado de um ``ping()`` bem-sucedido."""

    alcancado: bool
    destino: str
    colunas: list[str] = field(default_factory=list)
    linha: dict[str, Any] = field(default_factory=dict)

    @property
    def total_colunas(self) -> int:
        return len(self.colunas)


class SislocService:
    """Leitura read-only do Sisloc, sobre um ``Engine`` dedicado.

    O ``Engine`` **nunca** é o do Postgres e nunca aparece no Alembic.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        engine: Engine | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._engine_override = engine

    # ── configuração ─────────────────────────────────────────────────────────

    @property
    def configurado(self) -> bool:
        return self._settings.sisloc_configurado

    @property
    def destino(self) -> str:
        """Host:porta/banco — seguro para log (sem usuário, sem senha)."""
        return self._settings.sisloc_destino

    def connection_string(self) -> str:
        """Connection string ODBC completa. **CONTÉM A SENHA — não logar.**

        Exposta para teste e diagnóstico do formato; use ``destino`` para log.
        """
        return self._settings.sisloc_odbc_connect

    def connection_string_mascarada(self) -> str:
        """Mesma string com o ``PWD`` redigido — essa sim pode ser exibida."""
        return _sanitizar(self._settings.sisloc_odbc_connect)

    # ── engine ───────────────────────────────────────────────────────────────

    def _engine(self) -> Engine:
        if self._engine_override is not None:
            return self._engine_override
        if self._settings is get_settings():
            return get_sisloc_engine()
        return build_sisloc_engine(self._settings)

    # ── operações ────────────────────────────────────────────────────────────

    def ping(self) -> SislocPing:
        """Roda um ``SELECT TOP 1`` real em ``[dbo].[checklist_produto]``.

        Retorna as colunas e a linha lida. Levanta ``ConfigurationError`` se a
        credencial não estiver configurada e ``IntegrationError`` se o servidor
        estiver inalcançável — nenhuma das duas carrega a senha na mensagem.
        """
        if not self.configurado:
            raise ConfigurationError(
                "Sisloc não configurado: defina SISLOC_DB_HOST, SISLOC_DB_USER "
                "e SISLOC_DB_PASSWORD"
            )
        try:
            engine = self._engine()
            with engine.connect() as conn:
                resultado = conn.execute(_SQL_PING)
                colunas = list(resultado.keys())
                primeira = resultado.mappings().first()
        except ConfigurationError:
            raise
        except Exception as exc:
            motivo = _sanitizar(str(exc))
            _log.warning("sisloc_ping_falhou", destino=self.destino, error=motivo)
            raise IntegrationError(
                f"falha ao conectar no Sisloc ({self.destino}): {motivo}",
                details={"destino": self.destino},
            ) from None  # `from None`: a exceção original repete a conn string
        linha = dict(primeira) if primeira is not None else {}
        _log.info(
            "sisloc_ping_ok",
            destino=self.destino,
            colunas=len(colunas),
            linha_encontrada=primeira is not None,
        )
        return SislocPing(
            alcancado=True,
            destino=self.destino,
            colunas=colunas,
            linha=linha,
        )

    def fetch_checklists(
        self, checklist_ids: Collection[str]
    ) -> dict[str, SislocChecklist]:
        """As 11 colunas úteis de cada ``checklist_id``, em lote. **SOMENTE SELECT.**

        Devolve ``{checklist_id: SislocChecklist}`` com **uma linha por
        checklist**, desempatada por ``ordem`` (ver ``_SQL_ENRIQUECIMENTO``), e
        ``n_linhas`` dizendo quantas linhas a view tinha — é o que permite à
        tela avisar que o checklist cobre mais de um ativo.

        Ids sem linha na view simplesmente **não aparecem** no dicionário — a
        ausência é informação (1,10% dos checklists com foto, ≈2/mês) e o
        chamador a conta como ``formulario_ausente``, distinta de
        ``formulario_vazio``.

        O texto de ``formulario`` vem truncado (``varchar(30)``): casar sempre
        por prefixo ``F0NN``, ver ``app.services.checklist_filter``.

        Ids não numéricos são ignorados — ``codigo_checklist`` é ``int``, e
        descartar antes de consultar evita erro de conversão no servidor.
        Levanta ``ConfigurationError`` sem credencial e ``IntegrationError`` com
        o Sisloc inalcançável (VPN caída → ``HYT00 Login timeout expired``).
        """
        if not self.configurado:
            raise ConfigurationError(
                "Sisloc não configurado: defina SISLOC_DB_HOST, SISLOC_DB_USER "
                "e SISLOC_DB_PASSWORD"
            )
        numericos = sorted({cid.strip() for cid in checklist_ids if cid.strip().isdigit()})
        if not numericos:
            return {}

        out: dict[str, SislocChecklist] = {}
        try:
            engine = self._engine()
            with engine.connect() as conn:
                for inicio in range(0, len(numericos), LOTE_CHECKLISTS):
                    lote = numericos[inicio : inicio + LOTE_CHECKLISTS]
                    linhas = (
                        conn.execute(_SQL_ENRIQUECIMENTO, {"ids": [int(cid) for cid in lote]})
                        .mappings()
                        .all()
                    )
                    for linha in linhas:
                        checklist = _para_checklist(linha)
                        out[checklist.codigo_checklist] = checklist
        except ConfigurationError:
            raise
        except Exception as exc:
            motivo = _sanitizar(str(exc))
            _log.warning(
                "sisloc_checklists_falhou",
                destino=self.destino,
                ids=len(numericos),
                error=motivo,
            )
            raise IntegrationError(
                f"falha ao consultar checklists no Sisloc ({self.destino}): {motivo}",
                details={"destino": self.destino, "ids": len(numericos)},
            ) from None  # `from None`: a exceção original repete a conn string
        multi = sum(1 for c in out.values() if c.n_linhas > 1)
        _log.info(
            "sisloc_checklists_ok",
            destino=self.destino,
            solicitados=len(numericos),
            encontrados=len(out),
            multi_ativo=multi,
        )
        return out


def _texto(valor: Any) -> str | None:  # noqa: ANN401 — vem do driver, tipo aberto
    """Normaliza texto do ERP: ``None`` para vazio, sempre sem espaço nas bordas."""
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


def _inteiro(valor: Any) -> int | None:  # noqa: ANN401 — vem do driver, tipo aberto
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _para_checklist(linha: Mapping[Any, Any]) -> SislocChecklist:
    """Uma linha do cursor vira o modelo validado. Nomes de coluna, não posição.

    Por nome de propósito: a lista de colunas do ``SELECT`` já mudou uma vez
    neste projeto, e um desalinhamento posicional trocaria ``patrimonio`` por
    ``projeto`` sem erro nenhum.
    """
    return SislocChecklist(
        codigo_checklist=str(linha["codigo_checklist"]),
        formulario=_texto(linha.get("formulario")) or "",
        filial=_texto(linha.get("filial")),
        patrimonio=_texto(linha.get("patrimonio")),
        projeto=_texto(linha.get("projeto")),
        responsavel=_texto(linha.get("responsavel")),
        data_conclusao=linha.get("data_conclusao_checklist"),
        status=_texto(linha.get("status_checklist")),
        origem=_texto(linha.get("origem")),
        numero_om=_inteiro(linha.get("numero_om")),
        ordem=_inteiro(linha.get("ordem")),
        n_linhas=_inteiro(linha.get("n_linhas")) or 1,
    )
