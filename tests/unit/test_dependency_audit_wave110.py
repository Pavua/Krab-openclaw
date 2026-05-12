# -*- coding: utf-8 -*-
"""Wave 110: pip-audit weekly dependency vulnerability scan tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import krab_dependency_audit as audit

# --- parsing ----------------------------------------------------------------


def test_parse_audit_output_v2_dict_format() -> None:
    """pip-audit v2 формат: {"dependencies": [...]}."""
    raw = {
        "dependencies": [
            {
                "name": "urllib3",
                "version": "1.26.0",
                "vulns": [
                    {
                        "id": "PYSEC-2024-001",
                        "description": "Buffer overflow in urllib3",
                        "cvss_score": 9.5,
                        "fix_versions": ["1.26.18"],
                    }
                ],
            },
            {"name": "httpx", "version": "0.27.0", "vulns": []},
        ]
    }
    vulns = audit.parse_audit_output(raw)
    assert len(vulns) == 1
    assert vulns[0]["id"] == "PYSEC-2024-001"
    assert vulns[0]["package"] == "urllib3"
    assert vulns[0]["severity"] == "critical"  # cvss 9.5 → critical
    assert vulns[0]["fix_versions"] == ["1.26.18"]


def test_parse_audit_output_legacy_list_format() -> None:
    """Legacy формат: list of deps на верхнем уровне."""
    raw = [
        {
            "name": "requests",
            "version": "2.0",
            "vulns": [{"id": "GHSA-xxx", "severity": "MEDIUM"}],
        }
    ]
    vulns = audit.parse_audit_output(raw)
    assert len(vulns) == 1
    assert vulns[0]["severity"] == "medium"


def test_aggregate_by_severity_buckets() -> None:
    vulns = [
        {"severity": "critical"},
        {"severity": "critical"},
        {"severity": "high"},
        {"severity": "medium"},
        {"severity": "unknown"},
    ]
    counts = audit.aggregate_by_severity(vulns)
    assert counts["critical"] == 2
    assert counts["high"] == 1
    assert counts["medium"] == 1
    assert counts["unknown"] == 1


def test_extract_severity_from_cvss_score() -> None:
    assert audit._extract_severity({"cvss_score": 9.8}) == "critical"
    assert audit._extract_severity({"cvss_score": 7.5}) == "high"
    assert audit._extract_severity({"cvss_score": 5.0}) == "medium"
    assert audit._extract_severity({"cvss_score": 2.0}) == "low"
    assert audit._extract_severity({}) == "unknown"


# --- report build -----------------------------------------------------------


def test_build_report_skipped_when_pip_audit_missing() -> None:
    report = audit.build_report({"available": False, "error": "not installed"})
    assert report["status"] == "skipped"
    assert report["total_vulns"] == 0
    assert report["vulnerabilities"] == []
    assert "not installed" in report["error"]


def test_build_report_ok_with_vulns() -> None:
    raw = {
        "dependencies": [
            {
                "name": "pkg",
                "version": "1.0",
                "vulns": [{"id": "X", "cvss_score": 9.1}],
            }
        ]
    }
    report = audit.build_report({"available": True, "raw": raw, "returncode": 1})
    assert report["status"] == "ok"
    assert report["total_vulns"] == 1
    assert report["by_severity"]["critical"] == 1


# --- rolling persist --------------------------------------------------------


def test_persist_rolling_keeps_only_last_n(tmp_path: Path) -> None:
    """Rolling log хранит max 10 runs."""
    target = tmp_path / "dep_audit.json"
    for i in range(15):
        report = {
            "timestamp": f"2026-05-12T0{i % 10}:00:00",
            "status": "ok",
            "total_vulns": i,
            "by_severity": {},
            "vulnerabilities": [],
        }
        audit.persist_rolling(report, path=target, max_history=10)

    with target.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    runs = data["runs"]
    assert len(runs) == 10
    # Должны остаться последние 10 (i=5..14)
    assert runs[0]["total_vulns"] == 5
    assert runs[-1]["total_vulns"] == 14


def test_persist_rolling_handles_corrupt_existing(tmp_path: Path) -> None:
    target = tmp_path / "dep_audit.json"
    target.write_text("not valid json {{{", encoding="utf-8")
    report = {
        "timestamp": "2026-05-12T00:00:00",
        "status": "ok",
        "total_vulns": 0,
        "by_severity": {},
        "vulnerabilities": [],
    }
    audit.persist_rolling(report, path=target, max_history=10)
    with target.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert len(data["runs"]) == 1


# --- prometheus update (graceful) -------------------------------------------


def test_record_dependency_vulns_does_not_raise() -> None:
    """record_dependency_vulns не падает при пустом dict или отсутствии prometheus."""
    from src.core.metrics.dep_audit import record_dependency_vulns

    # Не падает даже если prometheus_client недоступен
    record_dependency_vulns({})
    record_dependency_vulns({"critical": 0, "high": 0})
    record_dependency_vulns({"critical": 2, "high": 1, "novel": 5})


# --- main flow --------------------------------------------------------------


def test_run_pip_audit_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_pip_audit graceful когда pip-audit не установлен."""
    monkeypatch.setattr(audit, "_resolve_pip_audit_binary", lambda: None)
    result = audit.run_pip_audit()
    assert result["available"] is False
    assert "pip-audit" in result["error"].lower()
