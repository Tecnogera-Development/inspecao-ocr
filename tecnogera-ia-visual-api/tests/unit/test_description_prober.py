"""Testes do DescriptionProber — IAVS-048.

Usa clientes falsos (mock Anthropic) para rodar offline.
Valida que:
  - probe_field retorna ProbeResult com descricao e confidence
  - update_yaml adiciona descricao_auto_gerada sem sobrescrever descricao original
  - campos sem descricao: "TODO" são ignorados pelo update_yaml
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from app.services.description_prober import DescriptionProber, ProbeResult


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_tool_use_response(descricao: str, confidence: float = 0.85) -> MagicMock:
    """Cria resposta mock com tool_use emit_descricao."""
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "emit_descricao"
    tool_block.input = {"descricao": descricao, "confidence": confidence}
    response.content = [tool_block]
    usage = MagicMock()
    usage.input_tokens = 50
    usage.output_tokens = 10
    type(usage).cache_read_input_tokens = property(lambda self: 0)
    type(usage).cache_creation_input_tokens = property(lambda self: 0)
    response.usage = usage
    return response


def _make_prober(descricao: str = "Filtro de óleo do motor") -> DescriptionProber:
    """Cria DescriptionProber com cliente Anthropic mockado."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_tool_use_response(descricao)
    return DescriptionProber(_client=mock_client)


# ── tracer bullet ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_probe_field_retorna_probe_result_com_descricao() -> None:
    """probe_field chama LLM e retorna ProbeResult com descricao e confidence."""
    prober = _make_prober("Filtro de óleo do motor diesel.")
    images = [("img1.jpeg", b"\xff\xd8\xff"), ("img2.jpeg", b"\xff\xd8\xff")]

    result = prober.probe_field("c3", images)

    assert isinstance(result, ProbeResult)
    assert result.field_name == "c3"
    assert result.descricao == "Filtro de óleo do motor diesel."
    assert result.confidence == pytest.approx(0.85)


# ── update_yaml ───────────────────────────────────────────────────────────────


_MINIMAL_YAML = """\
profiles:
  F013_liberacao_gerador:
    campos:
      - field_name: c3
        legivel: Campo c3
        descricao: "TODO"
        obrigatorio: true
      - field_name: c0
        legivel: Painel frontal
        descricao: "Vista frontal do painel."
        obrigatorio: true
"""


@pytest.mark.unit
def test_update_yaml_adiciona_descricao_auto_gerada(tmp_path: Path) -> None:
    """update_yaml adiciona descricao_auto_gerada após descricao: 'TODO'."""
    yaml_file = tmp_path / "profiles.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    prober = _make_prober()
    results = {
        "c3": ProbeResult(field_name="c3", descricao="Filtro de óleo do motor diesel.", confidence=0.9),
    }
    prober.update_yaml(yaml_file, results)

    updated = yaml_file.read_text(encoding="utf-8")
    assert 'descricao: "TODO"' in updated, "descricao original deve ser mantida"
    assert 'descricao_auto_gerada: "Filtro de óleo do motor diesel."' in updated


@pytest.mark.unit
def test_update_yaml_idempotente(tmp_path: Path) -> None:
    """Chamar update_yaml duas vezes não duplica descricao_auto_gerada."""
    yaml_file = tmp_path / "profiles.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    prober = _make_prober()
    results = {
        "c3": ProbeResult(field_name="c3", descricao="Filtro de óleo.", confidence=0.9),
    }
    prober.update_yaml(yaml_file, results)
    prober.update_yaml(yaml_file, results)

    updated = yaml_file.read_text(encoding="utf-8")
    count = updated.count("descricao_auto_gerada:")
    assert count == 1, f"esperado 1 ocorrência de descricao_auto_gerada, mas encontrado {count}"


@pytest.mark.unit
def test_update_yaml_nao_sobrescreve_descricao_real(tmp_path: Path) -> None:
    """update_yaml não modifica campos que já têm descricao real (não TODO)."""
    yaml_file = tmp_path / "profiles.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    prober = _make_prober()
    # Tentamos atualizar c0, mas c0 não tem descricao: "TODO"
    results = {
        "c0": ProbeResult(field_name="c0", descricao="Algo inventado.", confidence=0.7),
    }
    prober.update_yaml(yaml_file, results)

    updated = yaml_file.read_text(encoding="utf-8")
    assert "descricao_auto_gerada" not in updated
