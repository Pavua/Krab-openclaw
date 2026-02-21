#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw Ops Guard.

Зачем нужен:
- дать быстрый и понятный операционный срез по состоянию OpenClaw;
- поймать типовые поломки (конфликт порта, дубли gateway, invalid config);
- по флагу --fix применить безопасные remediation-шаги для боевого контура.

Связь с проектом:
- используется из .command-оберток в корне проекта;
- дополняет `openclaw_prod_status.command` и `openclaw_lab_beta.command`;
- снижает ручную рутину в Dashboard.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class CmdResult:
    """Результат выполнения shell-команды."""

    cmd: str
    code: int
    out: str
    err: str


def _run(cmd: list[str], timeout: int = 25) -> CmdResult:
    """Выполняет команду и возвращает stdout/stderr без исключений."""
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CmdResult(
            cmd=" ".join(cmd),
            code=completed.returncode,
            out=(completed.stdout or "").strip(),
            err=(completed.stderr or "").strip(),
        )
    except Exception as exc:  # noqa: BLE001
        return CmdResult(cmd=" ".join(cmd), code=127, out="", err=str(exc))


def _openclaw_cmd(openclaw_bin: str, profile: str, *tail: str) -> list[str]:
    """Собирает команду OpenClaw c учётом профиля."""
    cmd = [openclaw_bin]
    if profile != "main":
        cmd.extend(["--profile", profile])
    cmd.extend(list(tail))
    return cmd


def _profile_home(profile: str) -> Path:
    """Возвращает директорию профиля OpenClaw."""
    if profile == "main":
        return Path.home() / ".openclaw"
    return Path.home() / f".openclaw-{profile}"


def _auth_profile_path(profile: str) -> Path:
    """Путь к auth-профилям провайдеров модели."""
    return _profile_home(profile) / "agents" / "main" / "agent" / "auth-profiles.json"


def _read_profile_gateway_port(profile: str) -> int:
    """Читает gateway.port из openclaw.json профиля (fallback: 18789)."""
    config_path = _profile_home(profile) / "openclaw.json"
    if not config_path.exists():
        return 18789
    try:
        import json

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        gateway = payload.get("gateway", {}) if isinstance(payload, dict) else {}
        value = gateway.get("port", 18789) if isinstance(gateway, dict) else 18789
        port = int(value)
        return port if port > 0 else 18789
    except Exception:  # noqa: BLE001
        return 18789


def _log_candidates(project_root: Path) -> list[Path]:
    """Список логов, где ищем операционные маркеры."""
    today = dt.datetime.now().strftime("%Y-%m-%d")
    return [
        Path(f"/tmp/openclaw/openclaw-{today}.log"),
        project_root / "openclaw.log",
        project_root / "logs" / "krab.log",
    ]


def _scan_markers(text: str, markers: Iterable[str]) -> list[str]:
    """Возвращает найденные маркеры ошибок/аномалий."""
    found: list[str] = []
    lowered = text.lower()
    for marker in markers:
        if marker.lower() in lowered:
            found.append(marker)
    return found


def _tail(path: Path, max_lines: int = 220) -> str:
    """Читает хвост файла без зависимостей от внешних утилит."""
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:  # noqa: BLE001
        return ""


def _check_auth_permissions(path: Path) -> tuple[str, str]:
    """Проверяет права auth-файла: secure|insecure|missing."""
    if not path.exists():
        return "missing", f"Файл не найден: {path}"
    mode = stat.S_IMODE(path.stat().st_mode)
    secure = mode <= 0o600
    return ("secure" if secure else "insecure"), f"{path} (mode {oct(mode)})"


def _extract_pids_from_pgrep(text: str) -> list[int]:
    """Извлекает PID из вывода pgrep -fl (PID в начале строки)."""
    pids: list[int] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        head = line.split(maxsplit=1)[0]
        if head.isdigit():
            pids.append(int(head))
    return sorted(set(pids))


