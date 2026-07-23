"""Testes de parse_event_path — IAVS-060."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.dropbox import parse_event_path


@pytest.mark.unit
class TestParseEventPathValido:
    def test_campos_completos(self) -> None:
        parsed = parse_event_path("/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg")
        assert parsed.asset_code == "FROTA001"
        assert parsed.moment == "saida"
        assert parsed.canonical_angle == "frontal"
        assert parsed.uploaded_by == "joao"
        assert parsed.extension == ".jpg"
        assert parsed.captured_at == datetime(2026, 6, 1, 14, 30, 22)
        assert parsed.checklist_id is None
        assert parsed.has_complete_metadata is True

    def test_com_checklist_id(self) -> None:
        parsed = parse_event_path(
            "/Avarias/FROTA001/20260601_143022_saida_frontal_joao_276800.jpg"
        )
        assert parsed.uploaded_by == "joao"
        assert parsed.checklist_id == "276800"
        assert parsed.has_complete_metadata is True

    def test_sem_checklist_id_continua_valido(self) -> None:
        parsed = parse_event_path("/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg")
        assert parsed.checklist_id is None
        assert parsed.has_complete_metadata is True

    def test_moment_retorno(self) -> None:
        parsed = parse_event_path("/Avarias/CAM027/20260610_083000_retorno_traseira_tec01.png")
        assert parsed.moment == "retorno"
        assert parsed.canonical_angle == "traseira"
        assert parsed.asset_code == "CAM027"

    def test_asset_code_com_hifen(self) -> None:
        parsed = parse_event_path("/Avarias/CAM-027/20260601_000000_saida_lateralesq_maria.jpg")
        assert parsed.asset_code == "CAM-027"
        assert parsed.has_complete_metadata is True

    def test_avarias_root_customizado(self) -> None:
        parsed = parse_event_path(
            "/OutraPasta/ATIVO1/20260101_120000_saida_frontal_tec01.jpg",
            avarias_root="/OutraPasta",
        )
        assert parsed.asset_code == "ATIVO1"
        assert parsed.has_complete_metadata is True

    def test_raw_preservado(self) -> None:
        path = "/Avarias/FROTA001/20260601_143022_saida_frontal_joao.jpg"
        parsed = parse_event_path(path)
        assert parsed.raw == path


@pytest.mark.unit
class TestParseEventPathInvalido:
    def test_filename_sem_padrao_retorna_metadata_missing(self) -> None:
        parsed = parse_event_path("/Avarias/FROTA001/foto.jpg")
        assert parsed.asset_code == "FROTA001"
        assert parsed.has_complete_metadata is False
        assert parsed.moment is None
        assert parsed.canonical_angle is None

    def test_momento_invalido_retorna_metadata_missing(self) -> None:
        # "ida" não é "saida" nem "retorno"
        parsed = parse_event_path("/Avarias/FROTA001/20260601_143022_ida_frontal_joao.jpg")
        assert parsed.has_complete_metadata is False

    def test_path_fora_da_raiz_levanta_valueerror(self) -> None:
        with pytest.raises(ValueError, match="não está sob"):
            parse_event_path("/Sisloc/FROTA001/20260601_143022_saida_frontal_joao.jpg")

    def test_path_sem_pasta_de_ativo_levanta_valueerror(self) -> None:
        # Arquivo diretamente na raiz /Avarias/ sem subpasta
        with pytest.raises(ValueError, match="sem pasta de ativo_code"):
            parse_event_path("/Avarias/20260601_143022_saida_frontal_joao.jpg")

    def test_pasta_de_sistema_anotados_levanta_valueerror(self) -> None:
        # Compostos gerados pelo pipeline não são eventos
        with pytest.raises(ValueError, match="pasta de sistema"):
            parse_event_path("/Avarias/_anotados/GER-001_2026-06-10.jpg")

    def test_pasta_de_sistema_gabaritos_levanta_valueerror(self) -> None:
        with pytest.raises(ValueError, match="pasta de sistema"):
            parse_event_path("/Avarias/_gabaritos/frontal.jpg")

    def test_data_invalida_retorna_captured_at_none(self) -> None:
        # Mês 99 — data inválida
        parsed = parse_event_path("/Avarias/X/20269901_143022_saida_frontal_joao.jpg")
        assert parsed.captured_at is None
        # Demais campos são extraídos corretamente mesmo com data inválida
        assert parsed.moment == "saida"
        assert parsed.canonical_angle == "frontal"

    def test_nome_sem_uploader(self) -> None:
        # Só 4 segmentos no stem (falta uploader)
        parsed = parse_event_path("/Avarias/FROTA001/20260601_143022_saida_frontal.jpg")
        assert parsed.has_complete_metadata is False
