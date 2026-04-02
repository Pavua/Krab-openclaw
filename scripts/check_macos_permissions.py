# -*- coding: utf-8 -*-
"""
check_macos_permissions.py — truthful аудит ключевых macOS-разрешений для Краба.

Зачем нужен:
- после обновлений macOS, Codex, Terminal или Chrome TCC-права часто выглядят
  как «сброшенные», хотя проблема локальна и её можно быстро локализовать;
- пользователю нужна attach-ready сводка по Full Disk Access / TCC / Automation /
  quarantine без ручного хождения по десятку системных панелей;
- launcher `check_permissions.command` должен сохранять evidence в `artifacts/ops`,
  чтобы следующий агент видел тот же вердикт, а не пересказывал проблему по памяти.

Связи:
- используется `check_permissions.command` и `macos_permission_audit.command`;
- опирается только на стандартные утилиты macOS (`sqlite3`, `xattr`, `spctl`,
  `osascript`) и не требует сторонних Python-зависимостей.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts" / "ops"

DEFAULT_CLIENT_HINTS = (
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "com.openai.codex",
    "com.openai.chatgpt",
    "com.google.Chrome",
)

DEFAULT_PROTECTED_PATHS = (
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / ".openclaw",
    PROJECT_ROOT,
)

DEFAULT_TCC_SERVICES = (
    "kTCCServiceAccessibility",
    "kTCCServiceScreenCapture",
    "kTCCServiceSystemPolicyAllFiles",
    "kTCCServiceAppleEvents",
)


def _run_command(command: list[str], *, timeout_sec: float = 6.0) -> dict[str, Any]:
    """Выполняет системную команду и возвращает компактный machine-readable результат."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
            "error": str(exc),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": int(completed.returncode),
        "stdout": str(completed.stdout or ""),
        "stderr": str(completed.stderr or ""),
        "error": "" if completed.returncode == 0 else str(completed.stderr or completed.stdout or "").strip(),
    }


def _probe_path_readability(path: Path) -> dict[str, Any]:
    """
    Проверяет, читается ли защищённый путь.

    Что считается truth:
    - отсутствующий путь → `missing`;
    - существующий, но не читаемый → текст ошибки;
    - читаемый файл/директория → `readable=True`.
    """
    target = Path(path).expanduser()
    if not target.exists():
        return {
            "path": str(target),
            "exists": False,
            "readable": False,
            "error": "missing",
        }

    try:
        if target.is_dir():
            with os.scandir(target) as iterator:
                next(iterator, None)
        else:
            with target.open("rb") as handle:
                handle.read(1)
    except Exception as exc:  # noqa: BLE001
        return {
            "path": str(target),
            "exists": True,
            "readable": False,
            "error": str(exc),
        }

    return {
        "path": str(target),
        "exists": True,
        "readable": True,
        "error": "",
    }


def _tcc_db_candidates() -> list[Path]:
    """Возвращает возможные TCC.db пути для текущей macOS-учётки."""
    return [
        Path.home() / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db",
        Path("/Library/Application Support/com.apple.TCC/TCC.db"),
    ]


def _query_tcc_service(service: str) -> dict[str, Any]:
    """Пытается прочитать записи по конкретному TCC service из user-level TCC.db."""
    db_path = next((item for item in _tcc_db_candidates() if item.exists()), _tcc_db_candidates()[0])
    path_probe = _probe_path_readability(db_path)
    if not path_probe["exists"]:
        return {
            "service": service,
            "db_path": str(db_path),
            "db_accessible": False,
            "rows": [],
            "error": "missing",
        }

    sqlite3_path = shutil.which("sqlite3")
    if not sqlite3_path:
        return {
            "service": service,
            "db_path": str(db_path),
            "db_accessible": False,
            "rows": [],
            "error": "sqlite3_not_found",
        }

    escaped_service = str(service or "").replace("'", "''")
    query = (
        "SELECT client, auth_value, auth_reason, auth_version "
        f"FROM access WHERE service = '{escaped_service}' ORDER BY client ASC;"
    )
    command = [sqlite3_path, "-json", str(db_path), query]
    result = _run_command(command, timeout_sec=6.0)
    if not result["ok"]:
        return {
            "service": service,
            "db_path": str(db_path),
            "db_accessible": False,
            "rows": [],
            "error": str(result["error"] or "query_failed"),
        }

    try:
        rows = json.loads(result["stdout"] or "[]")
    except Exception as exc:  # noqa: BLE001
        return {
            "service": service,
            "db_path": str(db_path),
            "db_accessible": False,
            "rows": [],
            "error": f"json_decode_failed: {exc}",
        }

    if not isinstance(rows, list):
        rows = []

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized_rows.append(
            {
                "client": str(row.get("client") or ""),
                "auth_value": str(row.get("auth_value") or ""),
                "auth_reason": str(row.get("auth_reason") or ""),
                "auth_version": str(row.get("auth_version") or ""),
            }
        )

    return {
        "service": service,
        "db_path": str(db_path),
        "db_accessible": True,
        "rows": normalized_rows,
        "error": "",
    }


