"""Testes do acesso somente-leitura ao SQL Server do Sisloc.


Tudo aqui roda com mock — a conexão real exige VPN e não pode ser pré-requisito
de CI. O smoke contra o servidor real é `python -m app.cli sisloc_ping`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.config import AppEnv, Settings
from app.core.exceptions import ConfigurationError, IntegrationError
from app.services.sisloc import (
    TABELA_CHECKLIST_PRODUTO,
    SislocPing,
    SislocService,
    _sanitizar,
)

_SENHA = "s3nh@-do-erp"


def _settings(**extra: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "app_env": AppEnv.TEST,
        "sisloc_db_host": "10.246.0.15",
        "sisloc_db_user": "maisacesso_read",
        "sisloc_db_password": _SENHA,
    }
    base.update(extra)
    return Settings(**base)


# ── fakes de Engine ───────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, colunas: list[str], linha: dict[str, Any] | None) -> None:
        self._colunas = colunas
        self._linha = linha

    def keys(self) -> list[str]:
        return self._colunas

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._linha


class _FakeConn:
    def __init__(self, resultado: _FakeResult | None, erro: Exception | None) -> None:
        self._resultado = resultado
        self._erro = erro
        self.sql_executado: str | None = None

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, stmt: Any) -> _FakeResult:
        self.sql_executado = str(stmt)
        if self._erro is not None:
            raise self._erro
        assert self._resultado is not None
        return self._resultado


class _FakeEngine:
    def __init__(
        self,
        *,
        colunas: list[str] | None = None,
        linha: dict[str, Any] | None = None,
        erro: Exception | None = None,
    ) -> None:
        resultado = _FakeResult(colunas or [], linha) if erro is None else None
        self.conn = _FakeConn(resultado, erro)

    def connect(self) -> _FakeConn:
        return self.conn


# ── connection string ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_connection_string_tem_o_formato_decidido() -> None:
    conn = SislocService(_settings()).connection_string()
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in conn
    assert "SERVER=10.246.0.15,1433" in conn
    assert "DATABASE=dbsisloc_tecnogera" in conn
    assert "UID=maisacesso_read" in conn
    assert f"PWD={{{_SENHA}}}" in conn
    assert "Connection Timeout=5" in conn
    assert "APP=tecnogera-ia-visual" in conn


@pytest.mark.unit
def test_connection_string_exige_encrypt_yes_e_trust_server_certificate() -> None:
    """Servidor 2017 com certificado não confiável: é o único arranjo que conecta."""
    conn = SislocService(_settings()).connection_string()
    assert "Encrypt=yes" in conn
    assert "TrustServerCertificate=yes" in conn
    # Encrypt=strict exigiria TDS 8.0 / SQL Server 2022+.
    assert "strict" not in conn


@pytest.mark.unit
def test_encrypt_strict_e_rejeitado_pelo_schema() -> None:
    with pytest.raises(ValueError):
        _settings(sisloc_db_encrypt="strict")


@pytest.mark.unit
def test_trust_server_certificate_desligavel_por_config() -> None:
    """Quando a CA interna chegar, basta virar a env var — sem mudar código."""
    conn = SislocService(_settings(sisloc_db_trust_server_certificate=False)).connection_string()
    assert "TrustServerCertificate=no" in conn


@pytest.mark.unit
def test_senha_com_chave_de_fechamento_e_escapada() -> None:
    """'}' literal dobra dentro de {...}, senão o driver trunca a senha."""
    conn = SislocService(_settings(sisloc_db_password="ab}cd")).connection_string()
    assert "PWD={ab}}cd}" in conn


@pytest.mark.unit
def test_senha_com_ponto_e_virgula_sobrevive_ao_join() -> None:
    conn = SislocService(_settings(sisloc_db_password="a;b=c")).connection_string()
    assert "PWD={a;b=c}" in conn
    # O campo seguinte continua íntegro: as chaves protegem o separador.
    assert "Encrypt=yes" in conn


# ── não-vazamento da senha ────────────────────────────────────────────────────


@pytest.mark.unit
def test_sanitizar_redige_pwd() -> None:
    bruto = f"DRIVER={{x}};UID=u;PWD={{{_SENHA}}};Encrypt=yes"
    limpo = _sanitizar(bruto)
    assert _SENHA not in limpo
    assert "PWD=***" in limpo
    assert "Encrypt=yes" in limpo


@pytest.mark.unit
def test_sanitizar_redige_password_sem_chaves() -> None:
    limpo = _sanitizar(f"Server=x;Password={_SENHA};Database=y")
    assert _SENHA not in limpo
    assert "Database=y" in limpo


@pytest.mark.unit
def test_destino_nao_contem_senha_nem_usuario() -> None:
    destino = SislocService(_settings()).destino
    assert destino == "10.246.0.15:1433/dbsisloc_tecnogera"
    assert _SENHA not in destino
    assert "maisacesso_read" not in destino


@pytest.mark.unit
def test_connection_string_mascarada_nao_expoe_senha() -> None:
    mascarada = SislocService(_settings()).connection_string_mascarada()
    assert _SENHA not in mascarada
    assert "SERVER=10.246.0.15,1433" in mascarada


@pytest.mark.unit
def test_repr_de_settings_nao_expoe_senha() -> None:
    """SecretStr protege o repr — garantia contra log acidental do Settings."""
    assert _SENHA not in repr(_settings())
    assert _SENHA not in str(_settings().sisloc_db_password)


@pytest.mark.unit
def test_erro_de_conexao_nao_vaza_senha_na_excecao() -> None:
    """O pyodbc embute a connection string em vários erros — tem de ser redigida."""
    erro = RuntimeError(
        "('HYT00', '[HYT00] [Microsoft][ODBC Driver 18 for SQL Server]"
        f"Login timeout expired') (conn: DRIVER={{x}};UID=u;PWD={{{_SENHA}}})"
    )
    service = SislocService(_settings(), engine=_FakeEngine(erro=erro))
    with pytest.raises(IntegrationError) as exc_info:
        service.ping()
    assert _SENHA not in str(exc_info.value)
    assert _SENHA not in str(exc_info.value.details)
    assert "Login timeout expired" in exc_info.value.message


@pytest.mark.unit
def test_erro_de_conexao_nao_vaza_senha_no_log(capsys: pytest.CaptureFixture[str]) -> None:
    """structlog escreve em stdout (PrintLoggerFactory), daí capsys e não caplog."""
    erro = RuntimeError(f"falhou; PWD={{{_SENHA}}}")
    service = SislocService(_settings(), engine=_FakeEngine(erro=erro))
    with pytest.raises(IntegrationError):
        service.ping()
    logado = capsys.readouterr().out
    assert "sisloc_ping_falhou" in logado
    assert _SENHA not in logado


# ── degradação ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_sem_credencial_nao_esta_configurado() -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST)
    assert cfg.sisloc_configurado is False
    assert SislocService(cfg).configurado is False


@pytest.mark.unit
def test_credencial_vazia_vira_none() -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST, sisloc_db_host="  ", sisloc_db_user="")
    assert cfg.sisloc_db_host is None
    assert cfg.sisloc_db_user is None


@pytest.mark.unit
def test_ping_sem_credencial_levanta_configuration_error() -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST)
    with pytest.raises(ConfigurationError) as exc_info:
        SislocService(cfg).ping()
    assert "SISLOC_DB_HOST" in exc_info.value.message
    assert exc_info.value.error_code == "configuration_error"


@pytest.mark.unit
def test_connection_string_sem_credencial_levanta_configuration_error() -> None:
    cfg = Settings(_env_file=None, app_env=AppEnv.TEST)
    with pytest.raises(ConfigurationError):
        _ = cfg.sisloc_odbc_connect


@pytest.mark.unit
def test_falha_de_conexao_vira_integration_error_com_destino() -> None:
    service = SislocService(_settings(), engine=_FakeEngine(erro=OSError("rota inexistente")))
    with pytest.raises(IntegrationError) as exc_info:
        service.ping()
    assert exc_info.value.status_code == 502
    assert exc_info.value.details["destino"] == "10.246.0.15:1433/dbsisloc_tecnogera"


@pytest.mark.unit
def test_api_sobe_sem_sql_server_configurado() -> None:
    """A indisponibilidade do Sisloc não pode derrubar o boot da API."""
    from app.main import create_app

    cfg = Settings(_env_file=None, app_env=AppEnv.TEST)
    app = create_app(cfg)
    assert app is not None


# ── ping feliz ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ping_le_linha_real_da_view() -> None:
    engine = _FakeEngine(
        colunas=["filial", "codigo_checklist"],
        linha={"filial": "SP - SBC", "codigo_checklist": 310877},
    )
    resultado = SislocService(_settings(), engine=engine).ping()
    assert resultado.alcancado is True
    assert resultado.total_colunas == 2
    assert resultado.linha["codigo_checklist"] == 310877
    assert resultado.destino == "10.246.0.15:1433/dbsisloc_tecnogera"


@pytest.mark.unit
def test_ping_usa_select_top_1_no_objeto_qualificado() -> None:
    """Somente leitura, objeto entre colchetes e com schema — nunca DDL/DML."""
    engine = _FakeEngine(colunas=["filial"], linha={"filial": "SP - SBC"})
    SislocService(_settings(), engine=engine).ping()
    sql = (engine.conn.sql_executado or "").upper()
    assert sql.startswith("SELECT TOP (1)")
    assert TABELA_CHECKLIST_PRODUTO.upper() in sql
    for proibido in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"):
        assert proibido not in sql


@pytest.mark.unit
def test_ping_com_view_vazia_nao_quebra() -> None:
    engine = _FakeEngine(colunas=["filial"], linha=None)
    resultado = SislocService(_settings(), engine=engine).ping()
    assert resultado.alcancado is True
    assert resultado.linha == {}


@pytest.mark.unit
def test_sisloc_ping_dataclass_e_imutavel() -> None:
    ping = SislocPing(alcancado=True, destino="x")
    with pytest.raises(Exception):  # noqa: B017,PT011  frozen dataclass
        ping.alcancado = False  # type: ignore[misc]


# ── Engine dedicado ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_engine_dedicado_usa_pool_pequeno_e_autocommit() -> None:
    """Nunca reusa o Engine do Postgres; pool pequeno para não irritar o DBA."""
    pyodbc = pytest.importorskip("pyodbc")
    assert pyodbc is not None
    from app.db.sisloc import MAX_OVERFLOW, POOL_SIZE, build_sisloc_engine

    engine = build_sisloc_engine(_settings())
    try:
        assert engine.dialect.name == "mssql"
        assert engine.pool.size() == POOL_SIZE
        assert POOL_SIZE == 2
        assert MAX_OVERFLOW == 3
        assert engine.get_execution_options()["isolation_level"] == "AUTOCOMMIT"
    finally:
        engine.dispose()


@pytest.mark.unit
def test_engine_do_sisloc_nao_e_o_do_postgres() -> None:
    pytest.importorskip("pyodbc")
    from app.db.session import _get_session_factory
    from app.db.sisloc import build_sisloc_engine

    sisloc = build_sisloc_engine(_settings())
    try:
        postgres = _get_session_factory().kw["bind"]
        assert sisloc is not postgres
        assert sisloc.dialect.name != postgres.dialect.name
    finally:
        sisloc.dispose()


@pytest.mark.unit
def test_dispose_limpa_o_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pyodbc")
    import app.db.sisloc as mod

    monkeypatch.setattr(mod, "get_settings", _settings)
    mod.dispose_sisloc_engine()
    primeiro = mod.get_sisloc_engine()
    assert mod.get_sisloc_engine() is primeiro
    mod.dispose_sisloc_engine()
    assert mod._engine is None


# ── CLI (`python -m app.cli sisloc_ping`) ─────────────────────────────────────


@pytest.mark.unit
def test_cli_sisloc_ping_imprime_colunas(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import app.cli as cli
    import app.services.sisloc as mod

    engine = _FakeEngine(colunas=["filial", "codigo_checklist"], linha={"filial": "SP - SBC"})
    monkeypatch.setattr(
        mod, "SislocService", lambda *a, **k: SislocService(_settings(), engine=engine)
    )
    cli._run_sisloc_ping()
    saida = capsys.readouterr().out
    assert "OK — conexão estabelecida, 2 colunas" in saida
    assert "filial = 'SP - SBC'" in saida
    assert _SENHA not in saida


@pytest.mark.unit
def test_cli_sisloc_ping_sai_com_erro_e_dica_de_vpn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import app.cli as cli
    import app.services.sisloc as mod

    erro = RuntimeError(f"HYT00 Login timeout expired; PWD={{{_SENHA}}}")
    monkeypatch.setattr(
        mod,
        "SislocService",
        lambda *a, **k: SislocService(_settings(), engine=_FakeEngine(erro=erro)),
    )
    with pytest.raises(SystemExit) as exc_info:
        cli._run_sisloc_ping()
    assert exc_info.value.code == 1
    capturado = capsys.readouterr()
    assert "VPN" in capturado.err
    assert _SENHA not in capturado.err
    assert _SENHA not in capturado.out


@pytest.mark.unit
def test_cli_registra_o_subcomando_sisloc_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.cli as cli

    chamado: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run_sisloc_ping", lambda **kw: chamado.update(kw))
    cli.main(["sisloc_ping", "--verbose"])
    assert chamado == {"verbose": True}


@pytest.mark.unit
def test_service_sem_settings_reusa_o_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.sisloc as mod

    cfg = _settings()
    sentinela = _FakeEngine(colunas=[], linha=None)
    monkeypatch.setattr(mod, "get_settings", lambda: cfg)
    monkeypatch.setattr(mod, "get_sisloc_engine", lambda: sentinela)
    assert SislocService()._engine() is sentinela


@pytest.mark.unit
def test_service_com_settings_proprio_constroi_engine_novo(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.sisloc as mod

    sentinela = _FakeEngine(colunas=[], linha=None)
    monkeypatch.setattr(mod, "get_settings", lambda: Settings(_env_file=None, app_env=AppEnv.TEST))
    monkeypatch.setattr(mod, "build_sisloc_engine", lambda cfg: sentinela)
    assert SislocService(_settings())._engine() is sentinela


@pytest.mark.unit
def test_configuration_error_do_engine_nao_vira_integration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credencial ausente é erro de config (500), não integração indisponível (502)."""
    import app.services.sisloc as mod

    def _explode(_cfg: Settings) -> None:
        raise ConfigurationError("credenciais do Sisloc ausentes")

    monkeypatch.setattr(mod, "get_settings", lambda: Settings(_env_file=None, app_env=AppEnv.TEST))
    monkeypatch.setattr(mod, "build_sisloc_engine", _explode)
    with pytest.raises(ConfigurationError):
        SislocService(_settings()).ping()


