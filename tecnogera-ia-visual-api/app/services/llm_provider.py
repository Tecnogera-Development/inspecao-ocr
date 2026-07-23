"""LLMProvider Protocol, ClassificationResult e implementações fake/stub.

Hierarquia:
  LLMProvider (Protocol) — interface pública estável
  FakeLLMProvider        — testes offline e CI (3 modos)
  AnthropicProvider      — produção (IAVS-002)
  OpenAIProvider         — stub emergência (IAVS-002)
"""

from __future__ import annotations

import base64
import random
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.logging import get_logger
from app.services.cost_calculator import LLMUsage
from app.services.dropbox import parse_filename

_log = get_logger(__name__)

# ── Constantes ──────────────────────────────────────────────────────────────
_ANTHROPIC_BETA_HEADER = "extended-cache-ttl-2025-04-11"
_CONFIDENCE_THRESHOLD = 0.70
_INCONCLUSIVE_FLOOR = 0.40

def _build_emit_classification_tool(field_names: list[str]) -> dict[str, Any]:
    """Constrói tool com enum=field_names — força modelo a escolher do vocabulário.

    v1.1: adiciona second_best_field, second_best_confidence e quality_score (opcionais).
    """
    return {
        "name": "emit_classification",
        "description": (
            "Emite a classificação de uma imagem de checklist de gerador industrial. "
            "Use EXATAMENTE os campos do schema; não invente valores."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field_name": {
                    "type": "string",
                    "enum": list(field_names),
                    "description": (
                        "Código do campo Sisloc. DEVE ser exatamente um dos valores do enum."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "description": "Grau de confiança da classificação, de 0.0 a 1.0.",
                },
                "observation": {
                    "type": "string",
                    "description": "1-3 frases em PT-BR técnico descrevendo o que foi fotografado.",
                },
                "detected_issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Problemas detectados na imagem. Vazio se não há problemas.",
                },
                "second_best_field": {
                    "type": ["string", "null"],
                    "description": (
                        "Segundo melhor candidato de campo. Preencha APENAS se o segundo "
                        "candidato tiver confiança ≥ 0.30 E (top_1 − top_2) < 0.15; "
                        "caso contrário deixe null."
                    ),
                },
                "second_best_confidence": {
                    "type": ["number", "null"],
                    "description": (
                        "Confiança do segundo melhor candidato, de 0.0 a 1.0. "
                        "Deve ser preenchido se e somente se second_best_field for preenchido."
                    ),
                },
                "quality_score": {
                    "type": "number",
                    "description": (
                        "Qualidade da foto para fins de auditoria, de 0.0 a 1.0. "
                        "Rubrica: 0.0=inutilizável (escura/borrada/cortada); "
                        "0.5=usável com ressalvas; 1.0=nítida, bem iluminada, "
                        "enquadramento completo."
                    ),
                },
            },
            "required": ["field_name", "confidence", "observation", "detected_issues"],
        },
    }

_DAMAGE_SYSTEM_PROMPT = (
    "Você é um inspetor de avarias de equipamentos de locação industrial.\n"
    "Analise a imagem e emita o diagnóstico usando emit_damage.\n\n"
    "Classes de avaria:\n"
    "- ausencia_item: item obrigatório ausente ou não visível na imagem\n"
    "- fora_padrao_visual: presente mas com desvio visual do padrão (sujeira excessiva, "
    "desgaste, posição errada, etiqueta ilegível)\n"
    "- dano_visivel: dano físico visível (amasso, trinca, corrosão, vazamento, queima, "
    "isolação exposta)\n\n"
    "Regras obrigatórias:\n"
    "1. no_conformity=false → classes=[] — o equipamento está conforme neste ângulo\n"
    "2. no_conformity=true → ao menos uma entrada em classes[] com observation NÃO VAZIA\n"
    "3. observation DEVE citar âncora visual concreta "
    "(ex: 'mancha escura no painel esquerdo', 'parafuso ausente no quadrante inferior'). "
    "Justificativas vagas ('há dano', 'item ausente') são PROIBIDAS e serão rejeitadas\n"
    "4. canonical_angle: ângulo desta foto — escolha o mais próximo do ponto de vista\n"
    "5. severity segue a escala contratual: "
    "1=Crítica (risco imediato, bloqueia liberação), "
    "2=Alta (degrada operação, corrigir em 48h), "
    "3=Média (relevante, sem risco imediato), "
    "4=Baixa (desgaste cosmético). "
    "Use 1 apenas para riscos reais — não exagere\n"
    "6. Se gabarito fornecido, compare DIRETAMENTE com o padrão visual mostrado\n"
    "7. Se imagem de saída fornecida, julgue a MUDANÇA em relação à saída "
    "(não a aparência absoluta do retorno)\n"
    "8. Se REFERÊNCIAS de entrega numeradas forem fornecidas, primeiro identifique qual "
    "delas mostra o MESMO ângulo/parte da imagem alvo, compare a alvo contra ESSA referência, "
    "e informe o índice dela em matched_reference_index. Se nenhuma referência mostra o mesmo "
    "ângulo/parte, use matched_reference_index=null e avalie a alvo isoladamente"
)


