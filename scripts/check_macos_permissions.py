#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_macos_permissions.py — аудит macOS-разрешений и Gatekeeper для Краба.

Что это:
- локальный диагностический скрипт для multi-account и обычного one-click запуска;
- собирает truthful-срез по TCC/Gatekeeper-смежным разрешениям без внешних зависимостей.

Зачем:
- проблемы с Full Disk Access / Automation / Screen Recording часто выглядят как
  "ползунок включён, но ничего не работает";
- нужен единый reproducible-аудит, который можно прогнать перед свитчем учётки
  или при странных сбоях browser/system automation.

Связи:
- вызывается из `check_permissions.command`;
- дополняет readiness-скрипты и launcher-диагностику, но не заменяет их.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TCC_DB = Path.home() / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
DEFAULT_PROTECTED_PATHS = (
    Path.home() / "Library" / "Messages" / "chat.db",
    Path.home() / "Library" / "Safari" / "History.db",
    DEFAULT_TCC_DB,
)
TCC_SERVICES = (
    ("kTCCServiceSystemPolicyAllFiles", "Full Disk Access"),
    ("kTCCServiceAccessibility", "Accessibility"),
    ("kTCCServiceScreenCapture", "Screen Recording"),
    ("kTCCServiceAppleEvents", "Automation / Apple Events"),
)
CLIENT_HINTS = (
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "md.obsidian",
    "com.openai.codex",
    "com.todesktop.230313mzl4w4u92",
    "com.openai.chat",
    "com.todesktop.230313mzl4w4u92",  # desktop-shell кандидаты
    "com.cursor.Cursor",
    "com.google.Chrome",
    "com.anthropic.claudefordesktop",
)
QUARANTINE_PROBES = (
    ROOT / "check_permissions.command",
    ROOT / "start_krab.command",
    ROOT / "Start Full Ecosystem.command",
)


@dataclass
class CommandResult:
    ok: bool
    rc: int | None
    stdout: str
    stderr: str
    error: str = ""