@pytest.mark.unit
def test_sanitizar_cobre_a_forma_percent_encoded_da_url() -> None:
    """`repr(engine)` traz a senha URL-encoded; hide_password do SQLAlchemy não redige."""
    pytest.importorskip("pyodbc")
    from app.db.sisloc import build_sisloc_engine

    engine = build_sisloc_engine(_settings(sisloc_db_password="senha-simples"))
    try:
        cru = repr(engine)
        assert "senha-simples" in cru, "premissa do teste: o repr vaza mesmo"
        assert "senha-simples" not in _sanitizar(cru)
    finally:
        engine.dispose()


# ── consulta em lote: filtro + enriquecimento (tickets 07 e 17) ───────────────


def _linha(codigo: int, **extra: Any) -> dict[str, Any]:
    """Uma linha da view, com os nomes de coluna REAIS de `checklist_produto`."""
    base: dict[str, Any] = {
        "codigo_checklist": codigo,
        "formulario": "F180-VISITA GMG_REV04",
        "filial": "SP - SBC",
        "patrimonio": "TERP00601",
        "projeto": "035514/2026-EBAZAR.COM.BR. LTDA",
        "responsavel": "FILIPE.VIEIRA",
        "data_conclusao_checklist": datetime(2026, 7, 31, 23, 38, 7),
        "status_checklist": "Concluído",
        "origem": "OM",
        "numero_om": 104556,
        "ordem": 1,
        "n_linhas": 1,
    }
    base.update(extra)
    return base


