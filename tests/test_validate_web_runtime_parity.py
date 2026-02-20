"""
Тесты для scripts/validate_web_runtime_parity.py.

Проверяем, что валидатор:
1) фейлится на stub-маркерах,
2) фейлится на missing required patterns,
3) проходит на валидном runtime JS.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REQUIRED_SNIPPETS = """
<script>
async function updateStats() { return 1; }
async function assistantQuery() { return 1; }
async function loadModelFeedbackStats() { return 1; }
async function runQuickDeepResearch() { return 1; }
document.getElementById('quickDeepBtn').addEventListener('click', runQuickDeepResearch);
document.getElementById('feedbackStatsBtn').addEventListener('click', loadModelFeedbackStats);
const A = '/api/model/catalog';
const B = '/api/assistant/query';
const C = '/api/ops/report';
</script>
""".strip()


def _run_validator(base: Path, prototype: Path, min_ratio: float = 0.60) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[1] / "scripts" / "validate_web_runtime_parity.py"
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--base",
            str(base),
            "--prototype",
            str(prototype),
            "--min-ratio",
            str(min_ratio),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_runtime_validator_fails_on_stub_marker(tmp_path: Path):
    base = tmp_path / "base.html"
    prototype = tmp_path / "prototype.html"

    base.write_text(f"<html><body>{REQUIRED_SNIPPETS}</body></html>", encoding="utf-8")
    prototype.write_text(
        "<html><body><script>"
        "Placeholder, real JS gets transferred here in Production"
        "</script></body></html>",
        encoding="utf-8",
    )

    result = _run_validator(base, prototype)
    merged = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 1
    assert "stub markers: 1" in merged


def test_runtime_validator_fails_on_missing_required_patterns(tmp_path: Path):
    base = tmp_path / "base.html"
    prototype = tmp_path / "prototype.html"

    base.write_text(f"<html><body>{REQUIRED_SNIPPETS}</body></html>", encoding="utf-8")
    prototype.write_text(
        "<html><body><script>async function updateStats() { return 1; }</script></body></html>",
        encoding="utf-8",
    )

    result = _run_validator(base, prototype, min_ratio=0.01)
    merged = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 1
    assert "required pattern misses" in merged


def test_runtime_validator_passes_on_valid_runtime(tmp_path: Path):
    base = tmp_path / "base.html"
    prototype = tmp_path / "prototype.html"

    base_html = f"<html><body>{REQUIRED_SNIPPETS}</body></html>"
    base.write_text(base_html, encoding="utf-8")
    prototype.write_text(base_html, encoding="utf-8")

    result = _run_validator(base, prototype)
    merged = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0
    assert "Runtime parity check пройден" in merged
