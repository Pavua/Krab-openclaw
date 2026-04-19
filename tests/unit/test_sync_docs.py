"""Tests for docs auto-sync composite script (standalone, no conftest import)."""

import subprocess
from pathlib import Path


def test_sync_docs_script_exists():
    """Verify sync_docs.py exists and is executable."""
    repo = Path(__file__).resolve().parent.parent.parent
    script = repo / "scripts" / "sync_docs.py"
    assert script.exists(), f"sync_docs.py not found at {script}"
    assert script.stat().st_mode & 0o111, "sync_docs.py is not executable"


def test_generate_scripts_exist():
    """Verify prerequisite generation scripts exist."""
    repo = Path(__file__).resolve().parent.parent.parent
    assert (repo / "scripts" / "generate_commands_cheatsheet.py").exists()
    assert (repo / "scripts" / "generate_docs_index.py").exists()


def test_sync_docs_help():
    """Test sync_docs.py --help works."""
    repo = Path(__file__).resolve().parent.parent.parent
    venv_py = repo / "venv/bin/python"
    script = repo / "scripts" / "sync_docs.py"

    result = subprocess.run(
        [str(venv_py), str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=repo,
    )
    assert result.returncode == 0, f"help failed: {result.stderr}"
    assert "auto-sync" in result.stdout or "usage" in result.stdout


def test_sync_docs_only_filter():
    """Test sync_docs.py --only filter accepts valid choices."""
    repo = Path(__file__).resolve().parent.parent.parent
    venv_py = repo / "venv/bin/python"
    script = repo / "scripts" / "sync_docs.py"

    # Valid filter should work (even if script returns 1 due to offline Krab)
    result = subprocess.run(
        [str(venv_py), str(script), "--only", "index"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=repo,
    )
    # Exit code doesn't matter (cheatsheet may fail), but command should parse
    assert "cheatsheet" not in result.stdout, "Should skip cheatsheet with --only index"
    assert "index" in result.stdout, "Should run index script"


def test_sync_docs_check_flag():
    """Test sync_docs.py --check flag exists."""
    repo = Path(__file__).resolve().parent.parent.parent
    venv_py = repo / "venv/bin/python"
    script = repo / "scripts" / "sync_docs.py"

    result = subprocess.run(
        [str(venv_py), str(script), "--check"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=repo,
    )
    # Should exit 0 or 1 depending on drift, but not syntax error
    assert "SyntaxError" not in result.stderr


def test_sync_docs_index_runs():
    """Test docs index generation works (no Krab needed)."""
    repo = Path(__file__).resolve().parent.parent.parent
    venv_py = repo / "venv/bin/python"
    script = repo / "scripts" / "generate_docs_index.py"

    result = subprocess.run(
        [str(venv_py), str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=repo,
    )
    assert result.returncode == 0, f"generate_docs_index failed: {result.stderr}"
    assert "Generated" in result.stdout or "docs/README.md" in result.stdout