class _FakeLoteConn:
    """Conn que registra cada `execute(stmt, params)` do lote."""

    def __init__(
        self, linhas: list[dict[str, Any]] | None = None, erro: Exception | None = None
    ) -> None:
        self._linhas = linhas or []
        self._erro = erro
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> _FakeLoteConn:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.chamadas.append((str(stmt), dict(params or {})))
        if self._erro is not None:
            raise self._erro
        pedidos = set(params["ids"]) if params else set()
        resultado = MagicMock()
        resultado.mappings.return_value.all.return_value = [
            linha for linha in self._linhas if linha["codigo_checklist"] in pedidos
        ]
        return resultado


class _FakeLoteEngine:
    def __init__(self, conn: _FakeLoteConn) -> None:
        self.conn = conn

    def connect(self) -> _FakeLoteConn:
        return self.conn


def _servico_lote(conn: _FakeLoteConn) -> SislocService:
    return SislocService(_settings(), engine=_FakeLoteEngine(conn))


@pytest.mark.unit
def test_fetch_checklists_faz_uma_query_para_muitos_ids() -> None:
    """Uma query por checklist atravessando VPN transformaria o cron em latência."""
    conn = _FakeLoteConn(
        [_linha(278749), _linha(276800, formulario="F013 - GERADOR")]
    )

    out = _servico_lote(conn).fetch_checklists(["278749", "276800"])

    assert set(out) == {"278749", "276800"}
    assert out["278749"].formulario == "F180-VISITA GMG_REV04"
    assert out["276800"].formulario == "F013 - GERADOR"
    assert len(conn.chamadas) == 1
    sql = conn.chamadas[0][0].upper()
    assert "SELECT" in sql
    assert "IN" in sql
    # Somente SELECT — nenhuma escrita, jamais.
    assert not any(v in sql for v in ("INSERT", "UPDATE", "DELETE", "MERGE", "DROP"))