def _run(args: list[str], *, timeout: float = 6.0) -> CommandResult:
    """Безопасно выполняет локальную команду и возвращает структурированный результат."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(ok=False, rc=None, stdout="", stderr="", error=str(exc))
    return CommandResult(
        ok=proc.returncode == 0,
        rc=int(proc.returncode),
        stdout=str(proc.stdout or "").strip(),
        stderr=str(proc.stderr or "").strip(),
        error="",
    )


def _probe_path_readability(path: Path) -> dict[str, Any]:
    """Проверяет, читается ли защищённый путь текущей учёткой/хост-приложением."""
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "readable": False,
        "error": "",
    }
    if not result["exists"]:
        result["error"] = "missing"
        return result
    try:
        with path.open("rb") as handle:
            handle.read(64)
        result["readable"] = True
    except OSError as exc:
        result["error"] = str(exc)
    return result


def _query_tcc_rows(db_path: Path, service: str) -> dict[str, Any]:
    """Снимает строки TCC по одному сервису через sqlite3, если БД доступна."""
    if not db_path.exists():
        return {
            "service": service,
            "db_path": str(db_path),
            "db_accessible": False,
            "rows": [],
            "error": "tcc_db_missing",
        }

    query = (
        "SELECT client, auth_value, auth_reason, auth_version "
        "FROM access "
        f"WHERE service = '{service}' "
        "ORDER BY client ASC;"
    )
    run = _run(["sqlite3", str(db_path), query], timeout=8.0)
    if not run.ok:
        return {
            "service": service,
            "db_path": str(db_path),
            "db_accessible": False,
            "rows": [],
            "error": run.error or run.stderr or f"sqlite_rc={run.rc}",
        }

    rows: list[dict[str, Any]] = []
    for line in (run.stdout or "").splitlines():
        if not line.strip():
            continue
        client, auth_value, auth_reason, auth_version = (line.split("|") + ["", "", "", ""])[:4]
        rows.append(
            {
                "client": client.strip(),
                "auth_value": auth_value.strip(),
                "auth_reason": auth_reason.strip(),
                "auth_version": auth_version.strip(),
            }
        )
    return {
        "service": service,
        "db_path": str(db_path),
        "db_accessible": True,
        "rows": rows,
        "error": "",
    }


def _summarize_tcc_service(payload: dict[str, Any], *, client_hints: tuple[str, ...] = CLIENT_HINTS) -> dict[str, Any]:
    """Сводит raw TCC-строки к компактному readiness-friendly summary."""
    rows = payload.get("rows") or []
    matched_rows = [
        row
        for row in rows
        if any(hint.lower() in str(row.get("client", "")).lower() for hint in client_hints)
    ]
    allowed_count = 0
    denied_count = 0
    for row in matched_rows:
        value = str(row.get("auth_value", "") or "").strip()
        if value in {"2", "3", "4"}:
            allowed_count += 1
        elif value in {"0", "1"}:
            denied_count += 1

    return {
        "service": payload.get("service"),
        "db_accessible": bool(payload.get("db_accessible")),
        "rows_total": len(rows),
        "matched_rows_count": len(matched_rows),
        "matched_rows": matched_rows,
        "allowed_count": allowed_count,
        "denied_count": denied_count,
        "error": str(payload.get("error") or ""),
    }


def _probe_system_events() -> dict[str, Any]:
    """Проверяет, может ли текущий хост говорить с System Events."""
    run = _run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get name of current user',
        ],
        timeout=8.0,
    )
    return {
        "ok": run.ok,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "error": run.error,
        "hint": (
            "Если здесь ошибка про Apple Events или not authorized, проверь Automation/Accessibility "
            "для приложения, из которого запускается Codex/Terminal."
        ),
    }


def _probe_gatekeeper() -> dict[str, Any]:
    """Снимает базовый статус Gatekeeper и quarantine-хвостов на launcher-файлах."""
    spctl = _run(["spctl", "--status"], timeout=6.0)
    quarantine: list[dict[str, Any]] = []
    for path in QUARANTINE_PROBES:
        entry: dict[str, Any] = {"path": str(path), "exists": path.exists(), "quarantined": False, "value": ""}
        if not path.exists():
            quarantine.append(entry)
            continue
        xattr = _run(["xattr", "-p", "com.apple.quarantine", str(path)], timeout=4.0)
        if xattr.ok and xattr.stdout:
            entry["quarantined"] = True
            entry["value"] = xattr.stdout
        quarantine.append(entry)
    return {
        "spctl_ok": spctl.ok,
        "spctl_stdout": spctl.stdout,
        "spctl_stderr": spctl.stderr,
        "spctl_error": spctl.error,
        "quarantine": quarantine,
    }


def build_report() -> dict[str, Any]:
    """Собирает итоговый аудит macOS permission/Gatekeeper readiness."""
    protected_paths = [_probe_path_readability(path) for path in DEFAULT_PROTECTED_PATHS]
    tcc_raw = [_query_tcc_rows(DEFAULT_TCC_DB, service) for service, _title in TCC_SERVICES]
    tcc_summary = [_summarize_tcc_service(item) for item in tcc_raw]

    readable_protected_count = sum(1 for item in protected_paths if item.get("readable"))
    tcc_accessible = all(bool(item.get("db_accessible")) for item in tcc_raw)

    return {
        "ok": True,
        "user": os.getenv("USER") or "",
        "home": str(Path.home()),
        "repo_root": str(ROOT),
        "protected_paths": protected_paths,
        "protected_paths_readable_count": readable_protected_count,
        "tcc_db_path": str(DEFAULT_TCC_DB),
        "tcc_db_accessible": tcc_accessible,
        "tcc": {
            "raw": tcc_raw,
            "summary": tcc_summary,
        },
        "system_events": _probe_system_events(),
        "gatekeeper": _probe_gatekeeper(),
    }


def _status_emoji(ok: bool) -> str:
    return "✅" if ok else "⚠️"


def _print_human_report(report: dict[str, Any]) -> None:
    """Печатает короткий, но actionable отчёт для терминала."""
    print("🔐 macOS Permission Audit for Krab")
    print(f"👤 User: {report.get('user')}")
    print(f"🏠 Home: {report.get('home')}")
    print(f"📂 Repo: {report.get('repo_root')}")
    print("")

    readable_count = int(report.get("protected_paths_readable_count") or 0)
    protected_paths = report.get("protected_paths") or []
    print(f"{_status_emoji(readable_count > 0)} Protected-path probes: {readable_count}/{len(protected_paths)} readable")
    for item in protected_paths:
        ok = bool(item.get("readable"))
        tail = str(item.get("path") or "")
        error = str(item.get("error") or "")
        print(f"  {_status_emoji(ok)} {tail}")
        if error and error != "missing":
            print(f"    ↳ {error}")

    print("")
    print(f"{_status_emoji(bool(report.get('tcc_db_accessible')))} TCC.db readable: {report.get('tcc_db_accessible')}")
    for item in report.get("tcc", {}).get("summary", []):
        label = next((title for service, title in TCC_SERVICES if service == item.get("service")), item.get("service"))
        ok = bool(item.get("db_accessible")) and int(item.get("matched_rows_count") or 0) > 0
        print(
            f"  {_status_emoji(ok)} {label}: matched={item.get('matched_rows_count')} "
            f"allowed={item.get('allowed_count')} denied={item.get('denied_count')}"
        )
        if item.get("error"):
            print(f"    ↳ {item.get('error')}")

    system_events = report.get("system_events") or {}
    print("")
    print(f"{_status_emoji(bool(system_events.get('ok')))} System Events automation: {system_events.get('ok')}")
    if system_events.get("stderr"):
        print(f"  ↳ {system_events.get('stderr')}")
    elif system_events.get("error"):
        print(f"  ↳ {system_events.get('error')}")

    gatekeeper = report.get("gatekeeper") or {}
    print("")
    print(f"{_status_emoji(bool(gatekeeper.get('spctl_ok')))} Gatekeeper status: {gatekeeper.get('spctl_stdout') or gatekeeper.get('spctl_stderr') or gatekeeper.get('spctl_error')}")
    quarantine = gatekeeper.get("quarantine") or []
    quarantined = [item for item in quarantine if item.get("quarantined")]
    print(f"{_status_emoji(not quarantined)} Launcher quarantine tails: {len(quarantined)}")
    for item in quarantined:
        print(f"  ⚠️ {item.get('path')}")
        if item.get("value"):
            print(f"    ↳ {item.get('value')}")

    print("")
    print("Подсказки:")
    print("- Если `TCC.db readable = False`, сначала выдай Full Disk Access приложению, из которого запускаешь Codex/Terminal.")
    print("- Если `System Events automation = False`, проверь Automation/Accessibility для Terminal / Codex / Cursor / Chrome.")
    print("- Если есть quarantine-хвосты, очисти их через `xattr -d com.apple.quarantine <path>` после проверки происхождения файла.")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Проверка macOS permission/Gatekeeper readiness для Краба.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Печатать отчёт в JSON.")
    args = parser.parse_args(argv)

    report = build_report()
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