def _build_emit_damage_tool() -> dict[str, Any]:
    """Constrói tool emit_damage para classificação de avarias.

    Multi-label: um evento pode ter múltiplas classes (ex: dano_visivel E fora_padrao_visual).
    Flag top-level no_conformity é resposta explícita — evita ambiguidade de array vazio.
    Ângulo canônico no mesmo tool para minimizar chamadas.
    """
    return {
        "name": "emit_damage",
        "description": (
            "Emite o diagnóstico de avaria de uma imagem de equipamento em locação industrial. "
            "Use EXATAMENTE os campos do schema; não invente valores fora do enum."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "no_conformity": {
                    "type": "boolean",
                    "description": (
                        "True se detectou ao menos uma não conformidade; "
                        "false se o equipamento está conforme. "
                        "Se false, classes DEVE ser []."
                    ),
                },
                "classes": {
                    "type": "array",
                    "description": (
                        "Lista de não conformidades detectadas. "
                        "Vazio SOMENTE se no_conformity=false."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "class_name": {
                                "type": "string",
                                "enum": [
                                    "ausencia_item",
                                    "fora_padrao_visual",
                                    "dano_visivel",
                                ],
                                "description": "Classe de não conformidade.",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Grau de certeza desta classe, de 0.0 a 1.0.",
                            },
                            "severity": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 4,
                                "description": (
                                    "Severidade: 1=Crítica (risco imediato, bloqueia liberação), "
                                    "2=Alta (degrada operação, corrigir em 48h), "
                                    "3=Média (não conformidade sem risco imediato), "
                                    "4=Baixa (desgaste cosmético)."
                                ),
                            },
                            "observation": {
                                "type": "string",
                                "description": (
                                    "1-2 frases descrevendo a não conformidade com âncora visual "
                                    "concreta (localização, cor, forma, dimensão). "
                                    "PROIBIDO ser vago."
                                ),
                            },
                        },
                        "required": ["class_name", "confidence", "severity", "observation"],
                    },
                },
                "canonical_angle": {
                    "type": "string",
                    "enum": [
                        "frontal",
                        "lat_dir",
                        "lat_esq",
                        "traseira",
                        "teto",
                        "interior",
                    ],
                    "description": "Ângulo canônico desta imagem.",
                },
                "matched_reference_index": {
                    "type": ["integer", "null"],
                    "description": (
                        "Quando referências de entrega forem fornecidas, o índice (0-based) "
                        "da referência que mostra o MESMO ângulo/parte da imagem alvo — a que "
                        "você usou como base de comparação. null se nenhuma corresponde ou se "
                        "não houver referências."
                    ),
                },
            },
            "required": ["no_conformity", "classes", "canonical_angle"],
        },
    }


_CLASSIFY_SYSTEM_PROMPT = (
    "Você é um especialista em inspeção visual de geradores industriais para locação industrial.\n"
    "Analise cada imagem e classifique-a no campo correto do formulário Sisloc usando a ferramenta "
    "emit_classification.\n\n"
    "Regras:\n"
    "- field_name deve ser exatamente um dos códigos fornecidos (ex: c0, c3, c55)\n"
    "- confidence de 0.0 a 1.0: use ≥0.90 apenas quando tiver total certeza\n"
    "- observation: 1-3 frases objetivas em PT-BR técnico sobre o conteúdo da foto\n"
    "- detected_issues: liste apenas problemas visíveis e objetivos\n"
    "- Use os exemplos fornecidos como referência visual para calibrar suas classificações\n\n"
    "Campos opcionais:\n"
    "- second_best_field + second_best_confidence: preencha APENAS quando o segundo candidato "
    "tiver confiança ≥ 0.30 E (top_1 − top_2) < 0.15; caso contrário deixe ambos null\n"
    "- quality_score: quando solicitado, avalie a qualidade fotográfica de 0.0 a 1.0 "
    "(0.0=inutilizável; 0.5=usável com ressalvas; 1.0=nítida e completa)"
)

_REPORT_SYSTEM_PROMPT = (
    "Você é um redator técnico especializado em laudos de inspeção de geradores industriais. "
    "Preencha o template markdown fornecido com os dados das classificações. "
    "Use APENAS os campos do JSON. Campos vazios viram 'não observado'. "
    "Proibido inferir informações além do dado fornecido."
)


