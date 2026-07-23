"""Schemas Pydantic para perfis de equipamentos e relatórios de completude."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FieldSpec(BaseModel):
    """Especificação de um campo de checklist para um tipo de equipamento."""

    model_config = ConfigDict(frozen=True)

    field_name: str = Field(..., description="Identificador técnico Sisloc (ex: c0, c57).")
    legivel: str = Field(..., description="Rótulo legível em PT-BR para o relatório.")
    descricao: str = Field(..., description="Descrição curta do que a foto deve mostrar.")
    obrigatorio: bool = True
    comum: bool = Field(False, description="Presente em ≥78% dos checklists mas não universal.")


class EquipmentProfile(BaseModel):
    """Perfil de um tipo de equipamento (ex: ``gerador``)."""

    model_config = ConfigDict(frozen=True)

    tipo: str
    descricao: str
    campos: tuple[FieldSpec, ...]


class CompletenessReport(BaseModel):
    """Resultado da validação de completude do checklist."""

    model_config = ConfigDict(frozen=True)

    tipo: str
    presentes: tuple[str, ...]
    faltando_obrigatorios: tuple[str, ...]
    extras: tuple[str, ...]

    @property
    def completo(self) -> bool:
        return not self.faltando_obrigatorios
