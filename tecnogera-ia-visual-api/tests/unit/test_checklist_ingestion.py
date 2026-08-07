"""Ingestão agendada de checklists — ticket mvp-c54-c57/07.

Tudo aqui roda contra mock e SQLite em memória: nenhuma varredura real do
Dropbox, nenhuma consulta ao SQL Server e **nenhuma chamada de LLM**.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.core.exceptions import ConfigurationError, IntegrationError
from app.db.base import Base
from app.models.dropbox import DropboxDelta, ImageMetadata
from app.models.ingest import (
    CURSOR_CHECKLISTS,
    STATUS_DESCARTADO,
    STATUS_MATERIALIZADO,
    STATUS_PENDENTE,
    ChecklistIngestState,
    IngestCursor,
)
from app.models.pipeline import PipelineJob
from app.models.sisloc import SislocChecklist
from app.services.checklist_ingestion import ChecklistIngestionService
from app.services.dropbox import parse_filename

pytestmark = pytest.mark.unit


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.TEST, log_level="DEBUG")


def _imagem(checklist_id: str, campo: str, *, quando: str = "01_08_2026 09_00_00") -> ImageMetadata:
    """Monta um ImageMetadata com o nome REAL do Sisloc, via parse_filename."""
    nome = f"153269005_checklist_{checklist_id}_{campo}_0_{quando}.jpeg"
    dia, mes, resto = quando.split("_", 2)
    ano, hora = resto.split(" ")
    h, m, s = hora.split("_")
    return ImageMetadata(
        dropbox_path=f"/Sisloc/MG - CGE/Checklist/Chk/{nome}",
        filename=nome,
        size_bytes=1234,
        parsed=parse_filename(nome),
        server_modified=datetime(int(ano), int(mes), int(dia), int(h), int(m), int(s)),
    )


def _dropbox(*imagens: ImageMetadata, cursor: str = "cursor-2", **kwargs: Any) -> MagicMock:
    mock = MagicMock()
    mock.latest_checklist_cursor.return_value = "cursor-boot"
    mock.list_checklist_delta.return_value = DropboxDelta(
        cursor=cursor, images=list(imagens), **kwargs
    )
    return mock


def _checklist(
    codigo: str, formulario: str = "F038", *, status: str = "Concluído", **extra: Any
) -> SislocChecklist:
    """Uma linha da view já desempatada, com os defaults do caso feliz."""
    return SislocChecklist(
        codigo_checklist=codigo,
        formulario=formulario,
        status=status,
        **extra,
    )


def _sisloc(formularios: dict[str, str] | None = None) -> MagicMock:
    """Sisloc fake. O dict é `{checklist_id: formulario}`, tudo `Concluído`.

    Atalho de conveniência: o recorte por status tem testes próprios, e repetir
    `status="Concluído"` em trinta chamadas escondia o que cada teste mede.
    """
    mock = MagicMock()
    mock.fetch_checklists.return_value = {
        cid: _checklist(cid, form) for cid, form in (formularios or {}).items()
    }
    return mock


def _servico(db: Session, dropbox: MagicMock, sisloc: MagicMock, cfg: Settings):
    return ChecklistIngestionService(db=db, dropbox=dropbox, sisloc=sisloc, settings=cfg)


def _com_cursor(db: Session, cursor: str = "cursor-1") -> None:
    db.add(IngestCursor(name=CURSOR_CHECKLISTS, cursor=cursor))
    db.commit()


# ── bootstrap / marco de corte ────────────────────────────────────────────────


def test_bootstrap_nao_varre_historico(db: Session, cfg: Settings) -> None:
    """Primeira rodada só fixa o cursor: nada do passado entra na esteira."""
    dropbox = _dropbox()
    servico = _servico(db, dropbox, _sisloc(), cfg)

    resultado = servico.scan_and_ingest()

    assert resultado.bootstrap
    assert resultado.jobs_criados == 0
    dropbox.latest_checklist_cursor.assert_called_once()
    dropbox.list_checklist_delta.assert_not_called()
    assert db.get(IngestCursor, CURSOR_CHECKLISTS).cursor == "cursor-boot"


def test_marco_de_corte_por_data_descarta_arquivo_antigo(db: Session, cfg: Settings) -> None:
    cfg = cfg.model_copy(update={"checklist_ingest_since": date(2026, 8, 1)})
    _com_cursor(db)
    dropbox = _dropbox(
        _imagem("900", "c54", quando="15_07_2026 09_00_00"),
        _imagem("900", "c55", quando="15_07_2026 09_00_00"),
        _imagem("900", "c56", quando="15_07_2026 09_00_00"),
    )
    resultado = _servico(db, dropbox, _sisloc({"900": "F038"}), cfg).scan_and_ingest()

    assert resultado.imagens == 0
    assert resultado.jobs_criados == 0


def test_cursor_resetado_rebootstrapa_sem_derrubar(db: Session, cfg: Settings) -> None:
    _com_cursor(db)
    dropbox = _dropbox()
    dropbox.list_checklist_delta.return_value = DropboxDelta(cursor="", reset=True)

    resultado = _servico(db, dropbox, _sisloc(), cfg).scan_and_ingest()

    assert resultado.cursor_resetado
    assert resultado.bootstrap
    assert db.get(IngestCursor, CURSOR_CHECKLISTS).cursor == "cursor-boot"


# ── caminho feliz ─────────────────────────────────────────────────────────────


def test_checklist_completo_vira_job(db: Session, cfg: Settings) -> None:
    _com_cursor(db)
    dropbox = _dropbox(
        _imagem("278749", "c54"),
        _imagem("278749", "c55"),
        _imagem("278749", "c56"),
        _imagem("278749", "c12"),
    )
    sisloc = _sisloc({"278749": "F038 - PRÉ LOCAÇÃO DE GERADOR"})

    resultado = _servico(db, dropbox, sisloc, cfg).scan_and_ingest()

    assert resultado.jobs_criados == 1
    assert resultado.descartes == {}
    job = db.query(PipelineJob).one()
    assert job.checklist_id == "278749"
    assert job.status == "pending"  # despacho é do ticket 08 — sem LLM aqui

    estado = db.get(ChecklistIngestState, "278749")
    assert estado.status == STATUS_MATERIALIZADO
    assert estado.job_id == job.id
    assert estado.formulario == "F038 - PRÉ LOCAÇÃO DE GERADOR"
    # Cursor avançou → o mesmo delta não volta.
    assert db.get(IngestCursor, CURSOR_CHECKLISTS).cursor == "cursor-2"


def test_c57_opcional_nao_bloqueia(db: Session, cfg: Settings) -> None:
    """c57 nunca bloqueia — é opcional em ``avaliar`` (ver ``checklist_filter``)."""
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("1", c) for c in ("c54", "c55", "c56")))
    resultado = _servico(db, dropbox, _sisloc({"1": "F038"}), cfg).scan_and_ingest()
    assert resultado.jobs_criados == 1


def test_consulta_ao_sisloc_e_em_lote(db: Session, cfg: Settings) -> None:
    """Uma query para N checklists, nunca uma por checklist."""
    _com_cursor(db)
    imagens = [_imagem(str(i), c) for i in range(1, 21) for c in ("c54", "c55", "c56")]
    sisloc = _sisloc({str(i): "F038" for i in range(1, 21)})

    _servico(db, _dropbox(*imagens), sisloc, cfg).scan_and_ingest()

    assert sisloc.fetch_checklists.call_count == 1
    assert len(sisloc.fetch_checklists.call_args.args[0]) == 20


# ── descartes ─────────────────────────────────────────────────────────────────


def test_checklist_incompleto_nao_vira_job_e_conta_o_campo(db: Session, cfg: Settings) -> None:
    _com_cursor(db)
    dropbox = _dropbox(_imagem("500", "c54"), _imagem("500", "c56"))

    resultado = _servico(db, dropbox, _sisloc({"500": "F038"}), cfg).scan_and_ingest()

    assert resultado.jobs_criados == 0
    assert resultado.descartes == {"campo_faltante:c55": 1}
    assert db.query(PipelineJob).count() == 0
    # Não é terminal: continua pendente para o próximo delta completar.
    assert db.get(ChecklistIngestState, "500").status == STATUS_PENDENTE


def test_formulario_fora_da_whitelist_e_descarte_terminal(db: Session, cfg: Settings) -> None:
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("600", c) for c in ("c54", "c55", "c56", "c57")))

    resultado = _servico(db, dropbox, _sisloc({"600": "F013 - GERADOR"}), cfg).scan_and_ingest()

    assert resultado.jobs_criados == 0
    assert resultado.descartes == {"formulario_fora_whitelist:F013": 1}
    assert db.get(ChecklistIngestState, "600").status == STATUS_DESCARTADO


def test_f180_e_descartado_sem_gastar_llm(db: Session, cfg: Settings) -> None:
    """Corte para F038: F180 completo e concluído, mesmo assim fora.

    Nenhum job nasce — ``status == 'pending'`` só existe para job materializado,
    e este checklist nunca chega lá. Sem job, o despacho (ticket 08) nunca roda
    e nenhuma chamada de LLM acontece.
    """
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("601", c) for c in ("c54", "c55", "c56")))

    resultado = _servico(
        db, dropbox, _sisloc({"601": "F180-VISITA GMG_REV04"}), cfg
    ).scan_and_ingest()

    assert resultado.jobs_criados == 0
    assert resultado.descartes == {"formulario_fora_whitelist:F180": 1}
    assert db.query(PipelineJob).count() == 0
    assert db.get(ChecklistIngestState, "601").status == STATUS_DESCARTADO


def test_formulario_vazio_e_contado_a_parte(db: Session, cfg: Settings) -> None:
    """36% do parque. Descartado, mas sem se misturar a 'formulário errado'."""
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("700", c) for c in ("c54", "c55", "c56")))

    resultado = _servico(db, dropbox, _sisloc({"700": "   "}), cfg).scan_and_ingest()

    assert resultado.descartes == {"formulario_vazio": 1}


def test_sem_linha_no_erp_conta_como_ausente(db: Session, cfg: Settings) -> None:
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("800", c) for c in ("c54", "c55", "c56")))

    resultado = _servico(db, dropbox, _sisloc({}), cfg).scan_and_ingest()

    assert resultado.descartes == {"formulario_ausente": 1}
    assert db.get(ChecklistIngestState, "800").status == STATUS_PENDENTE


# ── recorte por status + enriquecimento (ticket 17) ───────────────────────────


@pytest.mark.parametrize("aberto", ["A Executar", "A Conferir"])
def test_checklist_nao_concluido_e_descartado_com_contador_proprio(
    db: Session, cfg: Settings, aberto: str
) -> None:
    """14,8% dos F180/F038. Fotos possivelmente parciais: não gastar chave paga."""
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("810", c) for c in ("c54", "c55", "c56")))
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {"810": _checklist("810", status=aberto)}

    resultado = _servico(db, dropbox, sisloc, cfg).scan_and_ingest()

    assert resultado.jobs_criados == 0
    assert resultado.descartes == {f"status_nao_concluido:{aberto}": 1}
    assert resultado.status_nao_concluido == 1
    assert db.query(PipelineJob).count() == 0
    # NÃO terminal: o checklist ainda pode fechar.
    assert db.get(ChecklistIngestState, "810").status == STATUS_PENDENTE


def test_status_nao_concluido_e_contado_a_parte_de_campo_faltante(
    db: Session, cfg: Settings
) -> None:
    """Ações diferentes: um cobra foto do técnico, o outro cobra fechar no ERP."""
    _com_cursor(db)
    dropbox = _dropbox(
        *(_imagem("820", c) for c in ("c54", "c55", "c56")),
        _imagem("821", "c54"),
    )
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {
        "820": _checklist("820", status="A Executar"),
        "821": _checklist("821"),
    }

    resultado = _servico(db, dropbox, sisloc, cfg).scan_and_ingest()

    assert resultado.descartes == {
        "status_nao_concluido:A Executar": 1,
        "campo_faltante:c55+c56": 1,
    }
    assert resultado.status_nao_concluido == 1


def test_checklist_que_fecha_depois_vira_job_na_rodada_seguinte(
    db: Session, cfg: Settings
) -> None:
    """O caso que, tratado como definitivo, perderia 14,8% do volume em silêncio.

    Um `A Conferir` de hoje é `Concluído` amanhã. O ledger tem de reconsultar o
    ERP mesmo já conhecendo o formulário — o formulário não muda, o status sim.
    """
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("830", c) for c in ("c54", "c55", "c56")))
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {"830": _checklist("830", status="A Conferir")}
    servico = _servico(db, dropbox, sisloc, cfg)

    primeiro = servico.scan_and_ingest()
    assert primeiro.jobs_criados == 0

    # Rodada seguinte: nenhuma foto nova, mas alguém conferiu o checklist.
    dropbox.list_checklist_delta.return_value = DropboxDelta(cursor="cursor-3", images=[])
    sisloc.fetch_checklists.return_value = {"830": _checklist("830")}
    segundo = servico.scan_and_ingest()

    assert segundo.jobs_criados == 1
    assert db.get(ChecklistIngestState, "830").status == STATUS_MATERIALIZADO


def test_erp_e_reconsultado_mesmo_com_formulario_ja_no_ledger(
    db: Session, cfg: Settings
) -> None:
    """Cachear a linha congelaria um `A Conferir` como descarte permanente."""
    _com_cursor(db)
    db.add(
        ChecklistIngestState(
            checklist_id="840",
            campos="c54,c55,c56",
            formulario="F038",  # já conhecido de uma rodada anterior
            status=STATUS_PENDENTE,
            motivo="status_nao_concluido:A Conferir",
        )
    )
    db.commit()
    sisloc = _sisloc({"840": "F038"})

    resultado = _servico(db, _dropbox(), sisloc, cfg).scan_and_ingest()

    sisloc.fetch_checklists.assert_called_once()
    assert list(sisloc.fetch_checklists.call_args.args[0]) == ["840"]
    assert resultado.jobs_criados == 1


def test_job_nasce_com_o_enriquecimento_e_o_snapshot(db: Session, cfg: Settings) -> None:
    """Filtro e enriquecimento saem do MESMO ida-e-volta ao SQL Server."""
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("311773", c) for c in ("c54", "c55", "c56")))
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {
        "311773": _checklist(
            "311773",
            "F038 - PRÉ LOCAÇÃO DE GERADOR",
            filial="SP - SBC",
            patrimonio="TBRG00101",
            projeto="035514/2026-EBAZAR.COM.BR. LTDA",
            responsavel="FILIPE.VIEIRA",
            numero_om=104555,
            ordem=1,
        )
    }

    _servico(db, dropbox, sisloc, cfg).scan_and_ingest()

    job = db.query(PipelineJob).one()
    # Tipadas e indexadas: as três que a aplicação CONSULTA.
    assert job.formulario == "F038 - PRÉ LOCAÇÃO DE GERADOR"
    assert job.patrimonio == "TBRG00101"
    assert job.projeto == "035514/2026-EBAZAR.COM.BR. LTDA"  # BRUTO
    assert job.n_linhas == 1
    # O resto vive no snapshot, com o `projeto` já decomposto.
    snap = job.sisloc_snapshot
    assert snap["filial"] == "SP - SBC"
    assert snap["responsavel"] == "FILIPE.VIEIRA"
    assert snap["numero_om"] == 104555
    assert snap["projeto"]["cliente"] == "EBAZAR.COM.BR. LTDA"
    assert snap["projeto"]["contrato"] == "035514"
    assert snap["projeto"]["bruto"] == "035514/2026-EBAZAR.COM.BR. LTDA"
    assert snap["lido_em"]  # sem isso não se distingue leitura antes/depois de correção


def test_n_linhas_e_persistido_para_a_tela_avisar(db: Session, cfg: Settings) -> None:
    """78 checklists cobrem dois ativos. Sem o aviso, a tela nomeia o errado."""
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("300425", c) for c in ("c54", "c55", "c56")))
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {
        "300425": _checklist("300425", patrimonio="TECG00466A", ordem=5, n_linhas=4)
    }

    resultado = _servico(db, dropbox, sisloc, cfg).scan_and_ingest()

    assert resultado.multi_ativo == 1
    job = db.query(PipelineJob).one()
    assert job.n_linhas == 4
    assert job.patrimonio == "TECG00466A"  # o primeiro por `ordem`
    assert job.sisloc_snapshot["n_linhas"] == 4


def test_projeto_fora_do_padrao_preserva_o_bruto(db: Session, cfg: Settings) -> None:
    """0,03% não casa com `<contrato>/<ano>-<CLIENTE>`. O bruto não pode sumir."""
    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("850", c) for c in ("c54", "c55", "c56")))
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {
        "850": _checklist("850", projeto="CONTRATO ANTIGO SEM PADRAO")
    }

    _servico(db, dropbox, sisloc, cfg).scan_and_ingest()

    job = db.query(PipelineJob).one()
    assert job.projeto == "CONTRATO ANTIGO SEM PADRAO"
    projeto = job.sisloc_snapshot["projeto"]
    assert projeto["bruto"] == "CONTRATO ANTIGO SEM PADRAO"
    assert projeto["padrao_reconhecido"] is False
    assert projeto["cliente"] is None


def test_pastas_de_sistema_e_nomes_invalidos_ja_vem_filtrados(db: Session, cfg: Settings) -> None:
    """O filtro de `_pasta` vive no DropboxService; aqui só o contador chega."""
    _com_cursor(db)
    dropbox = _dropbox(_imagem("810", "c54"), ignorados=3)

    resultado = _servico(db, dropbox, _sisloc({"810": "F038"}), cfg).scan_and_ingest()

    assert resultado.nomes_ignorados == 3


# ── dedup / idempotência ──────────────────────────────────────────────────────


def test_rodar_duas_vezes_nao_duplica_job(db: Session, cfg: Settings) -> None:
    """Mesmo delta reapresentado (cursor não avançou no mock) → um único job."""
    _com_cursor(db)
    imagens = [_imagem("278749", c) for c in ("c54", "c55", "c56")]
    dropbox = _dropbox(*imagens)
    sisloc = _sisloc({"278749": "F038"})
    servico = _servico(db, dropbox, sisloc, cfg)

    primeiro = servico.scan_and_ingest()
    segundo = servico.scan_and_ingest()

    assert primeiro.jobs_criados == 1
    assert segundo.jobs_criados == 0
    assert db.query(PipelineJob).count() == 1


def test_checklist_ja_descartado_nao_e_reavaliado(db: Session, cfg: Settings) -> None:
    _com_cursor(db)
    imagens = [_imagem("600", c) for c in ("c54", "c55", "c56")]
    sisloc = _sisloc({"600": "F013"})
    servico = _servico(db, _dropbox(*imagens), sisloc, cfg)

    servico.scan_and_ingest()
    sisloc.fetch_checklists.reset_mock()
    segundo = servico.scan_and_ingest()

    assert segundo.candidatos == 0
    assert segundo.descartes == {}


def test_job_preexistente_bloqueia_novo_job(db: Session, cfg: Settings) -> None:
    """Diff contra pipeline_jobs: o que foi rodado à mão não reprocessa."""
    _com_cursor(db)
    job_manual = PipelineJob(id=uuid.uuid4(), checklist_id="909", status="done", mode="sync")
    db.add(job_manual)
    db.commit()

    dropbox = _dropbox(*(_imagem("909", c) for c in ("c54", "c55", "c56")))
    resultado = _servico(db, dropbox, _sisloc({"909": "F038"}), cfg).scan_and_ingest()

    assert resultado.jobs_criados == 0
    assert resultado.ja_processados == 1
    assert db.query(PipelineJob).count() == 1
    estado = db.get(ChecklistIngestState, "909")
    assert estado.status == STATUS_MATERIALIZADO
    assert estado.job_id == job_manual.id


def test_fotos_em_deltas_diferentes_acumulam_e_completam(db: Session, cfg: Settings) -> None:
    """O caso que quebra a implementação ingênua de delta puro."""
    _com_cursor(db)
    sisloc = _sisloc({"777": "F038"})
    dropbox = _dropbox(_imagem("777", "c54"))
    servico = _servico(db, dropbox, sisloc, cfg)

    primeiro = servico.scan_and_ingest()
    assert primeiro.jobs_criados == 0

    dropbox.list_checklist_delta.return_value = DropboxDelta(
        cursor="cursor-3", images=[_imagem("777", "c55"), _imagem("777", "c56")]
    )
    segundo = servico.scan_and_ingest()

    assert segundo.jobs_criados == 1
    assert db.get(ChecklistIngestState, "777").campos == "c54,c55,c56"


def test_pendente_e_reavaliado_mesmo_sem_foto_nova(db: Session, cfg: Settings) -> None:
    """A linha do ERP pode aparecer depois da foto — sem custo de Dropbox."""
    _com_cursor(db)
    sisloc = _sisloc({})  # ERP ainda não tem a linha
    dropbox = _dropbox(*(_imagem("888", c) for c in ("c54", "c55", "c56")))
    servico = _servico(db, dropbox, sisloc, cfg)

    assert servico.scan_and_ingest().descartes == {"formulario_ausente": 1}

    # Rodada seguinte: nenhum arquivo novo, mas a linha do ERP chegou.
    dropbox.list_checklist_delta.return_value = DropboxDelta(cursor="cursor-3", images=[])
    sisloc.fetch_checklists.return_value = {"888": _checklist("888")}
    segundo = servico.scan_and_ingest()

    assert segundo.jobs_criados == 1


def test_pendente_antigo_sai_da_janela_de_retry(db: Session, cfg: Settings) -> None:
    _com_cursor(db)
    antigo = datetime.now(UTC) - timedelta(days=30)
    db.add(
        ChecklistIngestState(
            checklist_id="404",
            campos="c54",
            status=STATUS_PENDENTE,
            first_seen_at=antigo,
            last_seen_at=antigo,
        )
    )
    db.commit()

    resultado = _servico(db, _dropbox(), _sisloc(), cfg).scan_and_ingest()

    assert resultado.candidatos == 0


# ── degradação ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "falha",
    [
        IntegrationError("HYT00 Login timeout expired"),
        ConfigurationError("credenciais do Sisloc ausentes"),
    ],
)
def test_sisloc_indisponivel_nao_avanca_cursor_nem_cria_job(
    db: Session, cfg: Settings, falha: Exception
) -> None:
    """VPN caída: a rodada seguinte relê o mesmo delta, nada se perde."""
    _com_cursor(db, "cursor-1")
    sisloc = _sisloc()
    sisloc.fetch_checklists.side_effect = falha
    dropbox = _dropbox(*(_imagem("278749", c) for c in ("c54", "c55", "c56")))

    resultado = _servico(db, dropbox, sisloc, cfg).scan_and_ingest()

    assert resultado.sisloc_indisponivel
    assert resultado.jobs_criados == 0
    assert db.query(PipelineJob).count() == 0
    assert db.get(IngestCursor, CURSOR_CHECKLISTS).cursor == "cursor-1"


def test_sisloc_volta_e_o_delta_relido_cria_o_job(db: Session, cfg: Settings) -> None:
    _com_cursor(db, "cursor-1")
    sisloc = _sisloc()
    sisloc.fetch_checklists.side_effect = IntegrationError("Login timeout expired")
    dropbox = _dropbox(*(_imagem("278749", c) for c in ("c54", "c55", "c56")))
    servico = _servico(db, dropbox, sisloc, cfg)

    servico.scan_and_ingest()
    sisloc.fetch_checklists.side_effect = None
    sisloc.fetch_checklists.return_value = {"278749": _checklist("278749")}
    segundo = servico.scan_and_ingest()

    assert segundo.jobs_criados == 1


# ── teto por rodada ───────────────────────────────────────────────────────────


def test_teto_por_rodada_adia_sem_perder_campos(db: Session, cfg: Settings) -> None:
    cfg = cfg.model_copy(update={"checklist_ingest_max_checklists": 2})
    _com_cursor(db)
    imagens = [_imagem(f"10{i}", c) for i in range(4) for c in ("c54", "c55", "c56")]
    sisloc = _sisloc({f"10{i}": "F038" for i in range(4)})
    servico = _servico(db, _dropbox(*imagens), sisloc, cfg)

    primeiro = servico.scan_and_ingest()
    assert primeiro.jobs_criados == 2
    assert primeiro.adiados == 2
    # Os campos dos adiados ficaram no ledger.
    assert db.get(ChecklistIngestState, "103").campos == "c54,c55,c56"

    servico.scan_and_ingest()
    assert db.query(PipelineJob).count() == 4


def test_corrida_entre_rodadas_sobrepostas_nao_duplica(
    db: Session, cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duas rodadas simultâneas: a PK do ledger é o que impede o job duplicado."""
    from sqlalchemy.exc import IntegrityError

    _com_cursor(db)
    dropbox = _dropbox(*(_imagem("999", c) for c in ("c54", "c55", "c56")))
    servico = _servico(db, dropbox, _sisloc({"999": "F038"}), cfg)

    original = db.flush
    chamadas = {"n": 0}

    def _flush_com_colisao(*args: Any, **kw: Any) -> None:
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))
        original(*args, **kw)

    monkeypatch.setattr(db, "flush", _flush_com_colisao)
    resultado = servico.scan_and_ingest()

    assert resultado.jobs_criados == 0
    assert db.query(PipelineJob).count() == 0
