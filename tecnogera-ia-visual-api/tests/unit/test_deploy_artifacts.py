"""Smoke tests dos artefatos de deploy (IAVS-003).

Garantem que o Makefile expõe os alvos esperados pela DoD (CONTRIBUTING.md)
e que o deploy.sh tem sintaxe válida e exige as variáveis críticas.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_makefile_existe_e_tem_alvos_essenciais() -> None:
    makefile = ROOT / "Makefile"
    assert makefile.exists(), "Makefile não encontrado"
    content = makefile.read_text(encoding="utf-8")
    for alvo in (
        "up",
        "down",
        "build",
        "test",
        "lint",
        "fmt",
        "type",
        "check",
        "deploy",
    ):
        assert (
            f"\n{alvo}:" in content or f"\n.PHONY: {alvo}\n" in content
        ), f"alvo '{alvo}' não definido no Makefile"


@pytest.mark.unit
def test_deploy_script_existe_e_e_executavel() -> None:
    script = ROOT / "deploy.sh"
    assert script.exists(), "deploy.sh não encontrado"
    assert script.stat().st_mode & 0o111, "deploy.sh não tem permissão de execução"


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash não disponível")
def test_deploy_script_tem_sintaxe_valida() -> None:
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "deploy.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash não disponível")
def test_deploy_script_falha_sem_vars_obrigatorias(tmp_path: Path) -> None:
    """Sem VPS_HOST/VPS_USER/VPS_PATH e sem .env.deploy, exit code = 1."""
    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    (fake_root / "deploy.sh").write_bytes((ROOT / "deploy.sh").read_bytes())
    (fake_root / "deploy.sh").chmod(0o755)

    result = subprocess.run(
        [str(fake_root / "deploy.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "VPS_HOST" in result.stderr


@pytest.mark.unit
def test_env_deploy_example_existe_e_lista_vars() -> None:
    example = ROOT / ".env.deploy.example"
    assert example.exists()
    content = example.read_text(encoding="utf-8")
    for var in ("VPS_HOST", "VPS_USER", "VPS_PATH"):
        assert var in content
