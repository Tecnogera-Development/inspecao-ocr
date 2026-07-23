"""Validações estruturais do template de relatório (IAVS-010).

São smoke tests sobre os arquivos em ``docs/relatorio/`` para evitar que
mudanças acidentais quebrem a estrutura esperada pelo Modelo 3 (IAVS-011),
que vai consumir esses documentos como referência.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs" / "relatorio"

SECOES_TEMPLATE = (
    "# Relatório de Inspeção Visual",
    "## 1. Identificação",
    "## 2. Resumo executivo",
    "## 3. Análise por item",
    "## 4. Inconclusivas",
    "## 5. Não conformidades",
    "## 6. Recomendações",
    "## 7. Conclusão",
    "## 8. Limitações da análise",
)

NIVEIS_SEVERIDADE = ("Crítica", "Alta", "Média", "Baixa", "Info")


@pytest.mark.unit
def test_pasta_docs_relatorio_existe() -> None:
    assert DOCS.is_dir(), f"pasta {DOCS} não existe"


@pytest.mark.unit
@pytest.mark.parametrize(
    "arquivo",
    ["README.md", "template.md", "golden-sample-276800.md", "severidade.md"],
)
def test_arquivo_existe(arquivo: str) -> None:
    path = DOCS / arquivo
    assert path.is_file(), f"{arquivo} não encontrado em {DOCS}"
    assert path.stat().st_size > 200, f"{arquivo} parece vazio/curto demais"


@pytest.mark.unit
@pytest.mark.parametrize("secao", SECOES_TEMPLATE)
def test_template_tem_secao(secao: str) -> None:
    content = (DOCS / "template.md").read_text(encoding="utf-8")
    assert secao in content, f"seção '{secao}' ausente em template.md"


@pytest.mark.unit
@pytest.mark.parametrize("secao", SECOES_TEMPLATE)
def test_golden_sample_segue_template(secao: str) -> None:
    content = (DOCS / "golden-sample-276800.md").read_text(encoding="utf-8")
    assert secao in content, f"seção '{secao}' ausente em golden-sample-276800.md"


@pytest.mark.unit
@pytest.mark.parametrize("nivel", NIVEIS_SEVERIDADE)
def test_severidade_lista_todos_os_niveis(nivel: str) -> None:
    content = (DOCS / "severidade.md").read_text(encoding="utf-8")
    assert nivel in content, f"nível '{nivel}' ausente em severidade.md"


@pytest.mark.unit
def test_template_usa_placeholders_jinja_like() -> None:
    content = (DOCS / "template.md").read_text(encoding="utf-8")
    for placeholder in ("{{checklist.id}}", "{{filial.nome}}", "{{resumo.status}}"):
        assert placeholder in content, f"placeholder esperado ausente: {placeholder}"


@pytest.mark.unit
def test_golden_sample_nao_contem_placeholders_residuais() -> None:
    content = (DOCS / "golden-sample-276800.md").read_text(encoding="utf-8")
    assert "{{" not in content, "golden sample contém placeholders não preenchidos"
    assert "}}" not in content, "golden sample contém placeholders não preenchidos"
