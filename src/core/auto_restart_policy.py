# -*- coding: utf-8 -*-
"""
auto_restart_policy — self-healing политика для инфраструктурных сервисов Краба.

Назначение: когда HTTP probe/ping обнаруживает degraded сервис (OpenClaw Gateway,
MCP yung_nagato / p0lrd / hammerspoon, inbox-watcher), `attempt_restart()` пытается
перезапустить его.

Ключевая особенность (Session 11 feature req #5):
- ДО вызова restart-команды проверяем launchd state. Если сервис выгружен
  (`launchctl print` возвращает non-zero или строку `state = not loaded`),
  делаем `launchctl bootstrap gui/<uid> <plist>` — это re-loads сервис
  обратно в launchd.
- Только после этого (если плейстик был подгружен ранее) выполняем custom
  restart cmd, либо полагаемся на KeepAlive=true плейсstick'а и просто ждём.

Инцидент 17.04.2026:
    OpenClaw gateway был `state = not loaded` в launchd. HTTP probe
    просто timeout'ил, auto_restart ничего не делал, потому что умел только
    HTTP/process restart, а не re-bootstrap launchd plist.

Env:
    AUTO_RESTART_ENABLED   — default "false". Если не "true"/"1"/"yes", policy
                             сразу возвращает ("disabled_by_env",) без действий.

Использование:
    from src.core.auto_restart_policy import AutoRestartPolicy
    policy = AutoRestartPolicy()
    ok, reason = await policy.attempt_restart("openclaw_gateway")
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .logger import get_logger
from .subprocess_env import clean_subprocess_env

logger = get_logger(__name__)


# ─── Env flag ─────────────────────────────────────────────────────────────────


def _auto_restart_enabled() -> bool:
    """Читаем флаг AUTO_RESTART_ENABLED при каждом вызове (test-friendly)."""
    raw = os.environ.get("AUTO_RESTART_ENABLED", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Для backward compat — некоторые old call sites читают AUTO_RESTART_ENABLED на импорте.
AUTO_RESTART_ENABLED = _auto_restart_enabled()


# ─── Service map ──────────────────────────────────────────────────────────────


# launchd-label → plist path резолвим лениво, потому что os.getuid() может
# различаться в тестах, а Path.home() — в sandbox.
def _plist_path(label: str) -> str:
    return str(Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")


def _launchctl_kickstart_cmd(label: str) -> list[str]:
    uid = str(os.getuid())
    return ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"]


SERVICES: dict[str, dict[str, Any]] = {
    "openclaw_gateway": {
        "restart_cmd": [
            "bash",
            "-c",
            "openclaw gateway stop && sleep 2 && openclaw gateway start",
        ],
        "launchd_label": "ai.openclaw.gateway",
        "plist_path": _plist_path("ai.openclaw.gateway"),
    },
    "mcp_yung_nagato": {
        "restart_cmd": _launchctl_kickstart_cmd("com.krab.mcp-yung-nagato"),
        "launchd_label": "com.krab.mcp-yung-nagato",
        "plist_path": _plist_path("com.krab.mcp-yung-nagato"),
    },
    "mcp_p0lrd": {
        "restart_cmd": _launchctl_kickstart_cmd("com.krab.mcp-p0lrd"),
        "launchd_label": "com.krab.mcp-p0lrd",
        "plist_path": _plist_path("com.krab.mcp-p0lrd"),
    },
    "mcp_hammerspoon": {
        "restart_cmd": _launchctl_kickstart_cmd("com.krab.mcp-hammerspoon"),
        "launchd_label": "com.krab.mcp-hammerspoon",
        "plist_path": _plist_path("com.krab.mcp-hammerspoon"),
    },
    "inbox_watcher": {
        "restart_cmd": _launchctl_kickstart_cmd("ai.krab.inbox-watcher"),
        "launchd_label": "ai.krab.inbox-watcher",
        "plist_path": _plist_path("ai.krab.inbox-watcher"),
    },
}

# Backward compat — некоторые call sites импортируют плоский RESTART_COMMANDS.
RESTART_COMMANDS: dict[str, list[str]] = {
    name: cfg["restart_cmd"] for name, cfg in SERVICES.items()
}


# ─── Launchd helpers ──────────────────────────────────────────────────────────


def is_service_loaded_in_launchd(service_label: str) -> bool:
    """
    Проверяет что launchd service загружен (state != not_loaded).

    Returns True если loaded/running, False если выгружен или недоступен.

    Детали:
        - `launchctl print gui/<uid>/<label>` возвращает non-zero exit code
          (обычно 113), когда сервис не загружен вовсе → False.
        - Иногда вывод содержит строку `state = not loaded` при промежуточном
          состоянии → тоже False.
        - При таймауте/OSError → консервативно False (нельзя верить что загружен).
    """
    try:
        uid = str(os.getuid())
        result = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{service_label}"],
            capture_output=True,
            text=True,
            timeout=5,
            env=clean_subprocess_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning(
            "auto_restart_launchd_probe_failed",
            label=service_label,
            error=str(exc),
        )
        return False

    if result.returncode != 0:
        return False

    for line in result.stdout.split("\n"):
        lowered = line.lower()
        if "state" in lowered and "not loaded" in lowered:
            return False
    return True


def bootstrap_service_if_unloaded(service_label: str, plist_path: str) -> tuple[bool, str]:
    """
    Если service выгружен — re-bootstrap.

    Returns (did_bootstrap, reason):
        - (False, "already_loaded") — ничего не делали, всё ОК.
        - (True, "bootstrap_ok") — успешно вернули в launchd.
        - (True, "bootstrap_failed: ...") — bootstrap запустили, но он упал.
        - (True, "bootstrap_error: ...") — исключение при запуске.
    """
    if is_service_loaded_in_launchd(service_label):
        return False, "already_loaded"

    try:
        uid = str(os.getuid())
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", plist_path],
            capture_output=True,
            text=True,
            timeout=10,
            env=clean_subprocess_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return True, f"bootstrap_error: {exc}"

    if result.returncode == 0:
        return True, "bootstrap_ok"
    stderr = (result.stderr or "").strip()[:100]
    return True, f"bootstrap_failed: {stderr}"


# ─── Policy ───────────────────────────────────────────────────────────────────


class AutoRestartPolicy:
    """
    Координирует попытки перезапуска сервисов.

    - Throttle: не чаще одного restart на сервис в RESTART_COOLDOWN_SEC.
    - launchd-aware: сначала проверяем plist загружен.
    - Subprocess: всегда через clean_subprocess_env().
    """

    RESTART_COOLDOWN_SEC = 180

    def __init__(self) -> None:
        self._last_attempt: dict[str, float] = {}

    def _is_on_cooldown(self, service: str) -> bool:
        last = self._last_attempt.get(service)
        if last is None:
            return False
        return (time.monotonic() - last) < self.RESTART_COOLDOWN_SEC

    def _mark_attempt(self, service: str) -> None:
        self._last_attempt[service] = time.monotonic()

    async def attempt_restart(self, service: str) -> tuple[bool, str]:
        """
        Попытаться перезапустить инфраструктурный сервис.

        Returns (success, reason).
        """
        if not _auto_restart_enabled():
            return False, "disabled_by_env"

        service_cfg = SERVICES.get(service)
        if not service_cfg:
            return False, "unknown_service"

        if self._is_on_cooldown(service):
            return False, "cooldown"

        self._mark_attempt(service)

        # Pre-check: launchd state. Если плейсstick выгружен — bootstrap его.
        launchd_label = service_cfg.get("launchd_label")
        plist_path = service_cfg.get("plist_path")
        if launchd_label and plist_path:
            did_bootstrap, reason = await asyncio.to_thread(
                bootstrap_service_if_unloaded, launchd_label, plist_path
            )
            if did_bootstrap:
                logger.warning(
                    "auto_restart_launchd_bootstrap",
                    service=service,
                    label=launchd_label,
                    reason=reason,
                )
                return reason.startswith("bootstrap_ok"), reason

        # Сервис загружен в launchd — используем custom restart cmd.
        restart_cmd = service_cfg.get("restart_cmd")
        if not restart_cmd:
            return False, "no_restart_cmd"

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                restart_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env=clean_subprocess_env(),
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning(
                "auto_restart_cmd_error",
                service=service,
                error=str(exc),
            )
            return False, f"restart_error: {exc}"

        if result.returncode == 0:
            logger.info("auto_restart_cmd_ok", service=service)
            return True, "restart_ok"
        stderr = (result.stderr or "").strip()[:100]
        logger.warning(
            "auto_restart_cmd_failed",
            service=service,
            rc=result.returncode,
            stderr=stderr,
        )
        return False, f"restart_failed: {stderr}"


# Default instance — для удобства импорта.
auto_restart_policy = AutoRestartPolicy()


__all__ = [
    "AUTO_RESTART_ENABLED",
    "AutoRestartPolicy",
    "RESTART_COMMANDS",
    "SERVICES",
    "auto_restart_policy",
    "bootstrap_service_if_unloaded",
    "is_service_loaded_in_launchd",
]
