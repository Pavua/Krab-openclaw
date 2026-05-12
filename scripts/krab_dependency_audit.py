#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/krab_dependency_audit.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 110: weekly pip-audit run + JSON report в runtime state.

- Запускает `pip-audit --format=json` через subprocess (venv).
- Парсит вывод, агрегирует уязвимости по severity.
- Сохраняет последние 10 runs в ~/.openclaw/krab_runtime_state/dep_audit.json.
- Обновляет Prometheus Gauge krab_dependency_vulns_total{severity}.

Setup (один раз):
    venv/bin/pip install pip-audit

LaunchAgent: scripts/launchagents/ai.krab.dependency-audit.plist (Sat 04:00).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Путь к runtime state
DEFAULT_STATE_DIR = Path(os.path.expanduser("~/.openclaw/krab_runtime_state"))
DEFAULT_REPORT_PATH = DEFAULT_STATE_DIR / "dep_audit.json"
MAX_HISTORY = 10


def _resolve_pip_audit_binary() -> str | None:
    """Найти pip-audit: сначала в venv/, потом в PATH."""
    repo_root = Path(__file__).resolve().parent.parent
    venv_bin = repo_root / "venv" / "bin" / "pip-audit"
    if venv_bin.exists() and os.access(venv_bin, os.X_OK):
        return str(venv_bin)
    found = shutil.which("pip-audit")
    return found


def run_pip_audit(binary: str | None = None, timeout: int = 300) -> dict[str, Any]:
    """Запустить pip-audit --format=json и вернуть raw dict.

    Возвращает {"available": False, ...} если pip-audit не установлен.
    """
    bin_path = binary or _resolve_pip_audit_binary()
    if not bin_path:
        return {
            "available": False,
            "error": "pip-audit not installed; run `venv/bin/pip install pip-audit`",
        }
    try:
        proc = subprocess.run(
            [bin_path, "--format=json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"available": True, "error": "pip-audit timeout", "timeout": timeout}
    except OSError as exc:
        return {"available": True, "error": f"pip-audit exec failed: {exc}"}

    # pip-audit exits 1 если найдены уязвимости — это нормально, парсим stdout.
    raw_stdout = (proc.stdout or "").strip()
    if not raw_stdout:
        return {
            "available": True,
            "error": f"pip-audit empty stdout (rc={proc.returncode}, stderr={proc.stderr[:300]})",
        }
    try:
        parsed = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        return {"available": True, "error": f"pip-audit json parse failed: {exc}"}
    return {"available": True, "raw": parsed, "returncode": proc.returncode}


def parse_audit_output(raw: Any) -> list[dict[str, Any]]:
    """Извлечь vulnerabilities из pip-audit JSON.

    pip-audit формат (v2.x): {"dependencies": [{"name", "version", "vulns": [...]}, ...]}
    Старый: list of dependencies. Поддерживаем оба.
    """
    if isinstance(raw, dict):
        deps = raw.get("dependencies", [])
    elif isinstance(raw, list):
        deps = raw
    else:
        return []

    vulns: list[dict[str, Any]] = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        pkg = dep.get("name") or dep.get("package") or "unknown"
        ver = dep.get("version", "unknown")
        for v in dep.get("vulns", []) or []:
            if not isinstance(v, dict):
                continue
            vid = v.get("id") or v.get("aliases", ["UNKNOWN"])[0]
            severity = _extract_severity(v)
            vulns.append(
                {
                    "id": vid,
                    "package": pkg,
                    "version": ver,
                    "severity": severity,
                    "summary": (v.get("description") or v.get("summary") or "")[:300],
                    "fix_versions": v.get("fix_versions", []),
                }
            )
    return vulns


def _extract_severity(vuln: dict[str, Any]) -> str:
    """Достать severity из pip-audit vuln record (best-effort).

    pip-audit не всегда даёт severity напрямую. Пытаемся через aliases/cvss.
    """
    raw_sev = vuln.get("severity") or vuln.get("cvss_severity")
    if isinstance(raw_sev, str) and raw_sev.strip():
        return raw_sev.strip().lower()
    # CVSS score → bucket
    score = vuln.get("cvss_score") or vuln.get("cvss")
    if isinstance(score, (int, float)):
        if score >= 9.0:
            return "critical"
        if score >= 7.0:
            return "high"
        if score >= 4.0:
            return "medium"
        if score > 0:
            return "low"
    return "unknown"


def aggregate_by_severity(vulns: list[dict[str, Any]]) -> dict[str, int]:
    """Подсчитать CVE по severity."""
    counts: dict[str, int] = {}
    for v in vulns:
        sev = (v.get("severity") or "unknown").lower()
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def build_report(audit_result: dict[str, Any]) -> dict[str, Any]:
    """Свернуть результаты pip-audit в JSON report для персиста."""
    ts = datetime.now(timezone.utc).isoformat()
    if not audit_result.get("available"):
        return {
            "timestamp": ts,
            "status": "skipped",
            "error": audit_result.get("error", "pip-audit unavailable"),
            "total_vulns": 0,
            "by_severity": {},
            "vulnerabilities": [],
        }
    if "error" in audit_result:
        return {
            "timestamp": ts,
            "status": "error",
            "error": audit_result["error"],
            "total_vulns": 0,
            "by_severity": {},
            "vulnerabilities": [],
        }
    raw = audit_result.get("raw", {})
    vulns = parse_audit_output(raw)
    by_sev = aggregate_by_severity(vulns)
    return {
        "timestamp": ts,
        "status": "ok",
        "total_vulns": len(vulns),
        "by_severity": by_sev,
        "vulnerabilities": vulns,
    }


def persist_rolling(
    report: dict[str, Any],
    path: Path = DEFAULT_REPORT_PATH,
    max_history: int = MAX_HISTORY,
) -> None:
    """Дописать report в rolling log (last N runs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), list):
                history = loaded["runs"]
            elif isinstance(loaded, list):
                history = loaded
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(report)
    # Keep last N
    history = history[-max_history:]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump({"runs": history}, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def update_prometheus(by_severity: dict[str, int]) -> None:
    """Обновить Prometheus Gauge (no-op если prometheus_client отсутствует)."""
    try:
        # Lazy import чтобы скрипт работал в minimal env (без prometheus_client).
        from src.core.metrics.dep_audit import record_dependency_vulns

        record_dependency_vulns(by_severity)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    result = run_pip_audit()
    report = build_report(result)
    try:
        persist_rolling(report)
    except OSError as exc:
        print(f"dep_audit_persist_failed: {exc}", file=sys.stderr)
    update_prometheus(report.get("by_severity", {}))
    print(
        json.dumps(
            {
                "status": report["status"],
                "total_vulns": report["total_vulns"],
                "by_severity": report["by_severity"],
            },
            ensure_ascii=False,
        )
    )
    # exit 0 даже при наличии CVE — alert/metric это уже отражают.
    return 0


if __name__ == "__main__":
    sys.exit(main())
