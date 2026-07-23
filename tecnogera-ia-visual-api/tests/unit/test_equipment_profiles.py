"""Testes unit do EquipmentProfileService (IAVS-009)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import ConfigurationError, ResourceNotFoundError
from app.services.equipment_profiles import EquipmentProfileService

_PROFILE_KEY = "F013_liberacao_gerador"

# obrigatorio=True (≥6/7 checklists): 27 universais + 5 em 6/7
_F013_OBRIGATORIOS = {
    "c0", "c3", "c4", "c6", "c33", "c38", "c39", "c55", "c57", "c60",
    "c71", "c80", "c81", "c82", "c83", "c88", "c89", "c93", "c95",
    "c107", "c108", "c145", "c166", "c167", "c187", "c227", "c228",
    "c50", "c53", "c85", "c109", "c229",
}

# comum=True, obrigatorio=False (≥5/7 checklists)
_F013_COMUNS = {
    "c5", "c12", "c175", "c194", "c195",
    "c230", "c231", "c234", "c236", "c237", "c238",
}

_F013_FIELDS = _F013_OBRIGATORIOS | _F013_COMUNS


@pytest.fixture
def service() -> EquipmentProfileService:
    return EquipmentProfileService()


@pytest.mark.unit
def test_perfil_f013_tem_43_campos(
    service: EquipmentProfileService,
) -> None:
    perfil = service.get_profile(_PROFILE_KEY)
    assert len(perfil.campos) == 43
    assert {c.field_name for c in perfil.campos} == _F013_FIELDS
    obrigatorios = {c.field_name for c in perfil.campos if c.obrigatorio}
    assert obrigatorios == _F013_OBRIGATORIOS
    comuns = {c.field_name for c in perfil.campos if c.comum}
    assert comuns == _F013_COMUNS
    assert all(c.legivel for c in perfil.campos)


@pytest.mark.unit
def test_get_profile_inexistente_levanta_resource_not_found(
    service: EquipmentProfileService,
) -> None:
    with pytest.raises(ResourceNotFoundError) as exc:
        service.get_profile("inexistente")
    assert exc.value.details["tipo"] == "inexistente"
    assert _PROFILE_KEY in exc.value.details["disponiveis"]


@pytest.mark.unit
def test_required_fields_f013(service: EquipmentProfileService) -> None:
    obrigatorios = service.required_fields(_PROFILE_KEY)
    assert len(obrigatorios) == 32
    assert set(obrigatorios) == _F013_OBRIGATORIOS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("encontrados", "esperado_faltando", "esperado_extras"),
    [
        pytest.param(_F013_FIELDS, set(), set(), id="completo"),
        pytest.param(
            {"c0", "c6"},
            _F013_OBRIGATORIOS - {"c0", "c6"},
            set(),
            id="faltando_obrigatorios",
        ),
        pytest.param(
            _F013_FIELDS | {"x99_campo_extra"},
            set(),
            {"x99_campo_extra"},
            id="extras",
        ),
        pytest.param(set(), _F013_OBRIGATORIOS, set(), id="vazio"),
    ],
)
def test_validate_completeness(
    service: EquipmentProfileService,
    encontrados: set[str],
    esperado_faltando: set[str],
    esperado_extras: set[str],
) -> None:
    report = service.validate_completeness(_PROFILE_KEY, encontrados)

    assert set(report.faltando_obrigatorios) == esperado_faltando
    assert set(report.extras) == esperado_extras
    assert set(report.presentes) == encontrados & _F013_FIELDS
    assert report.completo is (not esperado_faltando)


@pytest.mark.unit
def test_yaml_malformado_levanta_configuration_error(tmp_path: Path) -> None:
    arquivo = tmp_path / "broken.yaml"
    arquivo.write_text("profiles:\n  gerador: [::not yaml", encoding="utf-8")

    with pytest.raises(ConfigurationError) as exc:
        EquipmentProfileService(profiles_path=arquivo)

    assert exc.value.details["path"] == str(arquivo)
    assert "reason" in exc.value.details


@pytest.mark.unit
def test_yaml_inexistente_levanta_configuration_error(tmp_path: Path) -> None:
    arquivo = tmp_path / "nao-existe.yaml"
    with pytest.raises(ConfigurationError) as exc:
        EquipmentProfileService(profiles_path=arquivo)
    assert exc.value.details["path"] == str(arquivo)


@pytest.mark.unit
def test_yaml_sem_chave_profiles_levanta_configuration_error(tmp_path: Path) -> None:
    arquivo = tmp_path / "sem-profiles.yaml"
    arquivo.write_text("outra_chave: 123\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as exc:
        EquipmentProfileService(profiles_path=arquivo)
    assert exc.value.details["reason"] == "missing_profiles_key"


@pytest.mark.unit
def test_yaml_profiles_nao_mapping_levanta_configuration_error(tmp_path: Path) -> None:
    arquivo = tmp_path / "profiles-lista.yaml"
    arquivo.write_text("profiles:\n  - gerador\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as exc:
        EquipmentProfileService(profiles_path=arquivo)
    assert exc.value.details["reason"] == "profiles_not_mapping"


@pytest.mark.unit
def test_yaml_perfil_invalido_levanta_configuration_error(tmp_path: Path) -> None:
    arquivo = tmp_path / "perfil-invalido.yaml"
    arquivo.write_text(
        "profiles:\n  gerador:\n    descricao: x\n    campos: not-a-list\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        EquipmentProfileService(profiles_path=arquivo)
