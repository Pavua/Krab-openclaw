#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверяет, кому на текущем Mac реально принадлежит живой runtime Краба/OpenClaw.

Что делает:
1) Снимает профиль текущей macOS-учётки: user, HOME и ожидаемые per-account пути.
2) Смотрит listener-процессы на ключевых портах `:8080`, `:18789`, `:8090`.
3) Читает live health-endpoints и сверяет, что `inbox_state` относится к текущему HOME.

Зачем:
- при разработке с нескольких macOS-учёток один и тот же репозиторий шарится безопасно,
  а runtime/auth/browser state шарить нельзя;
- этот скрипт нужен как truth-check перед restart, acceptance и handoff.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTS = (8080, 18789, 8090)


@dataclass(frozen=True)
class ListenerRow:
    """Описывает один слушающий процесс на конкретном TCP-порту."""

    command: str
    pid: int | None
    user: str
    port: int


def _http_json(url: str, *, timeout: float = 2.5) -> dict[str, Any]:
    """Безопасно читает локальный JSON endpoint и на ошибке возвращает structured payload."""
    req = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - локальные endpoints
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200) or 200),
                "json": json.loads(raw),
            }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": None,
            "error": str(exc),
        }


def _listener_rows(port: int) -> list[ListenerRow]:
    """Возвращает listener-процессы на порту через `lsof`."""
    try:
        completed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if completed.returncode != 0:
        return []

    rows: list[ListenerRow] = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        pid: int | None
        try:
            pid = int(parts[1])
        except ValueError:
            pid = None
        rows.append(
            ListenerRow(
                command=parts[0],
                pid=pid,
                user=parts[2],
                port=port,
            )
        )
    return rows


