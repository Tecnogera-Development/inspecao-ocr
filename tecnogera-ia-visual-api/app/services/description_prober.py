"""DescriptionProber — gera descrições automáticas para campos cN via Vision LLM.

Para cada campo com `descricao: "TODO"` em equipment_profiles.yaml, envia 4-6
imagens do shot_bank ao Claude (Sonnet) via tool_use forçado `emit_descricao` e
salva o resultado como `descricao_auto_gerada:` (chave distinta — não sobrescreve
`descricao:`). Promoção para `descricao:` é decisão humana posterior.

Interface pública:
  ProbeResult     — dataclass com field_name, descricao, confidence
  DescriptionProber(*, _client=None, api_key="", model="claude-sonnet-4-6")
    .probe_field(field_name, images) -> ProbeResult
    .update_yaml(yaml_path, results)  — atualiza YAML in-place
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

_log = get_logger(__name__)

_EMIT_DESCRICAO_TOOL: dict[str, Any] = {
    "name": "emit_descricao",
    "description": (
        "Emite uma descrição técnica breve do componente/área fotografado. "
        "Use PT-BR técnico, 8-15 palavras, sem mencionar cor, iluminação ou ângulo."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "descricao": {
                "type": "string",
                "description": "Descrição técnica do componente em PT-BR (8-15 palavras).",
            },
            "confidence": {
                "type": "number",
                "description": "Confiança na descrição, de 0.0 a 1.0.",
            },
        },
        "required": ["descricao", "confidence"],
    },
}

_SYSTEM_PROMPT = (
    "Você é um especialista em inspeção de geradores industriais. "
    "Analise as imagens e descreva em PT-BR técnico o componente/área comum a todas. "
    "Não mencione cor, iluminação, ângulo, resolução ou qualidade da foto — "
    "apenas o componente ou área inspecionada."
)


@dataclass
class ProbeResult:
    """Resultado de uma sondagem de descrição para um campo cN."""

    field_name: str
    descricao: str
    confidence: float


def _image_block(image_bytes: bytes) -> dict[str, Any]:
    data = base64.standard_b64encode(image_bytes).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    }


class DescriptionProber:
    """Sonda descrições de campos cN via Vision LLM (emit_descricao tool_use)."""

    def __init__(
        self,
        *,
        _client: Any = None,
        api_key: str = "",
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._model = model
        if _client is not None:
            self._client = _client
        else:
            import anthropic as _sdk  # noqa: PLC0415

            self._client = _sdk.Anthropic(api_key=api_key)

    def probe_field(
        self,
        field_name: str,
        images: list[tuple[str, bytes]],
    ) -> ProbeResult:
        """Chama LLM com tool_use emit_descricao para descrever um campo.

        Args:
            field_name: Código do campo (ex: "c3").
            images: Lista de (filename, bytes) com 4-6 imagens do shot_bank.

        Returns:
            ProbeResult com descricao e confidence.
        """
        n = len(images)
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Estas {n} imagens são exemplos do MESMO campo de checklist ({field_name}). "
                    "Descreva em PT-BR técnico (8-15 palavras) o objeto/área comum a todas. "
                    "Não mencione cor, iluminação ou ângulo — apenas o componente."
                ),
            }
        ]
        for _, img_bytes in images:
            content.append(_image_block(img_bytes))

        response = self._client.messages.create(
            model=self._model,
            max_tokens=128,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=[_EMIT_DESCRICAO_TOOL],
            tool_choice={"type": "tool", "name": "emit_descricao"},
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        raw: dict[str, Any] = tool_block.input

        return ProbeResult(
            field_name=field_name,
            descricao=raw["descricao"],
            confidence=float(raw["confidence"]),
        )

    def update_yaml(
        self,
        yaml_path: Path,
        results: dict[str, ProbeResult],
    ) -> None:
        """Atualiza o YAML adicionando `descricao_auto_gerada:` após `descricao: "TODO"`.

        Não sobrescreve `descricao:` original. Não modifica campos que não estão em results.
        Idempotente: se `descricao_auto_gerada:` já existe, não adiciona duplicata.
        """
        text = yaml_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        output: list[str] = []
        i = 0
        current_field: str | None = None
        field_indent: str = ""

        while i < len(lines):
            line = lines[i]

            # Detect the start of a new field block: "      - field_name: cN"
            fn_match = re.match(r"^(\s+)-\s+field_name:\s+(\S+)", line)
            if fn_match:
                field_indent = fn_match.group(1) + "  "  # indent of sub-fields
                current_field = fn_match.group(2)
                output.append(line)
                i += 1
                continue

            # Detect descricao: "TODO" within the current field block
            if current_field is not None and re.match(r'^\s+descricao:\s+"TODO"', line):
                output.append(line)
                i += 1
                if current_field in results:
                    # Insert descricao_auto_gerada after descricao: "TODO"
                    # but only if the very next line doesn't already have it
                    next_line = lines[i] if i < len(lines) else ""
                    if "descricao_auto_gerada:" not in next_line:
                        desc = results[current_field].descricao.replace('"', '\\"')
                        output.append(f'{field_indent}descricao_auto_gerada: "{desc}"\n')
                continue

            output.append(line)
            i += 1

        yaml_path.write_text("".join(output), encoding="utf-8")