@pytest.mark.unit
def test_uma_query_traz_filtro_e_enriquecimento_juntos() -> None:
    """Ticket 17: filtro e enriquecimento são o MESMO ida-e-volta."""
    conn = _FakeLoteConn([_linha(311771)])

    linha = _servico_lote(conn).fetch_checklists(["311771"])["311771"]

    # Filtro:
    assert linha.formulario == "F180-VISITA GMG_REV04"
    assert linha.status == "Concluído"
    # Enriquecimento, no mesmo round-trip:
    assert linha.filial == "SP - SBC"
    assert linha.patrimonio == "TERP00601"
    assert linha.responsavel == "FILIPE.VIEIRA"
    assert linha.numero_om == 104556
    assert linha.data_conclusao == datetime(2026, 7, 31, 23, 38, 7)
    assert linha.projeto_parseado.cliente == "EBAZAR.COM.BR. LTDA"
    assert len(conn.chamadas) == 1


@pytest.mark.unit
def test_query_desempata_por_ordem_e_nao_por_data() -> None:
    """`data_conclusao` é idêntica em 100% das duplicatas: não desempata nada."""
    conn = _FakeLoteConn([_linha(300425)])
    _servico_lote(conn).fetch_checklists(["300425"])

    sql = " ".join(conn.chamadas[0][0].split()).upper()
    assert "ROW_NUMBER() OVER ( PARTITION BY CODIGO_CHECKLIST ORDER BY ORDEM ASC" in sql
    assert "NUMERO_OM ASC" in sql
    assert "COUNT(*) OVER (PARTITION BY CODIGO_CHECKLIST) AS N_LINHAS" in sql
    assert "RN = 1" in sql
    assert "ORDER BY DATA_CONCLUSAO" not in sql