def _summarize_tcc_service(payload: dict[str, Any], client_hints: tuple[str, ...] = DEFAULT_CLIENT_HINTS) -> dict[str, Any]:
    """Сворачивает raw TCC-строки в compact summary по интересующим клиентам."""
    rows = payload.get("rows") if isinstance(payload, dict) else []
    rows = rows if isinstance(rows, list) else []
    normalized_hints = tuple(str(item or "").strip() for item in client_hints if str(item or "").strip())

    matched_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and any(str(row.get("client") or "").strip() == hint for hint in normalized_hints)
    ]

    allowed_count = 0
    denied_count = 0
    for row in matched_rows:
        auth_value = str(row.get("auth_value") or "").strip()
        if auth_value == "2":
            allowed_count += 1
        elif auth_value == "0":
            denied_count += 1

    return {
        "service": str(payload.get("service") or ""),
        "db_accessible": bool(payload.get("db_accessible")),
        "matched_rows_count": len(matched_rows),
        "allowed_count": allowed_count,
        "denied_count": denied_count,
        "matched_clients": [str(row.get("client") or "") for row in matched_rows],
        "error": str(payload.get("error") or ""),
    }


def _probe_system_events_authorization() -> dict[str, Any]:
    """Проверяет, может ли текущий процесс обратиться к System Events."""
    result = _run_command(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get name of every process',
        ],
        timeout_sec=6.0,
    )
    return {
        "ok": bool(result["ok"]),
        "error": str(result["error"] or ""),
    }


def _probe_quarantine(paths: list[Path]) -> dict[str, Any]:
    """Проверяет, висит ли quarantine/xattr на ключевых launcher-файлах."""
    quarantine: list[dict[str, Any]] = []
    xattr_path = shutil.which("xattr")
    spctl_path = shutil.which("spctl")
    for path in paths:
        item = {
            "path": str(path),
            "exists": path.exists(),
            "quarantined": False,
            "assessment_rejected": False,
            "detail": "",
        }
        if not path.exists():
            quarantine.append(item)
            continue

        if xattr_path:
            result = _run_command([xattr_path, str(path)], timeout_sec=4.0)
            attrs = result["stdout"].splitlines() if result["stdout"] else []
            if any(attr.strip() == "com.apple.quarantine" for attr in attrs):
                item["quarantined"] = True
                item["detail"] = "xattr:com.apple.quarantine"

        if not item["quarantined"] and spctl_path:
            result = _run_command([spctl_path, "--assess", "-vv", str(path)], timeout_sec=4.0)
            detail = str(result["stderr"] or result["stdout"] or "").strip()
            if "rejected" in detail.lower():
                # Unsigned локальный launcher может быть `rejected` у Gatekeeper без
                # quarantine xattr. Блокером считаем именно карантин, а этот verdict
                # сохраняем как диагностическую подсказку.
                item["assessment_rejected"] = True
                item["detail"] = detail

        quarantine.append(item)

    return {"quarantine": quarantine}


