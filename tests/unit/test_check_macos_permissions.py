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


def test_query_tcc_service_builds_inline_sql_and_parses_rows(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_tcc_db_candidates", lambda: [Path("/tmp/TCC.db")])
    monkeypatch.setattr(module, "_probe_path_readability", lambda path: {"exists": True, "readable": True, "error": ""})
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/sqlite3" if name == "sqlite3" else None)

    captured: list[list[str]] = []

    def _fake_run(command: list[str], *, timeout_sec: float = 6.0):
        captured.append(command)
        return {
            "ok": True,
            "returncode": 0,
            "stdout": '[{"client":"com.apple.Terminal","auth_value":"2","auth_reason":"4","auth_version":"1"}]',
            "stderr": "",
            "error": "",
        }

    monkeypatch.setattr(module, "_run_command", _fake_run)

    result = module._query_tcc_service("kTCCServiceSystemPolicyAllFiles")

    assert result["db_accessible"] is True
    assert result["rows"][0]["client"] == "com.apple.Terminal"
    assert "service = 'kTCCServiceSystemPolicyAllFiles'" in captured[0][3]


def test_probe_quarantine_does_not_treat_unsigned_script_as_quarantine(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    launcher = tmp_path / "start.command"
    launcher.write_text("#!/bin/bash\n", encoding="utf-8")

    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fake_run(command: list[str], *, timeout_sec: float = 6.0):
        if command[0].endswith("xattr"):
            return {"ok": True, "returncode": 0, "stdout": "com.apple.provenance\n", "stderr": "", "error": ""}
        if command[0].endswith("spctl"):
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": f"{launcher}: rejected\nsource=no usable signature",
                "error": "rejected",
            }
        raise AssertionError(command)

    monkeypatch.setattr(module, "_run_command", _fake_run)

    result = module._probe_quarantine([launcher])

    assert result["quarantine"][0]["quarantined"] is False
    assert result["quarantine"][0]["assessment_rejected"] is True


def test_build_readiness_summary_reports_blockers() -> None:
    module = _load_module()
    report = {
        "protected_paths": [{"readable": False}],
        "tcc_db_accessible": False,
        "tcc": {"summary": []},
        "system_events": {"ok": False},
        "gatekeeper": {"quarantine": [{"quarantined": True}]},
    }

    result = module._build_readiness_summary(report)

    assert result["overall_ready"] is False
    assert result["blocked_reasons"] == [
        "protected_paths_unreadable",
        "tcc_db_unavailable",
        "system_events_not_authorized",
        "launcher_quarantine_detected",
    ]


def test_build_readiness_summary_reports_ready_when_probes_are_green() -> None:
    module = _load_module()
    report = {
        "protected_paths": [{"readable": True}, {"readable": True}],
        "tcc_db_accessible": True,
        "tcc": {"summary": [{"matched_rows_count": 1}]},
        "system_events": {"ok": True},
        "gatekeeper": {"quarantine": [{"quarantined": False}]},
    }

    result = module._build_readiness_summary(report)

    assert result["overall_ready"] is True
    assert result["blocked_reasons"] == []
    assert result["matched_tcc_entries_detected"] is True


def test_write_artifact_writes_explicit_output(tmp_path: Path) -> None:
    module = _load_module()
    output_path = tmp_path / "permission_audit.json"
    report = {
        "user": "USER3",
        "readiness": {"overall_ready": True},
    }

    written_paths = module._write_artifact(report, output_path)

    assert written_paths == [str(output_path)]
    assert output_path.exists()
    assert '"overall_ready": true' in output_path.read_text(encoding="utf-8")