@pytest.mark.unit
def test_n_linhas_viaja_junto_para_a_tela_avisar() -> None:
    """Sem ele, o sistema nomearia o equipamento errado em silêncio (0,36%)."""
    conn = _FakeLoteConn([_linha(300425, patrimonio="TECG00466A", n_linhas=4)])

    linha = _servico_lote(conn).fetch_checklists(["300425"])["300425"]

    assert linha.n_linhas == 4
    assert linha.patrimonio == "TECG00466A"
    assert linha.snapshot().multi_ativo


@pytest.mark.unit
def test_as_colunas_de_ruido_medido_ficam_fora_da_query() -> None:
    """Constantes, funções do formulário e redundâncias não viajam pela VPN."""
    conn = _FakeLoteConn()
    _servico_lote(conn).fetch_checklists(["1"])
    sql = conn.chamadas[0][0].lower()
    for ruido in ("tipo_checklist", "tarefa_inventario", "local_inventario",
                  "descricao_origem", "id_origem"):
        assert ruido not in sql


@pytest.mark.unit
def test_fetch_checklists_manda_int_porque_a_coluna_e_int() -> None:
    conn = _FakeLoteConn()
    _servico_lote(conn).fetch_checklists(["9", "8"])
    assert conn.chamadas[0][1]["ids"] == [8, 9]