def _build_readiness_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Собирает практический verdict по audit-отчёту."""
    blocked_reasons: list[str] = []

    protected_paths = report.get("protected_paths") if isinstance(report, dict) else []
    protected_paths = protected_paths if isinstance(protected_paths, list) else []
    if any(not bool(item.get("readable")) for item in protected_paths if isinstance(item, dict)):
        blocked_reasons.append("protected_paths_unreadable")

    if not bool(report.get("tcc_db_accessible")):
        blocked_reasons.append("tcc_db_unavailable")

    system_events = report.get("system_events") if isinstance(report, dict) else {}
    if not bool((system_events or {}).get("ok")):
        blocked_reasons.append("system_events_not_authorized")

    gatekeeper = report.get("gatekeeper") if isinstance(report, dict) else {}
    quarantine = (gatekeeper or {}).get("quarantine") if isinstance(gatekeeper, dict) else []
    quarantine = quarantine if isinstance(quarantine, list) else []
    if any(bool(item.get("quarantined")) for item in quarantine if isinstance(item, dict)):
        blocked_reasons.append("launcher_quarantine_detected")

    tcc_summary = (report.get("tcc") or {}).get("summary") if isinstance(report.get("tcc"), dict) else []
    tcc_summary = tcc_summary if isinstance(tcc_summary, list) else []
    matched_tcc_entries_detected = any(int(item.get("matched_rows_count") or 0) > 0 for item in tcc_summary if isinstance(item, dict))

    return {
        "overall_ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "matched_tcc_entries_detected": matched_tcc_entries_detected,
    }


def _write_artifact(report: dict[str, Any], output_path: Path | None = None) -> list[str]:
    """Пишет JSON-артефакт в explicit output или в `artifacts/ops` latest+timestamp."""
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    written_paths: list[str] = []

    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        return [str(target)]

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    user = str(report.get("user") or "unknown").lower()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    latest_path = ARTIFACTS_DIR / f"macos_permission_audit_{user}_latest.json"
    stamp_path = ARTIFACTS_DIR / f"macos_permission_audit_{user}_{stamp}.json"
    for target in (latest_path, stamp_path):
        target.write_text(payload, encoding="utf-8")
        written_paths.append(str(target))
    return written_paths


def build_permission_report() -> dict[str, Any]:
    """Собирает полный audit-пакет по текущей macOS-учётке."""
    protected_paths = [_probe_path_readability(path) for path in DEFAULT_PROTECTED_PATHS]
    tcc_raw = [_query_tcc_service(service) for service in DEFAULT_TCC_SERVICES]
    tcc_summary = [_summarize_tcc_service(item) for item in tcc_raw]
    tcc_db_accessible = all(bool(item.get("db_accessible")) for item in tcc_raw)

    launcher_paths = [
        PROJECT_ROOT / "new start_krab.command",
        PROJECT_ROOT / "check_permissions.command",
        PROJECT_ROOT / "new Open Owner Chrome Remote Debugging.command",
    ]

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "user": os.getenv("USER", ""),
        "project_root": str(PROJECT_ROOT),
        "protected_paths": protected_paths,
        "tcc_db_accessible": tcc_db_accessible,
        "tcc": {
            "raw": tcc_raw,
            "summary": tcc_summary,
        },
        "system_events": _probe_system_events_authorization(),
        "gatekeeper": _probe_quarantine(launcher_paths),
    }
    report["readiness"] = _build_readiness_summary(report)
    return report


def main() -> int:
    """CLI entrypoint: печатает compact verdict и пишет JSON evidence."""
    parser = argparse.ArgumentParser(description="Проверка macOS permissions для Краба.")
    parser.add_argument("--output", type=Path, default=None, help="Явный путь для JSON-отчёта.")
    parser.add_argument("--json", action="store_true", help="Печатать полный JSON в stdout.")
    args = parser.parse_args()

    report = build_permission_report()
    written_paths = _write_artifact(report, args.output)
    report["written_paths"] = written_paths

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        readiness = report["readiness"]
        print(f"Practical readiness: {readiness['overall_ready']}")
        print(f"Blocked reasons: {', '.join(readiness['blocked_reasons']) or 'none'}")
        print(f"TCC DB accessible: {report['tcc_db_accessible']}")
        print(f"System Events authorized: {report['system_events']['ok']}")
        quarantined = sum(
            1 for item in report["gatekeeper"]["quarantine"] if isinstance(item, dict) and bool(item.get("quarantined"))
        )
        print(f"Quarantine findings: {quarantined}")
        print("Artifacts:")
        for path in written_paths:
            print(f"- {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