def _extract_pids_from_lsof(text: str) -> list[int]:
    """Извлекает PID из вывода lsof (2-й столбец после заголовка)."""
    pids: list[int] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("command"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return sorted(set(pids))


def main() -> int:
    """Точка входа скрипта."""
    parser = argparse.ArgumentParser(description="OpenClaw Ops Guard")
    parser.add_argument("--profile", default="main", help="Профиль OpenClaw: main|lab|<name>")
    parser.add_argument("--fix", action="store_true", help="Применить безопасные remediation-действия")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Корень проекта Краб",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    openclaw_bin = shutil.which("openclaw")
    if not openclaw_bin:
        print("❌ OpenClaw CLI не найден в PATH.")
        return 2

    profile = str(args.profile).strip() or "main"
    auth_path = _auth_profile_path(profile)
    expected_port = _read_profile_gateway_port(profile)

    print("🛡️ OpenClaw Ops Guard")
    print(f"Профиль: {profile}")
    print(f"OpenClaw: {openclaw_bin}")
    print(f"Проект: {project_root}")
    print(f"Время: {dt.datetime.now().isoformat(timespec='seconds')}")
    print()

    version = _run(_openclaw_cmd(openclaw_bin, profile, "--version"))
    status = _run(_openclaw_cmd(openclaw_bin, profile, "status"))
    gateway_status = _run(_openclaw_cmd(openclaw_bin, profile, "gateway", "status"))
    health = _run(_openclaw_cmd(openclaw_bin, profile, "health"))
    pgrep = _run(["pgrep", "-fl", "openclaw-gateway"])
    lsof = _run(["lsof", "-nP", f"-iTCP:{expected_port}", "-sTCP:LISTEN"])

    print("== Версия ==")
    print(version.out or version.err or "n/a")
    print()

    print("== Статус ==")
    print(status.out or status.err or "n/a")
    print()

    print("== Gateway ==")
    print(gateway_status.out or gateway_status.err or "n/a")
    print()

    print("== Health ==")
    print(health.out or health.err or "n/a")
    print()

    print("== Процессы openclaw-gateway ==")
    print(pgrep.out or "(не найдены)")
    print()

    print(f"== Порт {expected_port} ==")
    print(lsof.out or "(слушатель не найден)")
    print()

    auth_status, auth_line = _check_auth_permissions(auth_path)
    print("== Auth Profile ==")
    icon = {"secure": "✅ ", "insecure": "⚠️ ", "missing": "ℹ️ "}.get(auth_status, "ℹ️ ")
    print(icon + auth_line)
    print()

    marker_list = [
        "invalid config",
        "channel config schema unavailable",
        "port 18789 is already in use",
        "gateway already running",
        "lock timeout",
    ]
    found_markers: list[str] = []
    print("== Лог-маркеры ==")
    for candidate in _log_candidates(project_root):
        tail = _tail(candidate)
        if not tail:
            continue
        local_found = _scan_markers(tail, marker_list)
        if local_found:
            found_markers.extend(local_found)
            unique = ", ".join(sorted(set(local_found)))
            print(f"⚠️ {candidate}: {unique}")
    if not found_markers:
        print("✅ Критичные маркеры в хвосте логов не обнаружены.")
    print()

    # Оценка риска.
    pgrep_pids = _extract_pids_from_pgrep(pgrep.out)
    lsof_pids = _extract_pids_from_lsof(lsof.out)
    issues: list[str] = []
    if status.code != 0:
        issues.append("openclaw status завершился с ошибкой")
    if len(set(lsof_pids)) > 1:
        issues.append(f"на порту {expected_port} больше одного слушателя")
    if auth_status == "insecure":
        issues.append("небезопасные права auth-profiles.json")

    print("== Итог ==")
    if issues:
        for issue in issues:
            print(f"❗ {issue}")
    else:
        print("✅ Базовый операционный контур выглядит стабильно.")
    print()

    if args.fix:
        print("== Remediation (--fix) ==")
        # 1) Права auth-файла.
        if auth_path.exists():
            chmod_res = _run(["chmod", "600", str(auth_path)])
            if chmod_res.code == 0:
                print(f"✅ chmod 600: {auth_path}")
            else:
                print(f"⚠️ chmod не применён: {chmod_res.err or chmod_res.out}")
        else:
            print(f"ℹ️ auth файл отсутствует: {auth_path}")

        # 2) Доктор с авто-фиксом.
        doctor = _run(_openclaw_cmd(openclaw_bin, profile, "doctor", "--fix"), timeout=90)
        if doctor.code == 0:
            print("✅ openclaw doctor --fix выполнен")
        else:
            print("⚠️ doctor вернул не-нулевой код, см. вывод ниже")
        if doctor.out:
            print(doctor.out[-1500:])
        elif doctor.err:
            print(doctor.err[-1500:])

        # 3) Безопасная политика для Telegram groupPolicy (best-effort).
        policy_cmd = _openclaw_cmd(
            openclaw_bin,
            profile,
            "config",
            "set",
            "channels.telegram.groupPolicy",
            '"allowlist"',
            "--json",
        )
        policy = _run(policy_cmd)
        if policy.code == 0:
            print("✅ channels.telegram.groupPolicy=allowlist применено")
        else:
            print("ℹ️ groupPolicy не применён (возможно, ключ недоступен в этой сборке)")
            if policy.err:
                print(policy.err[-500:])

        # 4) Финальный статус.
        final_status = _run(_openclaw_cmd(openclaw_bin, profile, "status"))
        print("\n== Финальный статус ==")
        print(final_status.out or final_status.err or "n/a")
        print()

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
