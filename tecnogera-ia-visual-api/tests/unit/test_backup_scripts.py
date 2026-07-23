"""Testes dos artefatos de backup — IAVS-053 (E7 parte 1)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


@pytest.mark.unit
def test_backup_postgres_script_existe() -> None:
    assert (SCRIPTS / "backup_postgres.sh").exists(), "backup_postgres.sh não encontrado"


@pytest.mark.unit
def test_backup_postgres_script_executavel() -> None:
    script = SCRIPTS / "backup_postgres.sh"
    assert script.stat().st_mode & 0o111, "backup_postgres.sh não tem permissão de execução"


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash não disponível")
def test_backup_postgres_script_sintaxe_valida() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPTS / "backup_postgres.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"sintaxe inválida: {result.stderr}"


@pytest.mark.unit
def test_backup_postgres_script_contem_pg_dump() -> None:
    content = (SCRIPTS / "backup_postgres.sh").read_text()
    assert "pg_dump" in content


@pytest.mark.unit
def test_backup_postgres_script_contem_gzip() -> None:
    content = (SCRIPTS / "backup_postgres.sh").read_text()
    assert "gzip" in content


@pytest.mark.unit
def test_backup_postgres_script_nao_usa_dropbox() -> None:
    content = (SCRIPTS / "backup_postgres.sh").read_text()
    assert "dropbox" not in content.lower()


@pytest.mark.unit
def test_backup_postgres_script_contem_retencao_local() -> None:
    content = (SCRIPTS / "backup_postgres.sh").read_text()
    assert "find" in content
    assert "mtime" in content
    assert "-delete" in content


@pytest.mark.unit
def test_backup_postgres_script_contem_log_json() -> None:
    content = (SCRIPTS / "backup_postgres.sh").read_text()
    assert "backups.log" in content
    assert "size_bytes" in content


@pytest.mark.unit
def test_cleanup_dropbox_script_removido() -> None:
    assert not (SCRIPTS / "cleanup_backups_dropbox.sh").exists(), \
        "cleanup_backups_dropbox.sh não deve existir — backup local apenas"


@pytest.mark.unit
def test_backup_docs_existe() -> None:
    docs = ROOT / "docs" / "operations" / "backup.md"
    assert docs.exists(), "docs/operations/backup.md não encontrado"


@pytest.mark.unit
def test_backup_docs_cobre_restore() -> None:
    docs = ROOT / "docs" / "operations" / "backup.md"
    content = docs.read_text()
    assert "restore" in content.lower() or "restaurar" in content.lower()


@pytest.mark.unit
def test_backup_docs_nao_menciona_dropbox() -> None:
    docs = ROOT / "docs" / "operations" / "backup.md"
    content = docs.read_text()
    assert "dropbox" not in content.lower()
