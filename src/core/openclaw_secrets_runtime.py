# -*- coding: utf-8 -*-
"""
Утилиты runtime-перезагрузки секретов OpenClaw.

Зачем нужен:
- После переключения tier-ключа в models.json требуется применить изменения
  в живом gateway без ручного рестарта.

Связи:
- Вызывается из OpenClawClient при failover и из web write-endpoint.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from .subprocess_env import clean_subprocess_env

_DEFAULT_OPENCLAW_BIN_CANDIDATES = (
    "/opt/homebrew/bin/openclaw",
    "/usr/local/bin/openclaw",
)


def _resolve_openclaw_cli() -> dict[str, Any]:
    """
    Ищет исполняемый `openclaw` и честно объясняет, почему запуск невозможен.

    Почему это вынесено отдельно:
    - в multi-account режиме PATH может быть пустым или вести на owner-only symlink;
    - обычный `Permission denied` из subprocess скрывает реальную причину в UI;
    - web/runtime-диагностика должна различать `cli_not_found` и `cli_not_executable`.
    """
    candidates: list[tuple[str, str]] = []
    seen_paths: set[str] = set()

    env_path = str(os.getenv("OPENCLAW_BIN", "") or "").strip()
    if env_path:
        normalized = str(Path(env_path).expanduser())
        candidates.append(("env:OPENCLAW_BIN", normalized))
        seen_paths.add(normalized)

    path_candidate = shutil.which("openclaw")
    if path_candidate:
        normalized = str(Path(path_candidate).expanduser())
        if normalized not in seen_paths:
            candidates.append(("PATH", normalized))
            seen_paths.add(normalized)

    for raw_path in _DEFAULT_OPENCLAW_BIN_CANDIDATES:
        normalized = str(Path(raw_path).expanduser())
        if normalized not in seen_paths:
            candidates.append(("fallback", normalized))
            seen_paths.add(normalized)

    checked: list[dict[str, str]] = []
    saw_non_executable = False
    non_executable_candidate: dict[str, str] | None = None

    for source, candidate in candidates:
        record = {"source": source, "path": candidate, "reason": "not_found"}
        try:
            if not os.path.lexists(candidate):
                checked.append(record)
                continue
            if not os.access(candidate, os.X_OK):
                record["reason"] = "not_executable"
                checked.append(record)
                saw_non_executable = True
                non_executable_candidate = non_executable_candidate or record
                continue
            return {
                "ok": True,
                "path": candidate,
                "source": source,
                "checked": checked,
            }
        except OSError as exc:
            record["reason"] = "stat_error"
            record["detail"] = str(exc)
            checked.append(record)

    if saw_non_executable and non_executable_candidate:
        return {
            "ok": False,
            "error": "cli_not_executable",
            "path": non_executable_candidate.get("path", ""),
            "source": non_executable_candidate.get("source", ""),
            "checked": checked,
        }
    return {
        "ok": False,
        "error": "cli_not_found",
        "path": "",
        "source": "",
        "checked": checked,
    }


def get_openclaw_cli_runtime_status() -> dict[str, Any]:
    """
    Возвращает безопасный runtime-статус `openclaw` CLI без запуска команды.

    Нужен для UI и runtime-check:
    - показывает, возможен ли live reload вообще;
    - отделяет проблему ключа от проблемы прав/доступности CLI.
    """
    resolution = _resolve_openclaw_cli()
    return {
        "can_reload": bool(resolution.get("ok")),
        "error": "" if resolution.get("ok") else str(resolution.get("error") or "cli_not_found"),
        "cli_path": str(resolution.get("path") or ""),
        "cli_source": str(resolution.get("source") or ""),
        "checked": resolution.get("checked", []),
    }


async def reload_openclaw_secrets(timeout_sec: float = 25.0) -> dict[str, Any]:
    """Выполняет `openclaw secrets reload` и возвращает нормализованный результат."""
    cli_resolution = _resolve_openclaw_cli()
    if not cli_resolution.get("ok"):
        error_code = str(cli_resolution.get("error") or "cli_not_found")
        cli_path = str(cli_resolution.get("path") or "")
        cli_source = str(cli_resolution.get("source") or "")
        detail = f"path={cli_path}" if cli_path else "path="
        if cli_source:
            detail = f"{detail} source={cli_source}"
        return {
            "ok": False,
            "exit_code": 126 if error_code == "cli_not_executable" else 127,
            "error": error_code,
            "cli_path": cli_path,
            "cli_source": cli_source,
            "checked": cli_resolution.get("checked", []),
            "output": f"secrets_reload_{error_code}:{detail}",
        }

    cli_path = str(cli_resolution.get("path") or "openclaw")
    cli_source = str(cli_resolution.get("source") or "")
    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path,
            "secrets",
            "reload",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=clean_subprocess_env(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        output = stdout.decode("utf-8", errors="replace").strip()
        return {
            "ok": proc.returncode == 0,
            "exit_code": int(proc.returncode or 0),
            "error": "" if proc.returncode == 0 else "secrets_reload_failed",
            "cli_path": cli_path,
            "cli_source": cli_source,
            "output": output[-2000:],
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "exit_code": 124,
            "error": "secrets_reload_timeout",
            "cli_path": cli_path,
            "cli_source": cli_source,
            "output": "secrets_reload_timeout",
        }
    except PermissionError as exc:
        return {
            "ok": False,
            "exit_code": 126,
            "error": "cli_not_executable",
            "cli_path": cli_path,
            "cli_source": cli_source,
            "output": f"secrets_reload_cli_not_executable:path={cli_path} error={exc}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "exit_code": 1,
            "error": "secrets_reload_error",
            "cli_path": cli_path,
            "cli_source": cli_source,
            "output": f"secrets_reload_error:{exc}",
        }
