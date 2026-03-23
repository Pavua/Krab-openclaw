# -*- coding: utf-8 -*-
"""
Тесты для `scripts/check_macos_permissions.py`.

Что проверяем:
- что helper корректно сводит raw TCC-строки в компактный summary;
- что probe чтения файла честно различает readable/missing path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_macos_permissions.py"
    spec = importlib.util.spec_from_file_location("check_macos_permissions", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_summarize_tcc_service_counts_allowed_and_denied() -> None:
    module = _load_module()
    payload = {
        "service": "kTCCServiceAccessibility",
        "db_accessible": True,
        "rows": [
            {"client": "com.apple.Terminal", "auth_value": "2", "auth_reason": "4", "auth_version": "1"},
            {"client": "com.googlecode.iterm2", "auth_value": "0", "auth_reason": "4", "auth_version": "1"},
            {"client": "org.example.Other", "auth_value": "2", "auth_reason": "4", "auth_version": "1"},
        ],
        "error": "",
    }

    result = module._summarize_tcc_service(payload, client_hints=("com.apple.Terminal", "com.googlecode.iterm2"))

    assert result["matched_rows_count"] == 2
    assert result["allowed_count"] == 1
    assert result["denied_count"] == 1


def test_probe_path_readability_marks_missing_file(tmp_path: Path) -> None:
    module = _load_module()
    missing_path = tmp_path / "missing.db"

    result = module._probe_path_readability(missing_path)

    assert result["exists"] is False
    assert result["readable"] is False
    assert result["error"] == "missing"


def test_probe_path_readability_reads_existing_file(tmp_path: Path) -> None:
    module = _load_module()
    sample = tmp_path / "sample.txt"
    sample.write_text("ok", encoding="utf-8")

    result = module._probe_path_readability(sample)

    assert result["exists"] is True
    assert result["readable"] is True
    assert result["error"] == ""