class ClassificationResult(BaseModel):
    """Resultado de classificação de uma imagem por campo de checklist."""

    image_filename: str
    field_name: str | None = None
    generic_class: (
        Literal[
            "painel_display",
            "conexao_eletrica",
            "estrutura_externa",
            "componente_mecanico",
            "etiqueta_documento",
        ]
        | None
    ) = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_valid: bool
    observation: str = ""
    detected_issues: list[str] = Field(default_factory=list)
    requires_human_review: bool
    model_version: str
    shot_bank_hash: str = ""

    # v1.1 — segunda melhor classificação (opcional)
    second_best_field: str | None = None
    second_best_confidence: float | None = None

    # v1.1 — qualidade da foto (opcional; ativado via EMIT_QUALITY_SCORE)
    quality_score: float | None = None

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence deve ser [0,1], recebido {v}")
        return v

    @field_validator("quality_score")
    @classmethod
    def _quality_score_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"quality_score deve ser [0,1], recebido {v}")
        return v

    @model_validator(mode="after")
    def _second_best_consistency(self) -> "ClassificationResult":
        """Ambos second_best_* devem ser null ou ambos preenchidos."""
        has_field = self.second_best_field is not None
        has_conf = self.second_best_confidence is not None
        if has_field != has_conf:
            raise ValueError(
                "second_best_field e second_best_confidence devem ser ambos null "
                "ou ambos preenchidos"
            )
        return self


