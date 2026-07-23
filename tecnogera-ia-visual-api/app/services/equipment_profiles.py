"""Serviço de perfis de equipamentos — campos obrigatórios e validação de completude.

Carrega ``app/profiles/equipment_profiles.yaml`` uma vez na construção e expõe
operações de consulta. YAML malformado ou ausente vira ``ConfigurationError``;
perfil inexistente vira ``ResourceNotFoundError``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from app.core.exceptions import ConfigurationError, ResourceNotFoundError
from app.core.logging import get_logger
from app.models.equipment_profiles import (
    CompletenessReport,
    EquipmentProfile,
    FieldSpec,
)

if TYPE_CHECKING:  # pragma: no cover - apenas para tipos
    from collections.abc import Iterable

_log = get_logger(__name__)

_DEFAULT_PROFILES_PATH = (
    Path(__file__).resolve().parent.parent / "profiles" / "equipment_profiles.yaml"
)


class EquipmentProfileService:
    """Acesso a perfis de equipamentos definidos em YAML.

    Uso típico:

        service = EquipmentProfileService()
        perfil = service.get_profile("gerador")
        report = service.validate_completeness("gerador", ["C0_painel_frontal"])
    """

    def __init__(self, profiles_path: Path | None = None) -> None:
        self._path = profiles_path or _DEFAULT_PROFILES_PATH
        self._profiles: dict[str, EquipmentProfile] = self._load()

    def _load(self) -> dict[str, EquipmentProfile]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                "arquivo de perfis de equipamento não encontrado",
                details={"path": str(self._path), "reason": str(exc)},
            ) from exc

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                "YAML de perfis de equipamento malformado",
                details={"path": str(self._path), "reason": str(exc)},
            ) from exc

        if not isinstance(data, dict) or "profiles" not in data:
            raise ConfigurationError(
                "YAML de perfis sem chave 'profiles' no topo",
                details={"path": str(self._path), "reason": "missing_profiles_key"},
            )

        profiles_raw = data["profiles"]
        if not isinstance(profiles_raw, dict):
            raise ConfigurationError(
                "chave 'profiles' deve ser mapeamento de tipo→perfil",
                details={"path": str(self._path), "reason": "profiles_not_mapping"},
            )

        try:
            profiles = {
                tipo: self._build_profile(tipo, payload) for tipo, payload in profiles_raw.items()
            }
        except (TypeError, ValueError, KeyError) as exc:
            raise ConfigurationError(
                "perfil de equipamento inválido",
                details={"path": str(self._path), "reason": str(exc)},
            ) from exc

        _log.info(
            "equipment_profiles_carregados",
            path=str(self._path),
            tipos=list(profiles.keys()),
        )
        return profiles

    @staticmethod
    def _build_profile(tipo: str, payload: Any) -> EquipmentProfile:
        if not isinstance(payload, dict):
            raise TypeError(f"perfil '{tipo}' deve ser mapeamento")
        campos_raw = payload.get("campos", [])
        if not isinstance(campos_raw, list):
            raise TypeError(f"campos do perfil '{tipo}' devem ser lista")
        campos = tuple(FieldSpec(**c) for c in campos_raw)
        return EquipmentProfile(
            tipo=tipo,
            descricao=str(payload.get("descricao", "")),
            campos=campos,
        )

    def get_profile(self, tipo: str) -> EquipmentProfile:
        try:
            return self._profiles[tipo]
        except KeyError as exc:
            raise ResourceNotFoundError(
                f"perfil de equipamento '{tipo}' não encontrado",
                details={"tipo": tipo, "disponiveis": sorted(self._profiles.keys())},
            ) from exc

    def required_fields(self, tipo: str) -> list[str]:
        profile = self.get_profile(tipo)
        return [c.field_name for c in profile.campos if c.obrigatorio]

    def validate_completeness(
        self,
        tipo: str,
        found_fields: Iterable[str],
    ) -> CompletenessReport:
        profile = self.get_profile(tipo)
        encontrados = set(found_fields)
        obrigatorios = {c.field_name for c in profile.campos if c.obrigatorio}
        catalogados = {c.field_name for c in profile.campos}

        presentes = tuple(sorted(encontrados & catalogados))
        faltando = tuple(sorted(obrigatorios - encontrados))
        extras = tuple(sorted(encontrados - catalogados))

        return CompletenessReport(
            tipo=tipo,
            presentes=presentes,
            faltando_obrigatorios=faltando,
            extras=extras,
        )
