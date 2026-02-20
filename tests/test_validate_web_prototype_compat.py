"""
Тесты для scripts/validate_web_prototype_compat.py.

Проверяем, что валидатор:
1) фейлится на missing id,
2) фейлится на mock-маркерах,
3) проходит на совместимом прототипе.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_validator(base: Path, prototype: Path) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[1] / "scripts" / "validate_web_prototype_compat.py"
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--base",
            str(base),
            "--prototype",
            str(prototype),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validator_fails_on_missing_ids(tmp_path: Path):
    base = tmp_path / "base.html"
    prototype = tmp_path / "prototype.html"
    base.write_text('<div id="a"></div><div id="b"></div>', encoding="utf-8")
    prototype.write_text('<div id="a"></div>', encoding="utf-8")

    result = _run_validator(base, prototype)
    merged = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 1
    assert "missing ids: 1" in merged
    assert "b" in merged


def test_validator_fails_on_mock_markers(tmp_path: Path):
    base = tmp_path / "base.html"
    prototype = tmp_path / "prototype.html"
    base.write_text('<div id="a"></div>', encoding="utf-8")
    prototype.write_text(
        '<div id="a"></div>\n<script>// Mocked for Prototype View</script>',
        encoding="utf-8",
    )

    result = _run_validator(base, prototype)
    merged = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 1
    assert "mock markers: 1" in merged


def test_validator_passes_on_compatible_files(tmp_path: Path):
    base = tmp_path / "base.html"
    prototype = tmp_path / "prototype.html"
    base.write_text('<div id="a"></div><div id="b"></div>', encoding="utf-8")
    prototype.write_text('<div id="a"></div><div id="b"></div>', encoding="utf-8")

    result = _run_validator(base, prototype)
    merged = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "совместим для интеграции" in merged