@pytest.mark.unit
def test_fetch_checklists_quebra_em_lotes() -> None:
    from app.services.sisloc import LOTE_CHECKLISTS

    conn = _FakeLoteConn()
    ids = [str(i) for i in range(LOTE_CHECKLISTS + 10)]
    _servico_lote(conn).fetch_checklists(ids)
    assert len(conn.chamadas) == 2
    assert LOTE_CHECKLISTS == 500  # teto do SQL Server é 2100 parâmetros


@pytest.mark.unit
def test_fetch_checklists_ignora_id_nao_numerico() -> None:
    """`codigo_checklist` é int: id textual nem chega ao servidor."""
    conn = _FakeLoteConn()
    assert _servico_lote(conn).fetch_checklists(["abc", "'; DROP TABLE x --"]) == {}
    assert conn.chamadas == []


@pytest.mark.unit
def test_fetch_checklists_sem_ids_nao_toca_no_banco() -> None:
    conn = _FakeLoteConn()
    assert _servico_lote(conn).fetch_checklists([]) == {}
    assert conn.chamadas == []


@pytest.mark.unit
def test_fetch_checklists_id_ausente_nao_aparece_no_retorno() -> None:
    """Ausência é informação: 1,10% dos checklists com foto nunca entram na view."""
    conn = _FakeLoteConn([_linha(1)])
    out = _servico_lote(conn).fetch_checklists(["1", "2"])
    assert set(out) == {"1"}


@pytest.mark.unit
def test_campos_vazios_do_erp_viram_none() -> None:
    """`patrimonio` em branco é ausência, não string vazia na tela."""
    conn = _FakeLoteConn(
        [_linha(1, patrimonio="  ", projeto=None, responsavel="", numero_om=None)]
    )
    linha = _servico_lote(conn).fetch_checklists(["1"])["1"]
    assert linha.patrimonio is None
    assert linha.projeto is None
    assert linha.responsavel is None
    assert linha.numero_om is None


@pytest.mark.unit
def test_numero_impresentavel_vira_none_em_vez_de_derrubar_a_rodada() -> None:
    """Um valor inesperado numa coluna numérica não pode abortar o cron inteiro."""
    conn = _FakeLoteConn([_linha(1, numero_om="não é número", ordem=None)])
    linha = _servico_lote(conn).fetch_checklists(["1"])["1"]
    assert linha.numero_om is None
    assert linha.ordem is None
    assert linha.formulario == "F180-VISITA GMG_REV04"  # o resto sobrevive


@pytest.mark.unit
def test_fetch_checklists_vpn_caida_vira_integration_error_sem_senha() -> None:
    erro = Exception(f"[HYT00] Login timeout expired PWD={{{_SENHA}}}")
    conn = _FakeLoteConn(erro=erro)

    with pytest.raises(IntegrationError) as exc:
        _servico_lote(conn).fetch_checklists(["1"])
    assert _SENHA not in str(exc.value)


@pytest.mark.unit
def test_fetch_checklists_sem_credencial_levanta_configuration_error() -> None:
    service = SislocService(Settings(_env_file=None, app_env=AppEnv.TEST))
    with pytest.raises(ConfigurationError):
        service.fetch_checklists(["1"])
