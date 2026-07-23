"""Classifier — orquestra LLMProvider para todas as imagens de um checklist.

Integra ShotBank (IAVS-003) e AnthropicProvider (IAVS-002) para classificação
com few-shot visual do perfil F013 (IAVS-004).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.services.dropbox import parse_filename  # noqa: F401  — usado por _sort_by_field
from app.services.llm_provider import _GENERIC_CLASSES

if TYPE_CHECKING:
    from app.services.llm_provider import AnthropicProvider, ClassificationResult, FakeLLMProvider
    from app.services.shot_bank import ShotBank

_log = get_logger(__name__)

SUPPORTED_PROFILES = {"F013_liberacao_gerador"}


def _sort_by_field(paths: list[Path]) -> list[Path]:
    """Agrupa imagens por field_name pra maximizar cache hit do shot bloco.

    Imagens com filename inválido ficam por último (mantém ordem original entre elas).
    """
    def key(p: Path) -> tuple[int, str, str]:
        try:
            parsed = parse_filename(p.name)
            field = parsed.field_name if parsed else None
        except ValueError:
            field = None
        return (0, field, p.name) if field else (1, "", p.name)

    return sorted(paths, key=key)


def _get_shots(
    shot_bank: ShotBank | None,
    filename: str,
) -> list[tuple[str, bytes]] | None:
    """Extrai shots do ShotBank para o campo da imagem, excluindo a própria."""
    if shot_bank is None:
        return None
    try:
        parsed = parse_filename(filename)
        field = parsed.field_name if parsed else None
    except ValueError:
        return None
    if not field:
        return None
    refs = shot_bank.select_shots(field, exclude=[filename])
    result = []
    for ref in refs:
        try:
            result.append((ref.filename, ref.path.read_bytes()))
        except OSError as exc:
            _log.warning(
                "shot_read_failed",
                filename=ref.filename,
                path=str(ref.path),
                error=str(exc),
            )
    return result or None


class ProfileNotSupportedError(Exception):
    """Perfil de formulário não mapeado."""

    def __init__(self, profile_id: str) -> None:
        super().__init__(f"Perfil '{profile_id}' não suportado")
        self.profile_id = profile_id


class Classifier:
    """Classifica imagens de um checklist usando o LLMProvider configurado."""

    def __init__(
        self, provider: FakeLLMProvider | AnthropicProvider, field_names: list[str]
    ) -> None:
        self._provider = provider
        self._field_names = field_names

    def classify_checklist(
        self,
        image_paths: list[Path],
        profile_id: str = "F013_liberacao_gerador",
        shot_bank: ShotBank | None = None,
    ) -> list[ClassificationResult]:
        is_fallback = profile_id not in SUPPORTED_PROFILES
        field_names = _GENERIC_CLASSES if is_fallback else self._field_names
        bank_hash = shot_bank.compute_hash() if shot_bank is not None else ""

        ordered_paths = _sort_by_field(image_paths)

        results: list[ClassificationResult] = []
        for path in ordered_paths:
            content = path.read_bytes() if path.exists() else b""
            shots = None if is_fallback else _get_shots(shot_bank, path.name)

            result = self._provider.classify_image(
                image_filename=path.name,
                image_bytes=content,
                field_names=field_names,
                shots=shots,
            )

            if is_fallback:
                result = result.model_copy(update={
                    "generic_class": result.field_name,
                    "field_name": None,
                    "requires_human_review": True,
                })

            if bank_hash:
                result = result.model_copy(update={"shot_bank_hash": bank_hash})

            results.append(result)
            _log.info(
                "classifier_image_done",
                filename=path.name,
                field_name=result.field_name,
                confidence=result.confidence,
            )
        return results
