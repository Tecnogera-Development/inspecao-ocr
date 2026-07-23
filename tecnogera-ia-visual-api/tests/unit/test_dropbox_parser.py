"""Testes do parser de nomenclatura de imagens (IAVS-004)."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.dropbox import parse_filename


@pytest.mark.unit
def test_parse_nome_completo_com_data_hora() -> None:
    parsed = parse_filename("276800_C0_painel_frontal_2026-04-15_14-32.jpg")
    assert parsed.checklist_id == "276800"
    assert parsed.field_name == "C0_painel_frontal"
    assert parsed.captured_at == datetime(2026, 4, 15, 14, 32)
    assert parsed.extension == ".jpg"


@pytest.mark.unit
def test_parse_nome_sem_timestamp() -> None:
    parsed = parse_filename("276800_C12_nivel_oleo.jpg")
    assert parsed.checklist_id == "276800"
    assert parsed.field_name == "C12_nivel_oleo"
    assert parsed.captured_at is None


@pytest.mark.unit
def test_parse_extensao_normalizada_para_minusculo() -> None:
    parsed = parse_filename("276800_C0_2026-04-15_14-32.JPG")
    assert parsed.extension == ".jpg"


@pytest.mark.unit
def test_parse_aceita_field_name_simples() -> None:
    parsed = parse_filename("276800_painel_2026-04-15_14-32.png")
    assert parsed.field_name == "painel"
    assert parsed.extension == ".png"


@pytest.mark.unit
@pytest.mark.parametrize(
    "filename",
    ["semseparador.jpg", "_so_underscore.jpg", "276800_.jpg"],
)
def test_parse_falha_em_nomes_invalidos(filename: str) -> None:
    with pytest.raises(ValueError, match="checklist_id"):
        parse_filename(filename)


@pytest.mark.unit
def test_parse_timestamp_invalido_resulta_em_captured_at_none() -> None:
    parsed = parse_filename("276800_C0_painel_2026-13-99_25-99.jpg")
    assert parsed.captured_at is None
    assert parsed.field_name == "C0_painel"


@pytest.mark.unit
def test_parse_formato_real_sisloc() -> None:
    parsed = parse_filename("153269005_checklist_276800_c33_0_10_04_2026 12_16_22.jpeg")
    assert parsed.checklist_id == "276800"
    assert parsed.field_name == "c33"
    assert parsed.captured_at == datetime(2026, 4, 10, 12, 16, 22)
    assert parsed.extension == ".jpeg"


@pytest.mark.unit
def test_parse_formato_real_aceita_campo_alfanumerico() -> None:
    parsed = parse_filename("153269005_checklist_276800_c187_0_10_04_2026 20_18_12.jpeg")
    assert parsed.field_name == "c187"