def _voice_gateway_commands() -> list[str]:
    """Собирает команды процессов, похожих на Krab Voice Gateway."""
    patterns = ("Krab Voice Gateway", "app.main:app", "krab-voice-gateway")
    commands: list[str] = []
    for pattern in patterns:
        try:
            completed = subprocess.run(
                ["pgrep", "-fl", pattern],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return []
        if completed.returncode != 0:
            continue
        for line in completed.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                commands.append(parts[1].strip())
    unique: list[str] = []
    seen: set[str] = set()
    for cmd in commands:
        if cmd in seen:
            continue
        seen.add(cmd)
        unique.append(cmd)
    return unique


def _extract_voice_gateway_owner(commands: Iterable[str]) -> str | None:
    """Пытается определить владельца Voice Gateway по --app-dir пути."""
    for cmd in commands:
        match = re.search(r"--app-dir\s+(/Users/[^\\s]+)", cmd)
        if match:
            path = Path(match.group(1))
            if len(path.parts) >= 3 and path.parts[1] == "Users":
                return path.parts[2]
        match = re.search(r"/Users/([^/]+)/[^\\n]*Krab Voice Gateway", cmd)
        if match:
            return match.group(1)
    return None


def _current_account_paths(home_dir: Path) -> dict[str, str]:
    """Возвращает ключевые per-account пути для текущей macOS-учётки."""
    runtime_state_dir = home_dir / ".openclaw" / "krab_runtime_state"
    return {
        "home_dir": str(home_dir),
        "openclaw_home": str(home_dir / ".openclaw"),
        "runtime_state_dir": str(runtime_state_dir),
        "inbox_state_path": str(runtime_state_dir / "inbox_state.json"),
        "launcher_lock_path": str(runtime_state_dir / "launcher.lock"),
        "openclaw_pid_path": str(runtime_state_dir / "openclaw.pid"),
        "openclaw_owner_path": str(runtime_state_dir / "openclaw.owner"),
    }


def build_runtime_report(
    *,
    current_user: str | None = None,
    home_dir: Path | None = None,
    ports: Iterable[int] = DEFAULT_PORTS,
    listener_provider: Callable[[int], list[ListenerRow]] = _listener_rows,
    voice_gateway_cmds_provider: Callable[[], list[str]] = _voice_gateway_commands,
    http_json: Callable[[str], dict[str, Any]] = _http_json,
) -> dict[str, Any]:
    """Собирает machine-readable runtime ownership report для текущей учётки."""
    user = (current_user or str(os.getenv("USER", "") or "").strip() or Path.home().name).strip()
    resolved_home = (home_dir or Path.home()).expanduser().resolve()
    account_paths = _current_account_paths(resolved_home)

    port_rows = {int(port): listener_provider(int(port)) for port in ports}
    port_summary: dict[str, Any] = {}
    foreign_listener_detected = False
    active_listener_detected = False
    for port, rows in port_rows.items():
        owners = sorted({row.user for row in rows})
        current_owned = all(owner == user for owner in owners) if owners else True
        foreign_on_port = any(owner != user for owner in owners)
        active_listener_detected = active_listener_detected or bool(rows)
        foreign_listener_detected = foreign_listener_detected or foreign_on_port
        port_summary[str(port)] = {
            "listening": bool(rows),
            "owners": owners,
            "owned_by_current_user": current_owned,
            "foreign_owner_detected": foreign_on_port,
            "processes": [
                {
                    "command": row.command,
                    "pid": row.pid,
                    "user": row.user,
                }
                for row in rows
            ],
        }

    health_lite = http_json("http://127.0.0.1:8080/api/health/lite")
    openclaw_health = http_json("http://127.0.0.1:18789/health")
    voice_gateway_health = http_json("http://127.0.0.1:8090/health")
    voice_gateway_commands = voice_gateway_cmds_provider()
    voice_gateway_owner = _extract_voice_gateway_owner(voice_gateway_commands)
    voice_gateway_owner_matches = voice_gateway_owner == user if voice_gateway_owner else None
    voice_gateway_foreign_detected = bool(voice_gateway_owner) and voice_gateway_owner != user

    inbox_state_path = ""
    inbox_home_matches = None
    operator_id = None
    if health_lite.get("ok"):
        payload = health_lite.get("json", {})
        inbox_summary = payload.get("inbox_summary", {}) if isinstance(payload, dict) else {}
        inbox_state_path = str(inbox_summary.get("state_path", "") or "")
        operator_id = inbox_summary.get("operator_id")
        if inbox_state_path:
            inbox_home_matches = inbox_state_path.startswith(str(resolved_home) + os.sep)

    foreign_runtime_detected = foreign_listener_detected or inbox_home_matches is False
    verdict = "clean"
    if foreign_runtime_detected:
        verdict = "foreign_runtime_detected"
    elif active_listener_detected:
        verdict = "current_account_runtime_active"
    elif not health_lite.get("ok") and not openclaw_health.get("ok"):
        verdict = "runtime_not_running"

    recommendations: list[str] = []
    if foreign_listener_detected:
        recommendations.append(
            "На тех же портах найден runtime другой macOS-учётки. Сначала останови его из исходной учётки, затем делай reclaim."
        )
    if inbox_home_matches is False:
        recommendations.append(
            "Health endpoint ссылается на чужой inbox_state. Такой runtime нельзя считать валидным для текущей учётки."
        )
    if verdict == "runtime_not_running":
        recommendations.append("Runtime сейчас не поднят. Это нормально для cold-start; перед live acceptance нужен fresh start.")
    if verdict == "current_account_runtime_active":
        recommendations.append("Runtime уже принадлежит текущей учётке. Можно безопасно продолжать локальные проверки.")
    if voice_gateway_foreign_detected:
        recommendations.append(
            "Voice Gateway выглядит как процесс другой macOS-учётки. Не останавливай его из текущей учётки."
        )
    if not recommendations:
        recommendations.append("Смешения между учётками не обнаружено.")

    return {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_root": str(ROOT),
        "current_account": {
            "user": user,
            "home_dir": str(resolved_home),
            "paths": account_paths,
        },
        "ports": port_summary,
        "health": {
            "web_health_lite": health_lite,
            "openclaw_health": openclaw_health,
            "voice_gateway_health": voice_gateway_health,
        },
        "voice_gateway": {
            "process_detected": bool(voice_gateway_commands),
            "owner_user": voice_gateway_owner,
            "owner_matches_current_user": voice_gateway_owner_matches,
            "commands_detected": len(voice_gateway_commands),
        },
        "ownership": {
            "observed_inbox_state_path": inbox_state_path or None,
            "inbox_state_matches_current_home": inbox_home_matches,
            "operator_id": operator_id,
            "foreign_listener_detected": foreign_listener_detected,
            "foreign_runtime_detected": foreign_runtime_detected,
            "active_listener_detected": active_listener_detected,
            "voice_gateway_owner_user": voice_gateway_owner,
            "voice_gateway_owner_matches_current_user": voice_gateway_owner_matches,
            "voice_gateway_foreign_detected": voice_gateway_foreign_detected,
            "verdict": verdict,
        },
        "recommendations": recommendations,
    }


def _print_human_report(report: dict[str, Any]) -> None:
    """Печатает короткий human-readable отчёт для double-click `.command` запуска."""
    account = report["current_account"]
    ownership = report["ownership"]
    print("🧭 Current Account Runtime Check")
    print(f"👤 User: {account['user']}")
    print(f"🏠 HOME: {account['home_dir']}")
    print(f"🧾 Verdict: {ownership['verdict']}")
    print("")
    print("Порты:")
    for port, info in report["ports"].items():
        if info["listening"]:
            owners = ", ".join(info["owners"]) or "unknown"
            status = "свой" if info["owned_by_current_user"] else "чужой"
            print(f"- {port}: LISTEN by {owners} [{status}]")
        else:
            print(f"- {port}: no listener")
    print("")
    inbox_path = ownership["observed_inbox_state_path"] or "—"
    print(f"Inbox state: {inbox_path}")
    print(f"Совпадает с текущим HOME: {ownership['inbox_state_matches_current_home']}")
    voice_gateway = report.get("voice_gateway", {})
    owner_user = voice_gateway.get("owner_user") or "—"
    owner_match = voice_gateway.get("owner_matches_current_user")
    if owner_match is True:
        owner_status = "совпадает"
    elif owner_match is False:
        owner_status = "другая учётка"
    else:
        owner_status = "не определено"
    print(f"Voice Gateway owner: {owner_user} ({owner_status})")
    print("")
    print("Рекомендации:")
    for item in report["recommendations"]:
        print(f"- {item}")


def main() -> int:
    """CLI entrypoint с двумя режимами: текстовый и JSON."""
    parser = argparse.ArgumentParser(description="Проверка ownership текущего runtime Краба/OpenClaw.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Печатать только JSON-отчёт.")
    args = parser.parse_args()

    report = build_runtime_report()
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human_report(report)

    ownership = report["ownership"]
    if ownership["foreign_runtime_detected"]:
        return 2
    if ownership["verdict"] == "runtime_not_running":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
