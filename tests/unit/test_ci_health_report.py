# -*- coding: utf-8 -*-
"""Tests for scripts/ci_health_report.py — aggregator корректно собирает отчёт."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci_health_report.py"


def _load_module():
    """Загружаем scripts/ci_health_report.py как модуль (папка без __init__.py)."""
    spec = importlib.util.spec_from_file_location("ci_health_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, "cannot load ci_health_report"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def test_file_metrics_returns_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mod):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("# hello\nprint(1)\n")
    (src / "b.py").write_text("x=1\n")
    tests = tmp_path / "tests"
    tests.mkdir()

    monkeypatch.setattr(mod, "REPO", tmp_path)
    r = mod.file_metrics()
    assert r["py_files"] == 2
    assert r["total_lines"] >= 3


def test_make_report_contains_sections(mod):
    report = mod.make_report(
        {"passed": True, "errors": 0},
        {"passed": 100, "failed": 0, "skipped": 2, "errors": 0, "exit_ok": True},
        {"pct": "85%"},
        {"py_files": 150, "total_lines": 50000},
        {"recent": ["abc123 feat: X"], "branch": "main"},
    )
    assert "CI Health Report" in report
    assert "100 passed" in report
    assert "85%" in report
    assert "150" in report
    assert "main" in report


def test_make_report_flags_failures(mod):
    report = mod.make_report(
        {"passed": False, "errors": 3},
        {"passed": 50, "failed": 5, "skipped": 0, "errors": 0, "exit_ok": False},
        {"pct": "70%"},
        {"py_files": 10, "total_lines": 1000},
        {"recent": [], "branch": "feature/x"},
    )
    assert "5 failing tests" in report
    assert "3 lint errors" in report
    assert "feature/x" in report


def test_make_report_quick_mode(mod):
    report = mod.make_report(
        {"passed": True, "errors": 0},
        {"passed": 10, "failed": 0, "skipped": 0, "errors": 0, "exit_ok": True},
        {"pct": "skipped (--quick)"},
        {"py_files": 5, "total_lines": 100},
        {"recent": ["deadbeef init"], "branch": "main"},
    )
    assert "skipped (--quick)" in report