class DamageClassItem(BaseModel):
    class_name: Literal["ausencia_item", "fora_padrao_visual", "dano_visivel"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    severity: int = Field(..., ge=1, le=4)
    observation: str


class DamageClassifyResult(BaseModel):
    no_conformity: bool
    classes: list[DamageClassItem] = Field(default_factory=list)
    canonical_angle: Literal[
        "frontal", "lat_dir", "lat_esq", "traseira", "teto", "interior"
    ]
    model_version: str
    # índice (0-based) da referência de entrega que mostra o MESMO ângulo/parte
    # da imagem alvo; None quando não há referências ou nenhuma corresponde
    matched_reference_index: int | None = None


_FakeMode = Literal["filename_oracle", "noisy", "low_conf"]

_GENERIC_CLASSES: list[str] = [
    "painel_display",
    "conexao_eletrica",
    "estrutura_externa",
    "componente_mecanico",
    "etiqueta_documento",
]
_GENERIC_CLASS_SET = set(_GENERIC_CLASSES)


class FakeLLMProvider:
    """Provider falso para testes offline.

    Modos:
      filename_oracle — lê cN diretamente do nome do arquivo (100% acerto)
      noisy           — ~80% acerto, confiança variada (seed reproduzível)
      low_conf        — sempre confidence=0.50
    """

    def __init__(self, mode: _FakeMode = "filename_oracle", *, seed: int = 0) -> None:
        if mode not in ("filename_oracle", "noisy", "low_conf"):
            raise ValueError(
                f"modo '{mode}' inválido; esperado: filename_oracle | noisy | low_conf"
            )
        self._mode = mode
        self._rng = random.Random(seed)  # noqa: S311 — não é criptográfico, é simulação
        self.accumulated_usage = LLMUsage(model="fake")

    def classify_image(
        self,
        image_filename: str,
        image_bytes: bytes,
        field_names: list[str],
        *,
        shots: list[tuple[str, bytes]] | None = None,
    ) -> ClassificationResult:
        try:
            parsed = parse_filename(image_filename)
            true_field = parsed.field_name
        except ValueError:
            true_field = field_names[0] if field_names else "c0"

        # Detect generic-class (fallback) mode: field_names are super-classes, not cN codes.
        # Oracle rule: map field number from filename modulo 5 to pick a super-class.
        _is_generic = bool(field_names) and field_names[0] in _GENERIC_CLASS_SET

        if self._mode == "filename_oracle":
            if _is_generic:
                import re as _re  # noqa: PLC0415

                m = _re.search(r"_c(\d+)_", image_filename)
                field_num = int(m.group(1)) if m else 0
                generic_field = _GENERIC_CLASSES[field_num % len(_GENERIC_CLASSES)]
                return ClassificationResult(
                    image_filename=image_filename,
                    field_name=generic_field,
                    confidence=1.0,
                    is_valid=True,
                    observation="Classificação genérica via filename_oracle (fallback).",
                    detected_issues=[],
                    requires_human_review=False,
                    model_version="fake-oracle-1.0",
                    shot_bank_hash="",
                )
            return ClassificationResult(
                image_filename=image_filename,
                field_name=true_field,
                confidence=1.0,
                is_valid=True,
                observation="Classificação automática via filename_oracle.",
                detected_issues=[],
                requires_human_review=False,
                model_version="fake-oracle-1.0",
                shot_bank_hash="",
            )

        if self._mode == "low_conf":
            return ClassificationResult(
                image_filename=image_filename,
                field_name=true_field,
                confidence=0.50,
                is_valid=False,
                observation="Classificação inconclusiva (modo low_conf).",
                detected_issues=[],
                requires_human_review=True,
                model_version="fake-low_conf-1.0",
                shot_bank_hash="",
            )

        # noisy: 80% acerto, confiança aleatória entre 0.55 e 0.99
        acertou = self._rng.random() < 0.8
        chosen = true_field if acertou else (
            self._rng.choice([f for f in field_names if f != true_field] or [true_field])
        )
        conf = round(self._rng.uniform(0.55, 0.99), 4)
        return ClassificationResult(
            image_filename=image_filename,
            field_name=chosen,
            confidence=conf,
            is_valid=conf >= 0.70,
            observation="Classificação com ruído sintético (modo noisy).",
            detected_issues=[],
            requires_human_review=0.40 <= conf < 0.70,
            model_version="fake-noisy-1.0",
            shot_bank_hash="",
        )

    def classify_image_batch(
        self,
        images: list[tuple[str, bytes]],
        field_names: list[str],
        *,
        shots: list[tuple[str, bytes]] | None = None,
    ) -> str:
        """Simula submissão de batch; retorna fake batch_id."""
        return f"fake-batch-{len(images)}"

    def retrieve_batch(self, batch_id: str) -> Any:
        """Simula batch imediatamente resolvido."""
        from dataclasses import dataclass  # noqa: PLC0415

        @dataclass
        class _Status:
            processing_status: str = "ended"

        return _Status()

    def get_batch_results(self, batch_id: str) -> list[Any]:
        """Retorna lista vazia — fake batch não tem resultados reais."""
        return []

    def classify_event(
        self,
        image_bytes: bytes,
        *,
        gabarito_bytes: bytes | None = None,
        saida_bytes: bytes | None = None,
        references: list[bytes] | None = None,
    ) -> DamageClassifyResult:
        """Retorna resultado conforme sem não conformidades — usado em testes."""
        return DamageClassifyResult(
            no_conformity=False,
            classes=[],
            canonical_angle="frontal",
            model_version="fake-damage-1.0",
            matched_reference_index=0 if references else None,
        )

    def generate_report(
        self,
        classifications: list[ClassificationResult],
        checklist_meta: dict[str, Any],
        template: str,
    ) -> str:
        checklist_id = checklist_meta.get("checklist_id", "000000")
        data = checklist_meta.get("data", "N/D")
        cobertura = checklist_meta.get("cobertura_pct", 0)
        n_excl = checklist_meta.get("n_excluded", 0)
        total_obr = checklist_meta.get("total_obrigatorios", len(classifications))
        valid = checklist_meta.get("valid_classifications", [])
        inconc = checklist_meta.get("inconclusive_classifications", [])

        lines: list[str] = [
            "# Relatório de Inspeção Visual — Gerador",
            "",
            f"**Checklist nº {checklist_id}** · Filial não observado · {data}",
            "",
            "---",
            "",
            "## 1. Identificação",
            "",
            "| Campo | Valor |",
            "|-------|-------|",
            f"| Checklist ID | {checklist_id} |",
            f"| Data da inspeção | {data} |",
            "| Hora | não observado |",
            "| Filial | não observado |",
            "| Tipo de equipamento | não observado |",
            "| Modelo / Tag | não observado |",
            "| Técnico responsável | não observado |",
            "| Ordem de Serviço | não observado |",
            "| Inspeção gerada por | IA Visual Tecnogera v0.1 |",
            "",
            "---",
            "",
            "## 2. Resumo executivo",
            "",
            f"**Status geral:** {'Aprovado' if not inconc and not n_excl else 'Aprovado com ressalvas'}",
            "",
            "Inspeção automática via FakeLLMProvider.",
            "",
            "| Severidade | Quantidade |",
            "|-----------|-----------:|",
            "| Crítica | 0 |",
            "| Alta | 0 |",
            "| Média | 0 |",
            "| Baixa | 0 |",
            "| Info | 0 |",
            "",
            f"**Itens inspecionados:** {len(classifications)}",
            f"**Cobertura fotográfica:** {cobertura}%"
            f" ({len(valid)} de {total_obr} obrigatórios)",
            f"**Itens não analisados:** {n_excl}",
            "",
            "---",
            "",
            "## 3. Análise por item",
            "",
        ]

        for i, c in enumerate(valid, 1):
            fn = c.get("field_name") or c.get("image_filename", f"item-{i}")
            obs = c.get("observation", "não observado")
            conf = int(c.get("confidence", 0) * 100)
            img = c.get("image_filename", "")
            lines += [
                f"### 3.{i}. `{fn}`",
                "",
                "- **Foto presente:** Sim",
                "- **Qualidade da evidência:** não observado",
                f"- **Classificação:** {fn} (confiança {conf}%)",
                "- **Severidade da observação:** Info",
                "",
                f"**Observação:** {obs}",
                "",
                "**Justificativa técnica:** não observado",
                "",
                f"![{fn}](file:///{img})",
                "",
                "---",
                "",
            ]

        lines += ["## 4. Inconclusivas", ""]

        if inconc:
            for i, c in enumerate(inconc, 1):
                fn = c.get("field_name") or c.get("image_filename", f"inconc-{i}")
                img = c.get("image_filename", "")
                conf = int(c.get("confidence", 0) * 100)
                obs = c.get("observation", "não observado")
                qs = c.get("quality_score")
                quality_line = f"{qs:.1f}/1.0" if qs is not None else "não avaliado"
                lines += [
                    f"### 4.{i}. `{img}`",
                    "",
                    f"- **Melhor palpite:** {fn}",
                    f"- **Confiança:** {conf}%",
                    f"- **Qualidade:** {quality_line}",
                    f"- **Observação:** {obs}",
                    "",
                    f"![{img}](file:///{img})",
                    "",
                    "---",
                    "",
                ]
        else:
            lines += ["Nenhuma foto inconclusiva nesta inspeção.", "", "---", ""]

        lines += [
            "## 5. Não conformidades",
            "",
            "Nenhuma não conformidade identificada nesta inspeção.",
            "",
            "---",
            "",
            "## 6. Recomendações",
            "",
            "### 6.1 Curto prazo (até a próxima locação)",
            "não observado",
            "",
            "### 6.2 Médio prazo (próximo ciclo de manutenção)",
            "não observado",
            "",
            "### 6.3 Preventivas",
            "não observado",
            "",
            "---",
            "",
            "## 7. Conclusão",
            "",
            "não observado",
            "",
            "---",
            "",
            "## 8. Limitações da análise",
            "",
            "Este relatório foi gerado por sistema automatizado (IA Visual Tecnogera).",
            "",
            "---",
            "",
            "<small>Relatório gerado automaticamente por IA Visual Tecnogera v0.1.</small>",
        ]

        return "\n".join(lines)


def _is_retryable(exc: BaseException) -> bool:
    """Retorna True para erros 429 (rate limit) ou 529 (overloaded) da Anthropic."""
    try:
        import anthropic as _sdk  # noqa: PLC0415

        if isinstance(exc, _sdk.RateLimitError):
            return True
        if isinstance(exc, _sdk.APIStatusError) and exc.status_code == 529:
            return True
    except ImportError:
        pass
    return False


def _field_from_filename(filename: str) -> str | None:
    """Extrai cN do nome do arquivo Sisloc. None se inválido."""
    try:
        parsed = parse_filename(filename)
    except (ValueError, AttributeError):
        return None
    return parsed.field_name if parsed else None


_MAX_IMAGE_DIM = 1024  # px no lado maior — corta ~36% tokens vs. 1280 sem perda de accuracy


def _resize_for_api(image_bytes: bytes) -> bytes:
    """Reduz imagem para `_MAX_IMAGE_DIM` no lado maior; mantém aspect ratio.

    Imagens já ≤ _MAX_IMAGE_DIM passam intactas. Falhas de decode caem no original.
    """
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if max(img.size) <= _MAX_IMAGE_DIM:
                return image_bytes
            img.thumbnail((_MAX_IMAGE_DIM, _MAX_IMAGE_DIM), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
    except Exception:
        return image_bytes


def _image_block(image_bytes: bytes, *, with_cache: bool = False) -> dict[str, Any]:
    resized = _resize_for_api(image_bytes)
    data = base64.standard_b64encode(resized).decode("ascii")
    block: dict[str, Any] = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    }
    if with_cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


class AnthropicProvider:
    """Provider real via Anthropic API.

    Usa claude-sonnet-4-6 com tool_use forçado (emit_classification) e prompt
    cache de TTL estendido (1h). Retry automático em 429/529 via tenacity.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        report_model: str | None = None,
        _client: Any = None,
    ) -> None:
        self._model = model
        self._report_model = report_model or model
        self.accumulated_usage = LLMUsage(model=model)
        if _client is not None:
            self._client = _client
        else:
            import anthropic as _sdk  # noqa: PLC0415

            self._client = _sdk.Anthropic(
                api_key=api_key,
                default_headers={"anthropic-beta": _ANTHROPIC_BETA_HEADER},
            )

    def classify_image(
        self,
        image_filename: str,
        image_bytes: bytes,
        field_names: list[str],
        *,
        shots: list[tuple[str, bytes]] | None = None,
    ) -> ClassificationResult:
        """Classifica uma imagem usando Claude Vision com few-shot rotulado.

        Cada shot é prefixado com `Exemplo do campo {cN} (i/N):` para que o modelo
        associe shot ↔ classe. Sem rotulagem, a few-shot vira ruído visual
        (causa raiz do 0% accuracy em iter 1-2).
        """
        user_content: list[dict[str, Any]] = []

        if shots:
            total = len(shots)
            for i, (shot_filename, shot_bytes) in enumerate(shots, start=1):
                shot_field = _field_from_filename(shot_filename) or "?"
                user_content.append(
                    {"type": "text", "text": f"Exemplo do campo {shot_field} ({i}/{total}):"}
                )
                is_last = i == total
                user_content.append(_image_block(shot_bytes, with_cache=is_last))
            user_content.append({"type": "text", "text": "Imagem alvo — classifique:"})

        user_content.append(_image_block(image_bytes, with_cache=False))

        fields_bullets = "\n".join(f"- {f}" for f in field_names)
        system = [
            {
                "type": "text",
                "text": (
                    f"{_CLASSIFY_SYSTEM_PROMPT}\n\n"
                    f"Vocabulário de field_name (use EXATAMENTE um destes códigos):\n"
                    f"{fields_bullets}"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages = [{"role": "user", "content": user_content}]

        response = self._call_with_retry(
            model=self._model,
            max_tokens=256,
            system=system,
            messages=messages,
            tools=[_build_emit_classification_tool(field_names)],
            tool_choice={"type": "tool", "name": "emit_classification"},
        )
        self._log_usage(response.usage, image_filename)

        tool_block = next(b for b in response.content if b.type == "tool_use")
        raw: dict[str, Any] = tool_block.input

        confidence = float(raw["confidence"])
        is_valid = confidence >= _CONFIDENCE_THRESHOLD
        requires_review = _INCONCLUSIVE_FLOOR <= confidence < _CONFIDENCE_THRESHOLD

        sbf = raw.get("second_best_field")
        sbc = raw.get("second_best_confidence")
        if (sbf is None) != (sbc is None):
            _log.warning(
                "second_best_xor_sanitized",
                image_filename=image_filename,
                second_best_field=sbf,
                second_best_confidence=sbc,
            )
            sbf = None
            sbc = None

        return ClassificationResult(
            image_filename=image_filename,
            field_name=raw.get("field_name"),
            confidence=confidence,
            is_valid=is_valid,
            observation=raw.get("observation", ""),
            detected_issues=raw.get("detected_issues", []),
            requires_human_review=requires_review,
            model_version=self._model,
            shot_bank_hash="",
            second_best_field=sbf,
            second_best_confidence=sbc,
            quality_score=raw.get("quality_score"),
        )

    def classify_event(
        self,
        image_bytes: bytes,
        *,
        gabarito_bytes: bytes | None = None,
        saida_bytes: bytes | None = None,
        references: list[bytes] | None = None,
    ) -> DamageClassifyResult:
        """Classifica avarias de um evento usando emit_damage tool.

        Parâmetros opcionais:
          gabarito_bytes — imagem de referência do gabarito (marcada com cache_control)
          saida_bytes    — imagem de saída para comparação no retorno
          references     — fotos de entrega numeradas; o modelo escolhe a correspondente
                           (matched_reference_index) e compara a alvo contra ela
        """
        user_content: list[dict[str, Any]] = []

        if gabarito_bytes is not None:
            user_content.append(
                {"type": "text", "text": "Gabarito (padrão de referência visual):"}
            )
            user_content.append(_image_block(gabarito_bytes, with_cache=True))

        if references:
            user_content.append(
                {
                    "type": "text",
                    "text": (
                        "Referências de entrega (fotos do equipamento no estado bom). "
                        "Ache a que mostra o mesmo ângulo/parte da imagem alvo:"
                    ),
                }
            )
            # Anthropic permite no máx. 4 cache breakpoints. Marcamos cache_control
            # SÓ na última referência — o cache cobre todo o prefixo (todas as
            # referências), reusado entre análises do mesmo checklist, com 1 breakpoint.
            last = len(references) - 1
            for i, ref in enumerate(references):
                user_content.append({"type": "text", "text": f"Referência {i}:"})
                user_content.append(_image_block(ref, with_cache=(i == last)))

        if saida_bytes is not None:
            user_content.append(
                {"type": "text", "text": "Imagem de saída (estado na saída para locação):"}
            )
            user_content.append(_image_block(saida_bytes, with_cache=False))

        user_content.append({"type": "text", "text": "Imagem alvo — emita o diagnóstico:"})
        user_content.append(_image_block(image_bytes, with_cache=False))

        system = [
            {
                "type": "text",
                "text": _DAMAGE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages = [{"role": "user", "content": user_content}]

        response = self._call_with_retry(
            model=self._model,
            max_tokens=512,
            system=system,
            messages=messages,
            tools=[_build_emit_damage_tool()],
            tool_choice={"type": "tool", "name": "emit_damage"},
        )
        self._log_usage(response.usage, "classify_event")

        tool_block = next(b for b in response.content if b.type == "tool_use")
        raw: dict[str, Any] = tool_block.input

        return DamageClassifyResult(
            no_conformity=bool(raw["no_conformity"]),
            classes=[DamageClassItem(**c) for c in raw.get("classes", [])],
            canonical_angle=raw["canonical_angle"],
            model_version=self._model,
            matched_reference_index=raw.get("matched_reference_index"),
        )

    def generate_report(
        self,
        classifications: list[ClassificationResult],
        checklist_meta: dict[str, Any],
        template: str,
    ) -> str:
        """Gera markdown do relatório via Claude (texto puro, sem vision)."""
        import json as _json  # noqa: PLC0415

        payload = {
            "classifications": [c.model_dump() for c in classifications],
            "meta": checklist_meta,
        }
        user_text = (
            f"Template:\n\n{template}\n\n"
            f"Dados:\n\n{_json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "Preencha o template com os dados. Campos ausentes viram 'não observado'."
        )
        system = [
            {
                "type": "text",
                "text": _REPORT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages = [{"role": "user", "content": user_text}]

        response = self._call_with_retry(
            model=self._report_model,
            max_tokens=16384,
            system=system,
            messages=messages,
        )
        self._log_usage(response.usage, "report")

        text_block = next(b for b in response.content if b.type == "text")
        return text_block.text  # type: ignore[no-any-return]

    def classify_image_batch(
        self,
        images: list[tuple[str, bytes]],
        field_names: list[str],
        *,
        shots: list[tuple[str, bytes]] | None = None,
    ) -> str:
        """Submete classificações em batch via Anthropic Message Batches API.

        Retorna o batch_id da Anthropic para polling posterior.
        Cada imagem gera um request com custom_id = filename.
        O bloco de system carrega cache_control para maximizar hit rate.
        """
        fields_bullets = "\n".join(f"- {f}" for f in field_names)
        system = [
            {
                "type": "text",
                "text": (
                    f"{_CLASSIFY_SYSTEM_PROMPT}\n\n"
                    f"Vocabulário de field_name (use EXATAMENTE um destes códigos):\n"
                    f"{fields_bullets}"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        tool = _build_emit_classification_tool(field_names)
        tool_choice: dict[str, Any] = {"type": "tool", "name": "emit_classification"}

        requests = []
        for image_filename, image_bytes in images:
            user_content: list[dict[str, Any]] = []
            if shots:
                total = len(shots)
                for i, (shot_filename, shot_bytes) in enumerate(shots, start=1):
                    shot_field = _field_from_filename(shot_filename) or "?"
                    user_content.append(
                        {"type": "text", "text": f"Exemplo do campo {shot_field} ({i}/{total}):"}
                    )
                    is_last = i == total
                    user_content.append(_image_block(shot_bytes, with_cache=is_last))
                user_content.append({"type": "text", "text": "Imagem alvo — classifique:"})
            user_content.append(_image_block(image_bytes, with_cache=False))

            requests.append(
                {
                    "custom_id": image_filename,
                    "params": {
                        "model": self._model,
                        "max_tokens": 256,
                        "system": system,
                        "messages": [{"role": "user", "content": user_content}],
                        "tools": [tool],
                        "tool_choice": tool_choice,
                    },
                }
            )

        response = self._client.messages.batches.create(requests=requests)
        batch_id: str = response.id
        _log.info("batch_submitted", batch_id=batch_id, n_images=len(images))
        return batch_id

    def retrieve_batch(self, batch_id: str) -> Any:  # noqa: ANN401
        """Consulta o status de um batch em andamento."""
        return self._client.messages.batches.retrieve(batch_id)

    def get_batch_results(self, batch_id: str) -> list[Any]:
        """Baixa os resultados de um batch finalizado."""
        return list(self._client.messages.batches.results(batch_id))

    def _call_with_retry(self, **kwargs: Any) -> Any:  # noqa: ANN401
        """Chama messages.create com retry em 429/529 (máx 3 tentativas)."""
        result: Any = None
        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                result = self._client.messages.create(**kwargs)
        return result

    def _log_usage(self, usage: Any, context: str) -> None:  # noqa: ANN401
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.accumulated_usage.accumulate(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_write,
        )
        _log.info(
            "anthropic_usage",
            context=context,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            estimated_cost_usd=round(
                usage.input_tokens * 3e-6
                + usage.output_tokens * 15e-6
                + cache_read * 0.3e-6
                + cache_write * 3.75e-6,
                6,
            ),
        )


def _oai_image_block(image_bytes: bytes) -> dict[str, Any]:
    """Bloco de imagem no formato OpenAI (data URI base64)."""
    resized = _resize_for_api(image_bytes)
    data = base64.standard_b64encode(resized).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{data}", "detail": "high"},
    }


def _emit_damage_openai_tool() -> dict[str, Any]:
    """emit_damage no formato de function calling da OpenAI (reusa o mesmo schema)."""
    t = _build_emit_damage_tool()
    return {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }


class OpenAIProvider:
    """Provider via OpenAI API — classificação de avarias (classify_event).

    Usa um modelo com visão (default gpt-4o) e function calling forçado
    (emit_damage) para devolver o mesmo DamageClassifyResult da Anthropic.
    O cache de prompt da OpenAI é automático (sem cache_control manual).
    """

    def __init__(self, api_key: str, model: str, *, _client: Any = None) -> None:  # noqa: ANN401
        self._model = model
        self.accumulated_usage = LLMUsage(model=model)
        if _client is not None:
            self._client = _client
        else:
            import openai as _sdk  # noqa: PLC0415

            self._client = _sdk.OpenAI(api_key=api_key, max_retries=3)

    def classify_event(
        self,
        image_bytes: bytes,
        *,
        gabarito_bytes: bytes | None = None,
        saida_bytes: bytes | None = None,
        references: list[bytes] | None = None,
    ) -> DamageClassifyResult:
        content: list[dict[str, Any]] = []

        if gabarito_bytes is not None:
            content.append({"type": "text", "text": "Gabarito (padrão de referência visual):"})
            content.append(_oai_image_block(gabarito_bytes))

        if references:
            content.append(
                {
                    "type": "text",
                    "text": (
                        "Referências de entrega (fotos do equipamento no estado bom). "
                        "Ache a que mostra o mesmo ângulo/parte da imagem alvo:"
                    ),
                }
            )
            for i, ref in enumerate(references):
                content.append({"type": "text", "text": f"Referência {i}:"})
                content.append(_oai_image_block(ref))

        if saida_bytes is not None:
            content.append({"type": "text", "text": "Imagem de saída (estado na saída para locação):"})
            content.append(_oai_image_block(saida_bytes))

        content.append({"type": "text", "text": "Imagem alvo — emita o diagnóstico:"})
        content.append(_oai_image_block(image_bytes))

        messages = [
            {"role": "system", "content": _DAMAGE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

        completion = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=[_emit_damage_openai_tool()],
            tool_choice={"type": "function", "function": {"name": "emit_damage"}},
            max_tokens=512,
        )
        self._log_usage_openai(completion)

        import json as _json  # noqa: PLC0415

        tool_call = completion.choices[0].message.tool_calls[0]
        raw: dict[str, Any] = _json.loads(tool_call.function.arguments)

        classes = [
            DamageClassItem(
                class_name=c["class_name"],
                confidence=min(1.0, max(0.0, float(c["confidence"]))),
                severity=min(4, max(1, int(c["severity"]))),
                observation=c["observation"],
            )
            for c in raw.get("classes", [])
        ]
        return DamageClassifyResult(
            no_conformity=bool(raw["no_conformity"]),
            classes=classes,
            canonical_angle=raw["canonical_angle"],
            model_version=self._model,
            matched_reference_index=raw.get("matched_reference_index"),
        )

    def _log_usage_openai(self, completion: Any) -> None:  # noqa: ANN401
        usage = getattr(completion, "usage", None)
        if usage is None:
            return
        inp = getattr(usage, "prompt_tokens", 0) or 0
        out = getattr(usage, "completion_tokens", 0) or 0
        self.accumulated_usage.accumulate(input_tokens=inp, output_tokens=out)
        _log.info(
            "openai_usage",
            context="classify_event",
            input_tokens=inp,
            output_tokens=out,
            model=self._model,
        )

    def classify_image(self, *args: Any, **kwargs: Any) -> ClassificationResult:  # noqa: ANN401
        raise NotImplementedError("OpenAIProvider.classify_image não implementado (Sisloc usa Anthropic)")

    def generate_report(self, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401
        raise NotImplementedError("OpenAIProvider.generate_report não implementado (Sisloc usa Anthropic)")
