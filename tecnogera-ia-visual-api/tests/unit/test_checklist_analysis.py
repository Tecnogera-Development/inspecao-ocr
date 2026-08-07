"""Etapa de IA da esteira — ticket mvp-c54-c57/08.

Tudo roda contra ``FakeLLMProvider``/mock e SQLite em memória. **Nenhuma chave
real, nenhuma chamada de API** — a chave configurada é da conta do cliente.

Os casos que importam, na ordem em que quebram na prática: falha isolada por
vista, 3 vistas vs 4, `nao_processavel` pelos dois portões, o rollup pela pior
vista, e os três freios de gasto cortando o despacho.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import AppEnv, Settings
from app.db.base import Base
from app.models.checklist_analysis import (
    STATUS_ANALISADA,
    STATUS_FALHOU,
    STATUS_NAO_DESPACHADA,
    STATUS_NAO_PROCESSAVEL,
    ChecklistViewResult,
)
from app.models.dropbox import ImageMetadata
from app.models.pipeline import PipelineJob
from app.services.checklist_analysis import ChecklistAnalysisService, calcular_rollup
from app.services.dropbox import parse_filename
from app.services.llm_budget import (
    MOTIVO_DISPATCH_DESABILITADO,
    MOTIVO_ORCAMENTO_ESTOURADO,
    MOTIVO_TETO_DE_CHAMADAS,
)
from app.services.llm_provider import FakeLLMProvider
from app.services.view_inspection import Achado, InspecaoVista

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


def _cfg(**extra: Any) -> Settings:
    base: dict[str, Any] = {"llm_dispatch_enabled": True, "openai_api_key": "sk-teste"}
    base.update(extra)
    return Settings(_env_file=None, app_env=AppEnv.TEST, **base)


def _foto_ok(width: int = 960, height: int = 1280) -> bytes:
    """JPEG retrato nítido — o formato REAL da foto de campo do Sisloc."""
    img = Image.new("RGB", (width, height), color=(180, 180, 180))
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 4):
        draw.line([(x, 0), (x, height)], fill=(0, 0, 0), width=1)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _foto_degenerada() -> bytes:
    """Quadro preto chapado — o que a validação técnica calibrada barra."""
    buf = io.BytesIO()
    Image.new("RGB", (960, 1280), color=(2, 2, 2)).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _imagem(checklist_id: str, campo: str, *, quando: str = "01_08_2026 09_00_00") -> ImageMetadata:
    nome = f"153269005_checklist_{checklist_id}_{campo}_0_{quando}.jpeg"
    return ImageMetadata(
        dropbox_path=f"/Sisloc/MG - CGE/Checklist/{nome}",
        filename=nome,
        size_bytes=1234,
        parsed=parse_filename(nome),
        server_modified=datetime(2026, 8, 1, 9, 0, 0),
    )


def _sem_data(nome: str) -> ImageMetadata:
    """Imagem cujo nome não carrega data parseável (o Sisloc produz isso)."""
    from app.models.dropbox import ParsedFilename

    return ImageMetadata(
        dropbox_path=f"/Sisloc/{nome}",
        filename=nome,
        size_bytes=1,
        parsed=ParsedFilename(
            raw=nome,
            checklist_id="300001",
            field_name="c54",
            captured_at=None,
            extension=".jpeg",
        ),
    )


def _dropbox(
    campos: tuple[str, ...] = ("c54", "c55", "c56"),
    *,
    checklist_id: str = "300001",
    bytes_por_campo: dict[str, bytes] | None = None,
    extras: list[ImageMetadata] | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.list_checklist_images.return_value = [
        _imagem(checklist_id, c) for c in campos
    ] + (extras or [])
    conteudo = bytes_por_campo or {}

    def _download(path: str) -> bytes:
        for campo, dados in conteudo.items():
            if f"_{campo}_" in path:
                return dados
        return _foto_ok()

    mock.download_image.side_effect = _download
    return mock


class _ProviderSpy:
    """Provider controlável: laudo por campo, ou exceção."""

    def __init__(self, laudos: dict[str, InspecaoVista | Exception]) -> None:
        self._laudos = laudos
        self.chamadas: list[str] = []

    def inspect_view(self, image_bytes: bytes, campo: str) -> InspecaoVista:
        self.chamadas.append(campo)
        resposta = self._laudos.get(campo)
        if isinstance(resposta, Exception):
            raise resposta
        if resposta is None:
            return _laudo(campo)
        return resposta


def _laudo(
    campo: str,
    *,
    conformidade: str = "conforme",
    achados: list[Achado] | None = None,
    vista_confere: bool = True,
    motivo: str | None = None,
    in_tok: int = 4200,
    out_tok: int = 100,
) -> InspecaoVista:
    return InspecaoVista(
        campo=campo,
        processavel=conformidade != "nao_processavel",
        motivo_nao_processavel=motivo,
        conteudo_observado=f"conteudo de {campo}",
        vista_confere=vista_confere,
        conformidade=conformidade,  # type: ignore[arg-type]
        achados=achados or [],
        model_version="gpt-4.1-mini",
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


def _achado(severidade: int = 3, tipo: str = "corrosao_ferrugem", conf: float = 0.8) -> Achado:
    return Achado(
        classe="dano_visivel",
        tipo_defeito=tipo,
        severidade=severidade,
        local="quina superior",
        observacao="mancha laranja com textura",
        confianca=conf,
    )


def _job(db: Session, checklist_id: str = "300001", status: str = "pending") -> PipelineJob:
    job = PipelineJob(id=uuid.uuid4(), checklist_id=checklist_id, status=status, mode="sync")
    db.add(job)
    db.commit()
    return job


def _servico(
    db: Session,
    dropbox: MagicMock,
    provider: Any = None,
    cfg: Settings | None = None,
) -> ChecklistAnalysisService:
    return ChecklistAnalysisService(
        db=db,
        dropbox=dropbox,
        provider=provider or FakeLLMProvider(),
        settings=cfg or _cfg(),
    )


def _vistas(db: Session, job: PipelineJob) -> dict[str, ChecklistViewResult]:
    return {
        r.campo: r
        for r in db.query(ChecklistViewResult)
        .filter(ChecklistViewResult.job_id == job.id)
        .all()
    }


# ── caminho feliz ─────────────────────────────────────────────────────────────


def test_job_pendente_vira_done_com_uma_linha_por_vista(db: Session) -> None:
    job = _job(db)
    provider = _ProviderSpy({})

    resultado = _servico(db, _dropbox(), provider).dispatch_pending()

    db.refresh(job)
    assert job.status == "done"
    assert resultado.jobs_analisados == 1
    assert set(_vistas(db, job)) == {"c54", "c55", "c56"}
    assert provider.chamadas == ["c54", "c55", "c56"]


def test_uma_chamada_por_vista_nao_uma_com_todas(db: Session) -> None:
    """Decisão do ticket: achado atribuível à vista e falha isolada."""
    _job(db)
    provider = _ProviderSpy({})

    _servico(db, _dropbox(("c54", "c55", "c56", "c57")), provider).dispatch_pending()

    assert len(provider.chamadas) == 4


def test_custo_por_checklist_e_medido_e_persistido(db: Session) -> None:
    job = _job(db)
    provider = _ProviderSpy({})

    _servico(db, _dropbox(), provider).dispatch_pending()

    db.refresh(job)
    # 3 vistas × (4200 in × $0,40/MTok + 100 out × $1,60/MTok) = 3 × 0,00184
    assert job.llm_cost_usd == pytest.approx(0.00552, abs=1e-6)
    assert job.llm_calls == 3
    assert sum(v.cost_usd for v in _vistas(db, job).values()) == pytest.approx(0.00552, abs=1e-6)


def test_tokens_da_chamada_ficam_na_linha_da_vista(db: Session) -> None:
    job = _job(db)

    _servico(db, _dropbox(), _ProviderSpy({})).dispatch_pending()

    vista = _vistas(db, job)["c54"]
    assert vista.input_tokens == 4200
    assert vista.output_tokens == 100
    assert vista.model_version == "gpt-4.1-mini"


# ── 3 vistas vs 4 ─────────────────────────────────────────────────────────────


def test_tres_vistas_e_checklist_completo(db: Session) -> None:
    """`c57` ausente é NORMAL: o F180 não a emite desde set/2025."""
    job = _job(db)

    _servico(db, _dropbox(("c54", "c55", "c56"))).dispatch_pending()

    db.refresh(job)
    assert job.status == "done"
    assert job.vistas_recebidas == "c54,c55,c56"
    assert job.error is None
    assert "c57" not in _vistas(db, job)


def test_quatro_vistas_quando_a_c57_existe(db: Session) -> None:
    job = _job(db)

    _servico(db, _dropbox(("c54", "c55", "c56", "c57"))).dispatch_pending()

    db.refresh(job)
    assert job.vistas_recebidas == "c54,c55,c56,c57"
    assert len(_vistas(db, job)) == 4


def test_c57_ausente_nao_vira_pendencia_nem_achado(db: Session) -> None:
    job = _job(db)

    _servico(db, _dropbox(("c54", "c55", "c56"))).dispatch_pending()

    db.refresh(job)
    assert job.conformidade == "conforme"
    assert job.severidade_max is None
    assert job.vista_determinante is None


def test_vista_obrigatoria_ausente_reprova_o_job(db: Session) -> None:
    job = _job(db)

    _servico(db, _dropbox(("c54", "c56"))).dispatch_pending()

    db.refresh(job)
    assert job.status == "failed"
    assert job.error == "vistas_ausentes:c55"


def test_campo_fora_das_vistas_do_mvp_e_ignorado(db: Session) -> None:
    """`c0`, `c53`, `c145`… existem no mesmo checklist e não custam token."""
    job = _job(db)
    provider = _ProviderSpy({})
    dropbox = _dropbox(("c54", "c55", "c56"), extras=[_imagem("300001", "c0")])

    _servico(db, dropbox, provider).dispatch_pending()

    assert "c0" not in provider.chamadas
    assert "c0" not in _vistas(db, job)


def test_refoto_do_mesmo_campo_usa_a_mais_recente(db: Session) -> None:
    """O Sisloc aceita refoto; analisar as duas dobraria o custo por nada."""
    _job(db)
    provider = _ProviderSpy({})
    dropbox = _dropbox(
        ("c54", "c55", "c56"),
        extras=[_imagem("300001", "c54", quando="01_08_2026 17_30_00")],
    )

    _servico(db, dropbox, provider).dispatch_pending()

    assert provider.chamadas.count("c54") == 1
    caminhos = [c.args[0] for c in dropbox.download_image.call_args_list]
    assert any("17_30_00" in p for p in caminhos)


def test_refoto_mais_antiga_listada_depois_nao_ganha(db: Session) -> None:
    """A ordem em que o Dropbox devolve não é garantia de nada."""
    _job(db)
    dropbox = _dropbox(
        ("c55", "c56"),
        extras=[
            _imagem("300001", "c54", quando="01_08_2026 17_30_00"),
            _imagem("300001", "c54", quando="01_08_2026 08_00_00"),
        ],
    )

    _servico(db, dropbox).dispatch_pending()

    caminhos = [c.args[0] for c in dropbox.download_image.call_args_list]
    assert any("17_30_00" in p for p in caminhos)
    assert not any("08_00_00" in p for p in caminhos)


def test_desempate_de_refoto_sem_data_usa_o_nome() -> None:
    """``parse_filename`` devolve ``captured_at=None`` para data inválida.

    Acontece de verdade: o Sisloc já gravou ``31_02_2026`` no nome do arquivo.
    Sem desempate estável, duas rodadas escolheriam fotos diferentes.
    """
    from app.services.checklist_analysis import _mais_recente

    com_data = _imagem("300001", "c54")
    sem_data = _sem_data("a_primeira.jpeg")
    outra_sem_data = _sem_data("z_ultima.jpeg")

    assert _mais_recente(com_data, sem_data) is True
    assert _mais_recente(sem_data, com_data) is False
    assert _mais_recente(outra_sem_data, sem_data) is True
    assert _mais_recente(sem_data, outra_sem_data) is False


# ── falha isolada ─────────────────────────────────────────────────────────────


def test_vista_que_falha_no_llm_nao_derruba_as_outras(db: Session) -> None:
    job = _job(db)
    provider = _ProviderSpy({"c55": RuntimeError("timeout da OpenAI")})

    resultado = _servico(db, _dropbox(), provider).dispatch_pending()

    db.refresh(job)
    assert job.status == "done"
    vistas = _vistas(db, job)
    assert vistas["c55"].status == STATUS_FALHOU
    assert "timeout da OpenAI" in (vistas["c55"].error or "")
    assert vistas["c54"].status == STATUS_ANALISADA
    assert vistas["c56"].status == STATUS_ANALISADA
    assert resultado.vistas_falhadas == 1
    assert resultado.vistas_analisadas == 2


def test_vista_que_falha_no_download_nao_derruba_as_outras(db: Session) -> None:
    job = _job(db)
    dropbox = _dropbox()
    original = dropbox.download_image.side_effect

    def _falha_no_c56(path: str) -> bytes:
        if "_c56_" in path:
            raise OSError("404 no Dropbox")
        return original(path)

    dropbox.download_image.side_effect = _falha_no_c56

    _servico(db, dropbox).dispatch_pending()

    db.refresh(job)
    assert job.status == "done"
    assert _vistas(db, job)["c56"].status == STATUS_FALHOU
    assert job.vistas_recebidas == "c54,c55"


def test_job_falha_so_quando_nenhuma_vista_produz_laudo(db: Session) -> None:
    job = _job(db)
    provider = _ProviderSpy({c: RuntimeError("boom") for c in ("c54", "c55", "c56")})

    resultado = _servico(db, _dropbox(), provider).dispatch_pending()

    db.refresh(job)
    assert job.status == "failed"
    assert job.error == "nenhuma_vista_produziu_laudo"
    assert resultado.jobs_falhados == 1


def test_dropbox_fora_do_ar_marca_o_job_sem_derrubar_a_rodada(db: Session) -> None:
    job = _job(db)
    dropbox = MagicMock()
    dropbox.list_checklist_images.side_effect = OSError("conexão recusada")

    resultado = _servico(db, dropbox).dispatch_pending()

    db.refresh(job)
    assert job.status == "failed"
    assert "dropbox_indisponivel" in (job.error or "")
    assert resultado.jobs_falhados == 1


# ── nao_processavel: os dois portões ──────────────────────────────────────────


def test_validacao_tecnica_barra_sem_gastar_token(db: Session) -> None:
    """Quadro degenerado não merece chamada: é o primeiro portão."""
    job = _job(db)
    provider = _ProviderSpy({})
    dropbox = _dropbox(bytes_por_campo={"c55": _foto_degenerada()})

    resultado = _servico(db, dropbox, provider).dispatch_pending()

    assert "c55" not in provider.chamadas
    vista = _vistas(db, job)["c55"]
    assert vista.status == STATUS_NAO_PROCESSAVEL
    assert vista.cost_usd == 0.0
    assert vista.model_version == "validacao_tecnica"
    assert resultado.vistas_nao_processaveis == 1


def test_foto_retrato_do_sisloc_passa_na_validacao_tecnica(db: Session) -> None:
    """720×1280 é 79% do parque real; reprová-la zeraria a esteira."""
    job = _job(db)
    dropbox = _dropbox(bytes_por_campo={"c54": _foto_ok(720, 1280)})

    _servico(db, dropbox).dispatch_pending()

    assert _vistas(db, job)["c54"].status == STATUS_ANALISADA


def test_modelo_pode_devolver_nao_processavel_por_conta_propria(db: Session) -> None:
    """Nitidez não basta: contraluz severo passa no Laplacian e é injulgável."""
    job = _job(db)
    provider = _ProviderSpy(
        {"c56": _laudo("c56", conformidade="nao_processavel", motivo="foto_estourada")}
    )

    resultado = _servico(db, _dropbox(), provider).dispatch_pending()

    vista = _vistas(db, job)["c56"]
    assert vista.status == STATUS_NAO_PROCESSAVEL
    assert vista.motivo_nao_processavel == "foto_estourada"
    assert vista.cost_usd > 0  # a chamada aconteceu — o custo é real
    assert resultado.vistas_nao_processaveis == 1
    db.refresh(job)
    assert job.conformidade == "nao_processavel"
    assert job.vista_determinante == "c56"


# ── rollup ────────────────────────────────────────────────────────────────────


def test_rollup_e_a_pior_vista_com_registro_de_qual_foi(db: Session) -> None:
    job = _job(db)
    provider = _ProviderSpy(
        {
            "c54": _laudo("c54", conformidade="nao_conforme", achados=[_achado(severidade=3)]),
            "c55": _laudo("c55", conformidade="nao_conforme", achados=[_achado(severidade=1)]),
            "c56": _laudo("c56"),
        }
    )

    _servico(db, _dropbox(), provider).dispatch_pending()

    db.refresh(job)
    assert job.conformidade == "nao_conforme"
    assert job.severidade_max == 1
    assert job.vista_determinante == "c55"


def test_rollup_nao_conforme_domina_nao_processavel() -> None:
    """Defeito visto vale mais que a incerteza de outra vista."""
    rollup = calcular_rollup(
        {
            "c54": _laudo("c54", conformidade="nao_processavel", motivo="obstrucao"),
            "c55": _laudo("c55", conformidade="nao_conforme", achados=[_achado(severidade=4)]),
        }
    )

    assert rollup.conformidade == "nao_conforme"
    assert rollup.vista_determinante == "c55"


def test_rollup_nao_processavel_domina_conforme() -> None:
    """'Está tudo bem' apoiado em foto ilegível não é 'está tudo bem'."""
    rollup = calcular_rollup(
        {
            "c54": _laudo("c54"),
            "c56": _laudo("c56", conformidade="nao_processavel", motivo="foto_escura"),
        }
    )

    assert rollup.conformidade == "nao_processavel"
    assert rollup.vista_determinante == "c56"


def test_rollup_conforme_nao_aponta_vista_determinante() -> None:
    """Apontar uma vista treinaria o operador a procurar erro onde não há."""
    rollup = calcular_rollup({"c54": _laudo("c54"), "c55": _laudo("c55")})

    assert rollup.conformidade == "conforme"
    assert rollup.vista_determinante is None
    assert rollup.severidade_max is None


def test_rollup_desempata_por_confianca_e_depois_pela_ordem_das_vistas() -> None:
    rollup = calcular_rollup(
        {
            "c55": _laudo("c55", conformidade="nao_conforme", achados=[_achado(2, conf=0.5)]),
            "c54": _laudo("c54", conformidade="nao_conforme", achados=[_achado(2, conf=0.9)]),
        }
    )

    assert rollup.vista_determinante == "c54"


def test_rollup_de_conjunto_vazio_nao_explode() -> None:
    assert calcular_rollup({}).conformidade == "nao_processavel"


def test_vista_confere_falso_vai_para_as_metricas(db: Session) -> None:
    """Métrica de alarme do dicionário de campos, não veredito do equipamento."""
    job = _job(db)
    provider = _ProviderSpy({"c55": _laudo("c55", vista_confere=False)})

    _servico(db, _dropbox(), provider).dispatch_pending()

    db.refresh(job)
    assert job.metrics is not None
    assert job.metrics["vista_confere_falso"] == ["c55"]
    assert job.conformidade == "conforme"  # não é reprovação


# ── freios de gasto ───────────────────────────────────────────────────────────


def test_kill_switch_desligado_deixa_tudo_pending(db: Session) -> None:
    job = _job(db)
    provider = _ProviderSpy({})

    resultado = _servico(
        db, _dropbox(), provider, _cfg(llm_dispatch_enabled=False)
    ).dispatch_pending()

    db.refresh(job)
    assert job.status == "pending"
    assert provider.chamadas == []
    assert resultado.motivo_de_parada == MOTIVO_DISPATCH_DESABILITADO
    assert resultado.fila_restante == 1


def test_teto_de_chamadas_para_a_rodada_e_deixa_o_resto_na_fila(db: Session) -> None:
    _job(db, "300001")
    _job(db, "300002")
    provider = _ProviderSpy({})

    resultado = _servico(
        db, _dropbox(), provider, _cfg(llm_max_calls_per_run=2)
    ).dispatch_pending()

    assert len(provider.chamadas) == 2
    assert resultado.motivo_de_parada == MOTIVO_TETO_DE_CHAMADAS
    assert resultado.fila_restante == 2  # nenhum checklist fechou


def test_checklist_cortado_no_meio_volta_para_pending(db: Session) -> None:
    """Rollup sobre meio checklist seria pior que nenhum — e invisível na tela."""
    job = _job(db)
    provider = _ProviderSpy({})

    _servico(db, _dropbox(), provider, _cfg(llm_max_calls_per_run=2)).dispatch_pending()

    db.refresh(job)
    assert job.status == "pending"
    assert job.conformidade is None
    assert job.started_at is None
    assert _vistas(db, job)["c56"].status == STATUS_NAO_DESPACHADA


def test_custo_ja_gasto_e_contabilizado_mesmo_com_o_job_adiado(db: Session) -> None:
    job = _job(db)

    _servico(db, _dropbox(), _ProviderSpy({}), _cfg(llm_max_calls_per_run=2)).dispatch_pending()

    db.refresh(job)
    assert job.llm_calls == 2
    assert job.llm_cost_usd > 0


def test_orcamento_estourado_nao_despacha_nada(db: Session) -> None:
    job = _job(db)
    db.add(
        ChecklistViewResult(
            id=uuid.uuid4(),
            job_id=job.id,
            checklist_id=job.checklist_id,
            campo="c99",
            status=STATUS_ANALISADA,
            cost_usd=99.0,
        )
    )
    db.commit()
    provider = _ProviderSpy({})

    resultado = _servico(
        db, _dropbox(), provider, _cfg(llm_monthly_budget_usd=25.0)
    ).dispatch_pending()

    db.refresh(job)
    assert provider.chamadas == []
    assert job.status == "pending"
    assert resultado.motivo_de_parada == MOTIVO_ORCAMENTO_ESTOURADO


def test_provider_fake_em_producao_nao_despacha(db: Session) -> None:
    """Segunda linha de defesa: o Settings já recusa o boot sem chave."""
    job = _job(db)
    cfg = Settings(
        _env_file=None,
        app_env=AppEnv.STAGING,
        llm_dispatch_enabled=True,
    )
    provider = _ProviderSpy({})

    resultado = ChecklistAnalysisService(
        db=db, dropbox=_dropbox(), provider=provider, settings=cfg
    ).dispatch_pending()

    db.refresh(job)
    assert provider.chamadas == []
    assert job.status == "pending"
    assert resultado.motivo_de_parada == "provider_fake_fora_de_desenvolvimento"


def test_teto_de_jobs_por_rodada_limita_o_lote(db: Session) -> None:
    for i in range(5):
        _job(db, f"30000{i}")

    resultado = _servico(
        db, _dropbox(), _ProviderSpy({}), _cfg(checklist_analysis_max_jobs_per_run=2)
    ).dispatch_pending()

    assert resultado.jobs_vistos == 2
    assert resultado.fila_restante == 3


# ── seleção de jobs ───────────────────────────────────────────────────────────


def test_job_ja_processado_nao_e_reprocessado(db: Session) -> None:
    job = _job(db, status="done")
    provider = _ProviderSpy({})

    resultado = _servico(db, _dropbox(), provider).dispatch_pending()

    assert provider.chamadas == []
    assert resultado.jobs_vistos == 0
    db.refresh(job)
    assert job.status == "done"


def test_job_em_modo_batch_nao_entra_na_esteira_sincrona(db: Session) -> None:
    job = _job(db)
    job.mode = "batch"
    db.commit()

    resultado = _servico(db, _dropbox()).dispatch_pending()

    assert resultado.jobs_vistos == 0


def test_reprocessar_o_mesmo_job_sobrescreve_a_linha_da_vista(db: Session) -> None:
    job = _job(db)
    _servico(db, _dropbox()).dispatch_pending()
    job.status = "pending"
    db.commit()

    _servico(db, _dropbox()).dispatch_pending()

    assert len(_vistas(db, job)) == 3  # não duplicou


# ── provider fake ─────────────────────────────────────────────────────────────


def test_fake_provider_produz_laudo_conforme_sem_chave(db: Session) -> None:
    job = _job(db)

    _servico(db, _dropbox(), FakeLLMProvider()).dispatch_pending()

    db.refresh(job)
    assert job.status == "done"
    assert job.conformidade == "conforme"
    assert _vistas(db, job)["c54"].model_version == "fake-inspecao-1.0"
