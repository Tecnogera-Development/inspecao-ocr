"""Backfill por ``checklist_id`` — ticket mvp-c54-c57/11.

Tudo contra mock e SQLite em memória: nenhuma busca real no Dropbox, nenhuma
consulta ao SQL Server e **nenhuma chamada de LLM** (o backfill só materializa
jobs ``pending``; quem executa é o despacho da análise).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.core.exceptions import DomainError, IntegrationError
from app.db.base import Base
from app.models.dropbox import ImageMetadata
from app.models.ingest import (
    STATUS_DESCARTADO,
    STATUS_MATERIALIZADO,
    ChecklistIngestState,
)
from app.models.pipeline import PipelineJob
from app.models.sisloc import SislocChecklist
from app.services.checklist_backfill import (
    MOTIVO_BACKFILL,
    MOTIVO_SEM_IMAGENS,
    ChecklistBackfillService,
)
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


def _imagem(checklist_id: str, campo: str, *, quando: str = "15_02_2026 09_00_00") -> ImageMetadata:
    """ImageMetadata com o nome REAL do Sisloc, via parse_filename.

    A data default é deliberadamente **antiga** — anterior a qualquer marco de
    corte plausível. É o ponto do ticket: o backfill entra por id e o corte não
    se aplica.
    """
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


def _dropbox(por_id: dict[str, list[str]]) -> MagicMock:
    """Mock de Dropbox: ``{checklist_id: [campos]}``. Somente leitura."""
    mock = MagicMock()
    mock.list_checklist_images.side_effect = lambda cid: [
        _imagem(cid, campo) for campo in por_id.get(cid, [])
    ]
    return mock


def _checklist(
    codigo: str, formulario: str = "F038", *, status: str = "Concluído", **extra: Any
) -> SislocChecklist:
    return SislocChecklist(
        codigo_checklist=codigo, formulario=formulario, status=status, **extra
    )


def _sisloc(formularios: dict[str, str] | None = None) -> MagicMock:
    """Sisloc fake: ``{checklist_id: formulario}``, tudo ``Concluído``."""
    mock = MagicMock()
    mock.fetch_checklists.return_value = {
        cid: _checklist(cid, form) for cid, form in (formularios or {}).items()
    }
    return mock


def _servico(
    db: Session, dropbox: MagicMock, sisloc: MagicMock, cfg: Settings
) -> ChecklistBackfillService:
    return ChecklistBackfillService(db=db, dropbox=dropbox, sisloc=sisloc, settings=cfg)


# ── caminho feliz ─────────────────────────────────────────────────────────────


def test_id_valido_vira_job_ignorando_marco_de_corte(db: Session, cfg: Settings) -> None:
    """Foto de fevereiro, `CHECKLIST_INGEST_SINCE` em agosto: entra assim mesmo."""
    cfg = cfg.model_copy(update={"checklist_ingest_since": datetime(2026, 8, 1).date()})
    dropbox = _dropbox({"278749": ["c54", "c55", "c56"]})

    sisloc = _sisloc({"278749": "F038 - PRÉ LOCAÇÃO DE GERADOR"})
    resultado = _servico(db, dropbox, sisloc, cfg).backfill(["278749"])

    assert resultado.aceitos == 1
    item = resultado.itens[0]
    assert item.aceito and item.job_id is not None
    assert item.tentativa == 1
    assert item.reprocessamento is False
    assert item.vistas_para_analise == 3
    assert resultado.chamadas_visao_estimadas == 3

    job = db.get(PipelineJob, item.job_id)
    assert job is not None
    assert job.checklist_id == "278749"
    assert job.status == "pending"  # nasce pendente: nenhuma LLM foi chamada

    estado = db.get(ChecklistIngestState, "278749")
    assert estado is not None
    assert estado.status == STATUS_MATERIALIZADO
    assert estado.motivo == MOTIVO_BACKFILL
    assert estado.job_id == item.job_id


def test_c57_opcional_entra_quando_existe(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({"900": ["c54", "c55", "c56", "c57"]})
    resultado = _servico(db, dropbox, _sisloc({"900": "F038 - PRÉ LOCAÇÃO"}), cfg).backfill(["900"])

    assert resultado.aceitos == 1
    assert resultado.itens[0].vistas_para_analise == 4


def test_nao_toca_o_cursor_da_esteira(db: Session, cfg: Settings) -> None:
    """O backfill não lê nem escreve cursor: por isso ignora o marco de corte."""
    from app.models.ingest import CURSOR_CHECKLISTS, IngestCursor

    dropbox = _dropbox({"900": ["c54", "c55", "c56"]})
    _servico(db, dropbox, _sisloc({"900": "F038"}), cfg).backfill(["900"])

    assert db.get(IngestCursor, CURSOR_CHECKLISTS) is None
    dropbox.list_checklist_delta.assert_not_called()
    dropbox.latest_checklist_cursor.assert_not_called()


def test_uma_unica_consulta_em_lote_ao_sisloc(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({str(n): ["c54", "c55", "c56"] for n in range(900, 905)})
    sisloc = _sisloc({str(n): "F038" for n in range(900, 905)})

    resultado = _servico(db, dropbox, sisloc, cfg).backfill([str(n) for n in range(900, 905)])

    assert resultado.aceitos == 5
    sisloc.fetch_checklists.assert_called_once()


# ── recusas, cada uma com o motivo explícito ──────────────────────────────────


def test_campo_faltante_diz_qual_vista_falta(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({"901": ["c54", "c56"]})
    resultado = _servico(db, dropbox, _sisloc({"901": "F038"}), cfg).backfill(["901"])

    item = resultado.itens[0]
    assert not item.aceito
    assert item.motivo == "campo_faltante:c55"
    assert item.campos_faltantes == ("c55",)
    assert "c55 (lateral esquerda)" in item.detalhe
    assert "c54 (lateral direita)" in item.detalhe  # diz também o que TEM
    assert db.query(PipelineJob).count() == 0


def test_formulario_fora_da_whitelist_diz_qual_formulario(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({"902": ["c54", "c55", "c56", "c57"]})
    resultado = _servico(db, dropbox, _sisloc({"902": "F277-LIBERAÇÃO PLATAFORMA"}), cfg).backfill(
        ["902"]
    )

    item = resultado.itens[0]
    assert not item.aceito
    assert item.motivo == "formulario_fora_whitelist:F277"
    assert item.formulario == "F277-LIBERAÇÃO PLATAFORMA"
    assert "F277" in item.detalhe
    assert "F038" in item.detalhe  # qual é a whitelist (fonte única: FORMULARIOS_ALVO)
    assert db.query(PipelineJob).count() == 0


def test_f180_e_recusado_mesmo_por_backfill_manual(db: Session, cfg: Settings) -> None:
    """Corte para F038: o backfill reusa ``avaliar``, então herda o corte.

    Um operador não consegue reabrir o F180 pela porta de emergência do backfill
    — o mesmo ``FORMULARIOS_ALVO`` vale para os dois caminhos.
    """
    dropbox = _dropbox({"930": ["c54", "c55", "c56"]})
    resultado = _servico(
        db, dropbox, _sisloc({"930": "F180-VISITA GMG_REV04"}), cfg
    ).backfill(["930"])

    item = resultado.itens[0]
    assert not item.aceito
    assert item.motivo == "formulario_fora_whitelist:F180"
    assert db.query(PipelineJob).count() == 0


def test_formulario_truncado_casa_por_prefixo(db: Session, cfg: Settings) -> None:
    """`formulario` é varchar(30) e vem cortado — igualdade de string reprovaria."""
    dropbox = _dropbox({"903": ["c54", "c55", "c56"]})
    sisloc = _sisloc({"903": "F038 - PRÉ LOCAÇÃO DE GERADOR CORTAD"})
    resultado = _servico(db, dropbox, sisloc, cfg).backfill(["903"])

    assert resultado.aceitos == 1


def test_id_ausente_da_view_e_recusado_com_explicacao(db: Session, cfg: Settings) -> None:
    """~1,1% dos checklists nunca aparecem na view; não é atraso do ERP."""
    dropbox = _dropbox({"904": ["c54", "c55", "c56"]})
    resultado = _servico(db, dropbox, _sisloc({}), cfg).backfill(["904"])

    item = resultado.itens[0]
    assert not item.aceito
    assert item.motivo == "formulario_ausente"
    assert "dbo.checklist_produto" in item.detalhe
    assert "pipeline/run" in item.detalhe  # a saída de emergência é apontada
    assert db.query(PipelineJob).count() == 0


def test_formulario_vazio_e_distinto_de_ausente(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({"905": ["c54", "c55", "c56"]})
    resultado = _servico(db, dropbox, _sisloc({"905": ""}), cfg).backfill(["905"])

    assert resultado.itens[0].motivo == "formulario_vazio"


def test_id_sem_imagem_no_dropbox_nao_consulta_o_sisloc(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({})
    sisloc = _sisloc({})
    resultado = _servico(db, dropbox, sisloc, cfg).backfill(["999999"])

    item = resultado.itens[0]
    assert not item.aceito
    assert item.motivo == MOTIVO_SEM_IMAGENS
    assert "Dropbox" in item.detalhe
    sisloc.fetch_checklists.assert_not_called()


def test_recusa_nao_deixa_lixo_no_ledger(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({"906": ["c54"]})
    _servico(db, dropbox, _sisloc({"906": "F038"}), cfg).backfill(["906"])

    assert db.query(ChecklistIngestState).count() == 0
    assert db.query(PipelineJob).count() == 0


# ── o filtro novo não quebra o backfill (ticket 17) ───────────────────────────


def test_backfill_continua_funcionando_com_o_filtro_de_status(
    db: Session, cfg: Settings
) -> None:
    """Regressão: o recorte por status é do filtro, e o backfill reusa `avaliar`."""
    dropbox = _dropbox({"910": ["c54", "c55", "c56", "c57"]})
    resultado = _servico(db, dropbox, _sisloc({"910": "F038"}), cfg).backfill(["910"])

    assert resultado.aceitos == 1
    assert resultado.itens[0].vistas_para_analise == 4
    assert db.query(PipelineJob).count() == 1


def test_checklist_aberto_e_recusado_dizendo_o_que_fazer(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({"911": ["c54", "c55", "c56"]})
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {"911": _checklist("911", status="A Conferir")}

    resultado = _servico(db, dropbox, sisloc, cfg).backfill(["911"])

    item = resultado.itens[0]
    assert not item.aceito
    assert item.motivo == "status_nao_concluido:A Conferir"
    assert "A Conferir" in item.detalhe
    assert "esteira" in item.detalhe  # diz que não precisa fazer nada
    assert db.query(PipelineJob).count() == 0


def test_job_do_backfill_nasce_enriquecido_como_o_do_cron(
    db: Session, cfg: Settings
) -> None:
    """Fábrica única: um backfill que gravasse job mais pobre só apareceria na tela."""
    dropbox = _dropbox({"912": ["c54", "c55", "c56"]})
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {
        "912": _checklist(
            "912",
            "F038 - PRÉ LOCAÇÃO DE GERADOR",
            filial="MG - CGE",
            patrimonio="TECG01510",
            projeto="000000/2016-TECNOGERA",
            responsavel="MARCOS.PEREIRA",
        )
    }

    resultado = _servico(db, dropbox, sisloc, cfg).backfill(["912"])

    item = resultado.itens[0]
    assert item.patrimonio == "TECG01510"
    assert item.cliente == "TECNOGERA"
    job = db.get(PipelineJob, item.job_id)
    assert job.formulario == "F038 - PRÉ LOCAÇÃO DE GERADOR"
    assert job.patrimonio == "TECG01510"
    assert job.projeto == "000000/2016-TECNOGERA"
    assert job.sisloc_snapshot["filial"] == "MG - CGE"
    assert job.sisloc_snapshot["projeto"]["cliente"] == "TECNOGERA"


def test_multi_ativo_avisa_qual_patrimonio_recebeu_o_laudo(
    db: Session, cfg: Settings
) -> None:
    """Admite-se nomear um de N — mas nunca em silêncio."""
    dropbox = _dropbox({"300425": ["c54", "c55", "c56"]})
    sisloc = _sisloc()
    sisloc.fetch_checklists.return_value = {
        "300425": _checklist("300425", patrimonio="TECG00466A", n_linhas=4)
    }

    item = _servico(db, dropbox, sisloc, cfg).backfill(["300425"]).itens[0]

    assert item.aceito
    assert item.n_linhas == 4
    assert "4 linhas" in item.detalhe
    assert "TECG00466A" in item.detalhe
    assert db.get(PipelineJob, item.job_id).n_linhas == 4


# ── reprocessamento ───────────────────────────────────────────────────────────


def test_reprocessar_cria_execucao_nova_e_preserva_a_anterior(
    db: Session, cfg: Settings
) -> None:
    anterior = uuid.uuid4()
    db.add(PipelineJob(id=anterior, checklist_id="907", status="done", mode="sync"))
    db.commit()

    dropbox = _dropbox({"907": ["c54", "c55", "c56"]})
    resultado = _servico(db, dropbox, _sisloc({"907": "F038"}), cfg).backfill(["907"])

    item = resultado.itens[0]
    assert item.aceito
    assert item.reprocessamento is True
    assert item.tentativa == 2
    assert item.job_id != anterior

    # A execução anterior continua lá, intacta — é com ela que se compara.
    jobs = db.query(PipelineJob).filter(PipelineJob.checklist_id == "907").all()
    assert len(jobs) == 2
    assert db.get(PipelineJob, anterior).status == "done"


def test_ledger_descartado_nao_veta_o_backfill(db: Session, cfg: Settings) -> None:
    """`descartado` é a palavra final para o cron, não para um humano."""
    agora = datetime.now(UTC)
    db.add(
        ChecklistIngestState(
            checklist_id="908",
            campos="c54",
            formulario="F038",
            status=STATUS_DESCARTADO,
            motivo="campo_faltante:c55+c56",
            first_seen_at=agora,
            last_seen_at=agora,
        )
    )
    db.commit()

    dropbox = _dropbox({"908": ["c54", "c55", "c56"]})
    resultado = _servico(db, dropbox, _sisloc({"908": "F038"}), cfg).backfill(["908"])

    assert resultado.aceitos == 1
    estado = db.get(ChecklistIngestState, "908")
    assert estado.status == STATUS_MATERIALIZADO
    assert estado.motivo == MOTIVO_BACKFILL
    assert estado.campos_set == {"c54", "c55", "c56"}
    assert estado.job_id == resultado.itens[0].job_id


# ── guarda-corpo de lote ──────────────────────────────────────────────────────


def test_teto_de_lote_recusa_antes_de_tocar_qualquer_integracao(
    db: Session, cfg: Settings
) -> None:
    cfg = cfg.model_copy(update={"checklist_backfill_max_ids": 3})
    dropbox = _dropbox({})
    sisloc = _sisloc({})

    with pytest.raises(DomainError) as exc:
        _servico(db, dropbox, sisloc, cfg).backfill(["1", "2", "3", "4"])

    assert exc.value.status_code == 422
    assert "teto de 3" in exc.value.message
    assert exc.value.details == {"solicitados": 4, "teto": 3}
    dropbox.list_checklist_images.assert_not_called()
    sisloc.fetch_checklists.assert_not_called()


def test_teto_default_e_conservador(cfg: Settings) -> None:
    assert cfg.checklist_backfill_max_ids == 20


def test_teto_no_limite_passa(db: Session, cfg: Settings) -> None:
    cfg = cfg.model_copy(update={"checklist_backfill_max_ids": 2})
    dropbox = _dropbox({"910": ["c54", "c55", "c56"], "911": ["c54", "c55", "c56"]})
    resultado = _servico(db, dropbox, _sisloc({"910": "F038", "911": "F038"}), cfg).backfill(
        ["910", "911"]
    )

    assert resultado.aceitos == 2


def test_lista_vazia_e_recusada(db: Session, cfg: Settings) -> None:
    with pytest.raises(DomainError):
        _servico(db, _dropbox({}), _sisloc({}), cfg).backfill(["  ", ""])


def test_ids_repetidos_sao_deduplicados(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox({"912": ["c54", "c55", "c56"]})
    resultado = _servico(db, dropbox, _sisloc({"912": "F038"}), cfg).backfill(
        ["912", "912", " 912 "]
    )

    assert resultado.solicitados == 1
    assert resultado.duplicados_na_requisicao == 2
    assert db.query(PipelineJob).count() == 1


# ── degradação ────────────────────────────────────────────────────────────────


def test_sisloc_indisponivel_propaga_em_vez_de_falhar_calado(
    db: Session, cfg: Settings
) -> None:
    """Aqui não existe 'próxima rodada': quem pediu precisa saber que não rolou."""
    dropbox = _dropbox({"913": ["c54", "c55", "c56"]})
    sisloc = _sisloc({})
    sisloc.fetch_checklists.side_effect = IntegrationError("VPN caída")

    with pytest.raises(IntegrationError):
        _servico(db, dropbox, sisloc, cfg).backfill(["913"])

    assert db.query(PipelineJob).count() == 0


def test_lote_misto_reporta_cada_id_separadamente(db: Session, cfg: Settings) -> None:
    dropbox = _dropbox(
        {
            "920": ["c54", "c55", "c56"],  # aceito
            "921": ["c54"],  # campo faltante
            "922": ["c54", "c55", "c56"],  # fora da whitelist
            "923": ["c54", "c55", "c56"],  # ausente da view
        }
    )
    sisloc = _sisloc({"920": "F038", "921": "F038", "922": "F013"})

    resultado = _servico(db, dropbox, sisloc, cfg).backfill(["920", "921", "922", "923"])

    assert resultado.aceitos == 1
    assert resultado.recusados == 3
    motivos = {i.checklist_id: i.motivo for i in resultado.itens}
    assert motivos == {
        "920": None,
        "921": "campo_faltante:c55+c56",
        "922": "formulario_fora_whitelist:F013",
        "923": "formulario_ausente",
    }
    assert db.query(PipelineJob).count() == 1
    assert resultado.como_log()["aceitos"] == 1
