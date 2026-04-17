# -*- coding: utf-8 -*-
"""
auto_restart_policy.py — rate-limited restart-by-service для Krab.

Назначение:
- расширяет passive monitoring proactive_watch активным самолечением;
- перезапускает упавшие сервисы (OpenClaw gateway, MCP серверы) под LaunchAgents;
- защищён rate-limit'ом и экспоненциальным cooldown'ом, чтобы не уйти в loop.

Инварианты:
- Max N попыток в час на сервис (default 3).
- Экспоненциальный cooldown между попытками: 60s → 2min → 5min → cap 10min.
- Telegram DM владельцу на каждую попытку restart (success/fail).
- AUTO_RESTART_ENABLED env-флаг (default false) — явный opt-in владельца.
- Сброс consecutive_failures при успешном restart.

Дизайн:
- `ServiceRestartState` — per-service состояние с инкрементальной деградацией.
- `AutoRestartManager` — синглтон, хранит состояния и выполняет subprocess вызов.
- Notification callback асинхронный, Telegram-safe (owner-only).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Sequence

from .logger import get_logger
from .subprocess_env import clean_subprocess_env

log = get_logger(__name__)

# Дефолты можно переопределять через env для dry-run / stress-тестов.
DEFAULT_MAX_ATTEMPTS_PER_HOUR = 3
DEFAULT_COOLDOWNS_SEC: list[int] = [60, 120, 300, 600]  # Экспоненциальный backoff
DEFAULT_RESTART_TIMEOUT_SEC = 30

# Карта сервисов → restart commands. Импортируется proactive_watch/userbot_bridge
# для пробрасывания при реальных health failures. `bash -c` для openclaw,
# launchctl для MCP под LaunchAgent.
RESTART_COMMANDS: dict[str, list[str]] = {
    "openclaw_gateway": [
        "bash",
        "-c",
        "openclaw gateway stop && sleep 2 && openclaw gateway start",
    ],
    "mcp_yung_nagato": [
        "launchctl",
        "kickstart",
        "-k",
        f"gui/{os.getuid()}/com.krab.mcp-yung-nagato",
    ],
    "mcp_p0lrd": [
        "launchctl",
        "kickstart",
        "-k",
        f"gui/{os.getuid()}/com.krab.mcp-p0lrd",
    ],
    "mcp_hammerspoon": [
        "launchctl",
        "kickstart",
        "-k",
        f"gui/{os.getuid()}/com.krab.mcp-hammerspoon",
    ],
}


def is_auto_restart_enabled() -> bool:
    """Читаем flag в runtime (чтобы тесты могли менять env через monkeypatch)."""
    return os.environ.get("AUTO_RESTART_ENABLED", "false").lower() == "true"


@dataclass
class ServiceRestartState:
    """Per-service tracking attempts + cooldown."""

    service_name: str
    attempts: list[datetime] = field(default_factory=list)  # Timestamps попыток (UTC)
    last_restart: datetime | None = None
    consecutive_failures: int = 0

    def can_restart(self, now: datetime | None = None) -> tuple[bool, str]:
        """Проверка rate-limit + cooldown. Возвращает (allowed, reason)."""
        now = now or datetime.now(timezone.utc)
        # GC: оставляем только попытки за последний час
        cutoff = now - timedelta(hours=1)
        self.attempts = [a for a in self.attempts if a > cutoff]

        if len(self.attempts) >= DEFAULT_MAX_ATTEMPTS_PER_HOUR:
            return False, f"rate_limit_exceeded:{len(self.attempts)}/hr"

        if self.last_restart:
            cooldown_idx = min(
                self.consecutive_failures, len(DEFAULT_COOLDOWNS_SEC) - 1
            )
            cooldown_sec = DEFAULT_COOLDOWNS_SEC[cooldown_idx]
            elapsed = now - self.last_restart
            if elapsed < timedelta(seconds=cooldown_sec):
                return False, f"cooldown:{cooldown_sec}s"

        return True, ""

    def record_attempt(self, success: bool, now: datetime | None = None) -> None:
        """Фиксирует попытку: обновляем счётчики + сбрасываем failures при success."""
        now = now or datetime.now(timezone.utc)
        self.attempts.append(now)
        self.last_restart = now
        if success:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1


class AutoRestartManager:
    """Синглтон для tracking + execution service restart."""

    def __init__(self) -> None:
        self._states: dict[str, ServiceRestartState] = {}
        self._notification_cb: Callable[[str], Awaitable[None]] | None = None

    def set_notification_callback(
        self, cb: Callable[[str], Awaitable[None]] | None
    ) -> None:
        """Устанавливает async callback для уведомления владельца."""
        self._notification_cb = cb

    def get_state(self, service: str) -> ServiceRestartState:
        """Lazy-init per-service state."""
        if service not in self._states:
            self._states[service] = ServiceRestartState(service_name=service)
        return self._states[service]

    def reset(self) -> None:
        """Полный сброс (используется в тестах)."""
        self._states.clear()
        self._notification_cb = None

    async def attempt_restart(
        self,
        service: str,
        restart_cmd: Sequence[str],
        *,
        timeout_sec: int = DEFAULT_RESTART_TIMEOUT_SEC,
    ) -> tuple[bool, str]:
        """
        Попытка restart. Returns (success, reason).

        reason: "disabled_by_env" | "rate_limit_exceeded:N/hr" | "cooldown:Ns"
              | "ok" | "cmd_failed" | "timeout" | "exec_error"
        """
        if not is_auto_restart_enabled():
            return False, "disabled_by_env"

        state = self.get_state(service)
        can, why = state.can_restart()
        if not can:
            log.info("auto_restart_skipped", service=service, reason=why)
            return False, why

        cmd_list = list(restart_cmd)
        log.warning("auto_restart_attempt", service=service, cmd=cmd_list)

        success = False
        fail_reason = "cmd_failed"
        try:
            result = subprocess.run(
                cmd_list,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=clean_subprocess_env(),
                check=False,
            )
            success = result.returncode == 0
            if not success:
                fail_reason = "cmd_failed"
                log.warning(
                    "auto_restart_cmd_nonzero",
                    service=service,
                    rc=result.returncode,
                    stderr=(result.stderr or "")[:200],
                )
        except subprocess.TimeoutExpired as exc:
            log.error("auto_restart_timeout", service=service, error=str(exc))
            success = False
            fail_reason = "timeout"
        except (OSError, ValueError) as exc:
            log.error("auto_restart_failed", service=service, error=str(exc))
            success = False
            fail_reason = "exec_error"

        state.record_attempt(success)

        # Уведомление владельца — best-effort, не ломает ход
        status_label = "OK" if success else "FAILED"
        msg = (
            f"Auto-restart [{service}]: {status_label}\n"
            f"Попытка {len(state.attempts)}/{DEFAULT_MAX_ATTEMPTS_PER_HOUR} за час."
        )
        if self._notification_cb is not None:
            try:
                await self._notification_cb(msg)
            except Exception as exc:  # noqa: BLE001
                log.error("auto_restart_notify_failed", error=str(exc))

        log.warning(
            "auto_restart_result",
            service=service,
            success=success,
            attempts_last_hour=len(state.attempts),
            consecutive_failures=state.consecutive_failures,
        )
        return success, "ok" if success else fail_reason

    def status(self) -> dict[str, Any]:
        """Возвращает snapshot всех per-service состояний для UI/диагностики."""
        out: dict[str, Any] = {
            "enabled": is_auto_restart_enabled(),
            "max_attempts_per_hour": DEFAULT_MAX_ATTEMPTS_PER_HOUR,
            "cooldowns_sec": list(DEFAULT_COOLDOWNS_SEC),
            "services": {},
        }
        for name, state in self._states.items():
            out["services"][name] = {
                "attempts_last_hour": len(state.attempts),
                "last_restart": state.last_restart.isoformat()
                if state.last_restart
                else None,
                "consecutive_failures": state.consecutive_failures,
            }
        return out


# Singleton, используется из proactive_watch + userbot_bridge
auto_restart_manager = AutoRestartManager()


__all__ = [
    "AutoRestartManager",
    "ServiceRestartState",
    "auto_restart_manager",
    "is_auto_restart_enabled",
    "DEFAULT_MAX_ATTEMPTS_PER_HOUR",
    "DEFAULT_COOLDOWNS_SEC",
    "DEFAULT_RESTART_TIMEOUT_SEC",
    "RESTART_COMMANDS",
]
