"""Etapa de IA da esteira: consome os ``pipeline_jobs`` — ticket ``mvp-c54-c57/08``.

O ticket 07 materializa jobs ``pending`` e para ali; **ninguém os consumia**.
Este módulo é o elo que faltava: pega os pendentes, baixa as vistas do Dropbox
(somente leitura), valida tecnicamente, chama o modelo **uma vez por vista** e
persiste laudo por vista + rollup do checklist.

Decisões que a implementação impõe
----------------------------------

**Uma chamada por vista, não as 3–4 juntas.** Custa o mesmo em tokens de imagem
e compra duas coisas: achado atribuível à vista (a tela do ticket 09 mostra
"ferrugem na `c54`", não "ferrugem em algum lugar") e falha isolada — uma vista
que estoura timeout não derruba as outras. É o formato que o ticket 15 validou.

**Vista que falha grava a própria linha.** Toda vista roda dentro do seu
``try``; o erro vira ``status='falhou'`` naquela linha e o loop segue. Um job só
vira ``failed`` inteiro quando **nenhuma** vista produziu resultado — porque aí
não há nada para o operador julgar.

**Freio de gasto antes de cada chamada, não depois.** ``LLMBudgetGuard`` decide
por vista. Quando o freio corta no meio de um checklist, o job volta para
``pending``: meio checklist analisado é pior que nenhum, porque o rollup sairia
de uma amostra parcial e o operador não teria como saber.

**`c57` ausente é NORMAL.** O F180 não a emite desde set/2025. Três vistas é
checklist completo: não é `componente_ausente`, não é pendência, não reduz
completude, e não aparece como buraco na tela.

**Validação técnica antes da IA economiza chamada.** Quadro degenerado (preto,
lente tapada) vira ``nao_processavel`` sem gastar token. Mas o portão técnico é
só o piso — nitidez não basta como porteiro (o `c57` de 278154 é nítido e
inútil por contraluz), então o modelo também pode devolver ``nao_processavel``.

**Dropbox é somente leitura.** ``list_checklist_images`` + ``download_image``,
nada mais. Nenhuma escrita, nenhum delete, jamais.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.checklist_analysis import (
    STATUS_ANALISADA,
    STATUS_FALHOU,
    STATUS_NAO_DESPACHADA,
    STATUS_NAO_PROCESSAVEL,
    ChecklistViewResult,
)
from app.models.pipeline import PipelineJob
from app.services.checklist_filter import CAMPOS_OBRIGATORIOS
from app.services.cost_calculator import compute_cost
from app.services.event_validation import EventValidationService
from app.services.llm_budget import LLMBudgetGuard
from app.services.view_inspection import (
    CAMPOS_VISTA,
    MOTIVO_POR_VALIDACAO,
    InspecaoVista,
    inspecao_nao_processavel,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.dropbox import ImageMetadata
    from app.services.dropbox import DropboxService

_log = get_logger(__name__)

#: Ordem de severidade da conformidade de uma vista — usada no rollup.
#: ``nao_conforme`` domina ``nao_processavel``: um defeito visto numa vista vale
#: mais que a incerteza de outra. ``nao_processavel`` domina ``conforme``: um
#: "está tudo bem" apoiado em foto ilegível não é um "está tudo bem".
_ORDEM_CONFORMIDADE: dict[str, int] = {
    "nao_conforme": 0,
    "nao_processavel": 1,
    "conforme": 2,
}


@dataclass
class ChecklistAnalysisResult:
    """O que uma rodada de análise fez — e por que parou, se parou."""

    jobs_vistos: int = 0
    jobs_analisados: int = 0
    jobs_falhados: int = 0
    jobs_adiados: int = 0
    vistas_analisadas: int = 0
    vistas_nao_processaveis: int = 0
    vistas_falhadas: int = 0
    chamadas_llm: int = 0
    custo_usd: float = 0.0
    gasto_no_mes_usd: float = 0.0
    fila_restante: int = 0
    motivo_de_parada: str | None = None
    conformidades: Counter[str] = field(default_factory=Counter)

    def como_log(self) -> dict[str, Any]:
        return {
            "jobs_vistos": self.jobs_vistos,
            "jobs_analisados": self.jobs_analisados,
            "jobs_falhados": self.jobs_falhados,
            "jobs_adiados": self.jobs_adiados,
            "vistas_analisadas": self.vistas_analisadas,
            "vistas_nao_processaveis": self.vistas_nao_processaveis,
            "vistas_falhadas": self.vistas_falhadas,
            "chamadas_llm": self.chamadas_llm,
            "custo_usd": round(self.custo_usd, 6),
            "gasto_no_mes_usd": round(self.gasto_no_mes_usd, 6),
            "fila_restante": self.fila_restante,
            "motivo_de_parada": self.motivo_de_parada,
            "conformidades": dict(self.conformidades),
        }


@dataclass(frozen=True, slots=True)
class Rollup:
    """Rollup do checklist: a pior vista, e qual foi."""

    conformidade: str
    severidade_max: int | None
    vista_determinante: str | None


def calcular_rollup(vistas: dict[str, InspecaoVista]) -> Rollup:
    """Rollup = pior vista, com registro de qual vista determinou.

    "Pior" é ``nao_conforme`` > ``nao_processavel`` > ``conforme``; dentro de
    ``nao_conforme``, a severidade mais crítica (1 é o pior), desempatada pela
    maior confiança e depois pela ordem canônica das vistas — determinístico,
    porque a tela do operador não pode mudar de veredito entre dois renders.

    ``vista_determinante`` é ``None`` quando tudo está conforme: não há vista a
    culpar, e apontar uma treinaria o operador a procurar erro onde não há.
    """
    if not vistas:
        return Rollup(conformidade="nao_processavel", severidade_max=None, vista_determinante=None)

    ordenadas = sorted(
        vistas.values(),
        key=lambda v: (
            _ORDEM_CONFORMIDADE.get(v.conformidade, 3),
            v.severidade_max if v.severidade_max is not None else 9,
            -(v.achado_principal.confianca if v.achado_principal else 0.0),
            CAMPOS_VISTA.index(v.campo) if v.campo in CAMPOS_VISTA else 99,
        ),
    )
    pior = ordenadas[0]
    if pior.conformidade == "conforme":
        return Rollup(conformidade="conforme", severidade_max=None, vista_determinante=None)
    return Rollup(
        conformidade=pior.conformidade,
        severidade_max=pior.severidade_max,
        vista_determinante=pior.campo,
    )


class ChecklistAnalysisService:
    """Despacha os ``pipeline_jobs`` pendentes e roda a inspeção por vista."""

    def __init__(
        self,
        db: Session,
        dropbox: DropboxService,
        provider: Any,  # noqa: ANN401 — protocolo estrutural (inspect_view)
        settings: Settings | None = None,
        validator: EventValidationService | None = None,
        guard: LLMBudgetGuard | None = None,
    ) -> None:
        self._db = db
        self._dropbox = dropbox
        self._provider = provider
        self._settings = settings or get_settings()
        self._validator = validator or EventValidationService()
        self._guard = guard or LLMBudgetGuard(db, self._settings)

    # ── entrada ──────────────────────────────────────────────────────────────

    def dispatch_pending(self) -> ChecklistAnalysisResult:
        """Uma rodada: pega jobs ``pending``, respeita os freios, processa.

        Nunca levanta por indisponibilidade externa — o cron não pode derrubar
        o worker por causa de um Dropbox intermitente.
        """
        resultado = ChecklistAnalysisResult()

        decisao = self._guard.avaliar_rodada()
        if not decisao:
            resultado.motivo_de_parada = decisao.motivo
            resultado.fila_restante = self._contar_pendentes()
            _log.warning(
                "checklist_analysis_nao_despachou",
                motivo=decisao.motivo,
                fila_restante=resultado.fila_restante,
            )
            return resultado

        jobs = self._proximos_jobs()
        resultado.jobs_vistos = len(jobs)

        for job in jobs:
            if self._guard.motivo_de_parada is not None:
                resultado.jobs_adiados += 1
                continue
            self._analisar_job(job, resultado)

        resultado.chamadas_llm = self._guard.chamadas
        resultado.custo_usd = self._guard.custo_da_rodada
        resultado.gasto_no_mes_usd = self._guard.gasto_no_mes
        resultado.motivo_de_parada = self._guard.motivo_de_parada
        resultado.fila_restante = self._contar_pendentes()

        if resultado.fila_restante:
            _log.info(
                "checklist_analysis_fila_restante",
                fila_restante=resultado.fila_restante,
                motivo=resultado.motivo_de_parada,
            )
        return resultado

    # ── por job ──────────────────────────────────────────────────────────────

    def _analisar_job(self, job: PipelineJob, resultado: ChecklistAnalysisResult) -> None:
        """Processa um checklist inteiro. Nunca propaga exceção."""
        job.status = "running"
        job.started_at = datetime.now(UTC)
        self._db.commit()

        try:
            vistas_disponiveis = self._localizar_vistas(job.checklist_id)
        except Exception as exc:  # noqa: BLE001 — Dropbox fora não derruba a rodada
            self._marcar_falha(job, f"dropbox_indisponivel: {exc}")
            resultado.jobs_falhados += 1
            _log.warning(
                "checklist_analysis_dropbox_falhou",
                checklist_id=job.checklist_id,
                error=str(exc),
            )
            return

        faltantes = [c for c in CAMPOS_OBRIGATORIOS if c not in vistas_disponiveis]
        if faltantes:
            # O filtro do ticket 07 já aprovou por campo visto no Dropbox; se a
            # foto sumiu entre a ingestão e agora, é fato novo, não bug do filtro.
            self._marcar_falha(job, f"vistas_ausentes:{'+'.join(faltantes)}")
            resultado.jobs_falhados += 1
            _log.warning(
                "checklist_analysis_vistas_ausentes",
                checklist_id=job.checklist_id,
                faltantes=faltantes,
            )
            return

        laudos: dict[str, InspecaoVista] = {}
        custo_do_job = 0.0
        chamadas_do_job = 0
        cortado = False

        for campo in CAMPOS_VISTA:
            imagem = vistas_disponiveis.get(campo)
            if imagem is None:
                continue  # `c57` ausente é o caso NORMAL — nada a registrar
            linha, laudo, custo, chamou = self._analisar_vista(job, campo, imagem)
            self._persistir_vista(linha)
            if linha.status == STATUS_NAO_DESPACHADA:
                cortado = True
                break
            custo_do_job += custo
            chamadas_do_job += 1 if chamou else 0
            if laudo is not None:
                laudos[campo] = laudo
                if laudo.conformidade == "nao_processavel":
                    resultado.vistas_nao_processaveis += 1
                else:
                    resultado.vistas_analisadas += 1
            else:
                resultado.vistas_falhadas += 1

        if cortado:
            # Freio cortou no meio: devolve o job à fila em vez de fechar um
            # rollup sobre amostra parcial. As vistas já gravadas são reusadas
            # na próxima rodada? Não — a linha é sobrescrita por (job, campo).
            job.status = "pending"
            job.started_at = None
            job.llm_cost_usd = round(job.llm_cost_usd + custo_do_job, 6)
            job.llm_calls += chamadas_do_job
            self._db.commit()
            resultado.jobs_adiados += 1
            _log.info(
                "checklist_analysis_job_adiado",
                checklist_id=job.checklist_id,
                motivo=self._guard.motivo_de_parada,
            )
            return

        if not laudos:
            self._marcar_falha(job, "nenhuma_vista_produziu_laudo")
            resultado.jobs_falhados += 1
            return

        rollup = calcular_rollup(laudos)
        job.conformidade = rollup.conformidade
        job.severidade_max = rollup.severidade_max
        job.vista_determinante = rollup.vista_determinante
        job.vistas_recebidas = ",".join(sorted(laudos))
        job.llm_cost_usd = round(job.llm_cost_usd + custo_do_job, 6)
        job.llm_calls += chamadas_do_job
        job.metrics = {
            **(job.metrics or {}),
            "vistas": len(laudos),
            "vistas_recebidas": sorted(laudos),
            "custo_usd": round(custo_do_job, 6),
            "chamadas_llm": chamadas_do_job,
            "vista_confere_falso": sorted(
                c for c, laudo in laudos.items() if not laudo.vista_confere
            ),
        }
        job.status = "done"
        job.error = None
        job.finished_at = datetime.now(UTC)
        self._db.commit()

        resultado.jobs_analisados += 1
        resultado.conformidades[rollup.conformidade] += 1
        _log.info(
            "checklist_analisado",
            checklist_id=job.checklist_id,
            job_id=str(job.id),
            conformidade=rollup.conformidade,
            severidade_max=rollup.severidade_max,
            vista_determinante=rollup.vista_determinante,
            vistas=job.vistas_recebidas,
            custo_usd=round(custo_do_job, 6),
        )

    # ── por vista ────────────────────────────────────────────────────────────

    def _analisar_vista(
        self, job: PipelineJob, campo: str, imagem: ImageMetadata
    ) -> tuple[ChecklistViewResult, InspecaoVista | None, float, bool]:
        """Uma vista, isolada. Devolve (linha, laudo, custo, chamou_llm).

        Nenhuma exceção escapa: falha aqui vira ``status='falhou'`` naquela
        linha e as outras vistas seguem — é o requisito de isolamento.
        """
        linha = ChecklistViewResult(
            id=uuid.uuid4(),
            job_id=job.id,
            checklist_id=job.checklist_id,
            campo=campo,
            dropbox_path=imagem.dropbox_path,
            status=STATUS_FALHOU,
        )

        try:
            image_bytes = self._dropbox.download_image(imagem.dropbox_path)
        except Exception as exc:  # noqa: BLE001
            linha.error = f"download: {exc}"
            _log.warning(
                "vista_download_falhou",
                checklist_id=job.checklist_id,
                campo=campo,
                error=str(exc),
            )
            return linha, None, 0.0, False

        validacao = self._validator.validate_technical(image_bytes)
        if not validacao.processable:
            # Quadro degenerado: reprovado sem gastar um token.
            motivo_bruto = validacao.reason.value if validacao.reason else "obstrucao"
            laudo = inspecao_nao_processavel(
                campo,
                MOTIVO_POR_VALIDACAO.get(motivo_bruto, "obstrucao"),
                f"Reprovada pela validação técnica ({motivo_bruto}) antes da IA.",
            )
            self._preencher(linha, laudo, custo=0.0)
            return linha, laudo, 0.0, False

        decisao = self._guard.antes_da_chamada()
        if not decisao:
            linha.status = STATUS_NAO_DESPACHADA
            linha.error = decisao.motivo
            return linha, None, 0.0, False

        try:
            laudo = self._provider.inspect_view(image_bytes, campo)
        except Exception as exc:  # noqa: BLE001 — vista isolada
            linha.error = f"llm: {exc}"
            _log.warning(
                "vista_llm_falhou",
                checklist_id=job.checklist_id,
                campo=campo,
                error=str(exc),
            )
            # A chamada pode ter sido cobrada mesmo falhando no parse; contar
            # como gasta é o lado seguro do erro.
            self._guard.registrar_chamada(0.0)
            return linha, None, 0.0, True

        custo = compute_cost(
            model=laudo.model_version or self._settings.llm_model_efetivo,
            input_tokens=laudo.input_tokens,
            output_tokens=laudo.output_tokens,
        )
        self._guard.registrar_chamada(custo)
        self._preencher(linha, laudo, custo=custo)
        return linha, laudo, custo, True

    @staticmethod
    def _preencher(linha: ChecklistViewResult, laudo: InspecaoVista, *, custo: float) -> None:
        """Copia o laudo para a linha, denormalizando o achado principal."""
        principal = laudo.achado_principal
        linha.status = (
            STATUS_NAO_PROCESSAVEL
            if laudo.conformidade == "nao_processavel"
            else STATUS_ANALISADA
        )
        linha.conformidade = laudo.conformidade
        linha.motivo_nao_processavel = laudo.motivo_nao_processavel
        linha.vista_confere = laudo.vista_confere
        linha.conteudo_observado = laudo.conteudo_observado
        linha.achados = [a.model_dump() for a in laudo.achados]
        linha.severidade_max = laudo.severidade_max
        linha.classe = principal.classe if principal else None
        linha.tipo_defeito = principal.tipo_defeito if principal else None
        linha.confianca = principal.confianca if principal else None
        linha.model_version = laudo.model_version
        linha.input_tokens = laudo.input_tokens
        linha.output_tokens = laudo.output_tokens
        linha.cost_usd = custo
        linha.error = None

    # ── persistência ─────────────────────────────────────────────────────────

    def _persistir_vista(self, linha: ChecklistViewResult) -> None:
        """Grava a vista, sobrescrevendo se o job já tiver sido rodado antes.

        Commit por vista, de propósito: o custo já gasto precisa estar no banco
        antes da próxima chamada, senão o teto de orçamento soma um acumulado
        desatualizado e o freio chega tarde.
        """
        existente = (
            self._db.query(ChecklistViewResult)
            .filter(
                ChecklistViewResult.job_id == linha.job_id,
                ChecklistViewResult.campo == linha.campo,
            )
            .one_or_none()
        )
        if existente is None:
            self._db.add(linha)
        else:
            for coluna in (
                "dropbox_path", "status", "conformidade", "motivo_nao_processavel",
                "vista_confere", "conteudo_observado", "achados", "severidade_max",
                "classe", "tipo_defeito", "confianca", "model_version",
                "input_tokens", "output_tokens", "cost_usd", "error",
            ):
                setattr(existente, coluna, getattr(linha, coluna))
        self._db.commit()

    def _marcar_falha(self, job: PipelineJob, erro: str) -> None:
        job.status = "failed"
        job.error = erro
        job.finished_at = datetime.now(UTC)
        self._db.commit()

    # ── consultas ────────────────────────────────────────────────────────────

    def _proximos_jobs(self) -> list[PipelineJob]:
        """Jobs ``pending`` mais antigos primeiro — FIFO, sem inanição."""
        return (
            self._db.query(PipelineJob)
            .filter(PipelineJob.status == "pending", PipelineJob.mode == "sync")
            .order_by(PipelineJob.created_at.asc())
            .limit(self._settings.checklist_analysis_max_jobs_per_run)
            .all()
        )

    def _contar_pendentes(self) -> int:
        return (
            self._db.query(PipelineJob)
            .filter(PipelineJob.status == "pending", PipelineJob.mode == "sync")
            .count()
        )

    def _localizar_vistas(self, checklist_id: str) -> dict[str, ImageMetadata]:
        """``campo -> imagem`` para c54–c57, ficando com a foto mais recente.

        O Sisloc aceita refoto: o mesmo campo pode ter várias imagens com
        sequência e horário diferentes. Analisar todas multiplicaria o custo
        por algo que a tela não mostra; a última é a que o técnico quis deixar.
        """
        escolhidas: dict[str, ImageMetadata] = {}
        for imagem in self._dropbox.list_checklist_images(checklist_id):
            campo = imagem.parsed.field_name.strip().lower()
            if campo not in CAMPOS_VISTA:
                continue
            atual = escolhidas.get(campo)
            if atual is None or _mais_recente(imagem, atual):
                escolhidas[campo] = imagem
        return escolhidas


def _mais_recente(candidata: ImageMetadata, atual: ImageMetadata) -> bool:
    """Compara por ``captured_at``; sem data, o nome do arquivo desempata."""
    nova = candidata.parsed.captured_at
    velha = atual.parsed.captured_at
    if nova is not None and velha is not None:
        return nova > velha
    if nova is not None:
        return True
    if velha is not None:
        return False
    return candidata.filename > atual.filename
