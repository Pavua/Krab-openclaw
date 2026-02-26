"""
Тесты для scripts/live_channel_smoke.py.

Проверяем локальную логику детекта паттернов без запуска реального openclaw CLI.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path


def _load_smoke_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "live_channel_smoke.py"
    spec = importlib.util.spec_from_file_location("live_channel_smoke", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_scan_patterns_marks_error_and_warning_severity():
    module = _load_smoke_module()
    lines = [
        "The model has crashed without additional information. (Exit code: null)",
        "krab-output-sanitizer: loaded without install/load-path provenance",
    ]
    patterns = [
        ("model_crash", re.compile(r"model has crashed", re.IGNORECASE), "error"),
        ("plugin_untracked", re.compile(r"without install/load-path provenance", re.IGNORECASE), "warn"),
    ]

    findings = module._scan_patterns(Path("/tmp/fake.log"), lines, patterns)

    assert len(findings) == 2
    assert findings[0]["code"] == "model_crash"
    assert findings[0]["severity"] == "error"
    assert findings[1]["code"] == "plugin_untracked"
    assert findings[1]["severity"] == "warn"


def test_pattern_specs_contains_sanitizer_config_invalid_guard():
    module = _load_smoke_module()
    codes = {item[0] for item in module.PATTERN_SPECS}
    assert "sanitizer_plugin_config_invalid" in codes
    assert "sanitizer_plugin_untracked_provenance" in codes


def test_tail_lines_for_missing_file_is_empty():
    module = _load_smoke_module()
    missing = Path("/tmp/definitely_missing_live_channel_smoke.log")
    assert module._tail_lines(missing, 100) == []
