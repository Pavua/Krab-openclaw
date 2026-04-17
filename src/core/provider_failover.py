# -*- coding: utf-8 -*-
"""
Provider auto-failover для LLM routing.

Слушает runtime_route events — если N consecutive failures одного провайдера,
автоматически switch на следующий из fallback_chain.

Notification owner через Telegram DM (callback injection).

Защитные меры:
- `PROVIDER_FAILOVER_ENABLED=false` по умолчанию (safety).
- Cooldown между switches (защита от осцилляции fallback-цепочкой).
- Callback injection для decoupling от `openclaw_client`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from .logger import get_logger

logger = get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    """Читает int из env с fallback на default при ошибке парсинга."""
    raw = os.environ.get(name, "")
    try:
        return int(str(raw).strip()) if str(raw).strip() else default
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes", "on")


DEFAULT_FAILURE_THRESHOLD = _env_int("PROVIDER_FAILOVER_THRESHOLD", 3)
DEFAULT_COOLDOWN_SEC = _env_int("PROVIDER_FAILOVER_COOLDOWN", 300)


def _auto_failover_enabled() -> bool:
    """Читаем env на каждом вызове — чтобы тесты с monkeypatch работали."""
    return _env_bool("PROVIDER_FAILOVER_ENABLED", default=False)


@dataclass
class ProviderState:
    """Health-снимок одного провайдера."""

    provider: str
    consecutive_failures: int = 0
    last_failure_at: Optional[datetime] = None
    last_error_code: str = ""
    total_failures: int = 0
    total_successes: int = 0


@dataclass
class FailoverResult:
    """Итог попытки `maybe_failover` — сработал switch или нет и почему."""

    triggered: bool
    from_provider: str = ""
    to_provider: str = ""
    reason: str = ""


FailoverCallback = Callable[[str, str], Awaitable[None]]
NotificationCallback = Callable[[str], Awaitable[None]]


class ProviderFailoverPolicy:
    """Tracks per-provider health, triggers failover при достижении threshold."""

    def __init__(
        self,
        threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_sec: int = DEFAULT_COOLDOWN_SEC,
    ) -> None:
        self._states: dict[str, ProviderState] = {}
        self._threshold = max(1, int(threshold))
        self._cooldown_sec = max(0, int(cooldown_sec))
        self._last_failover_at: Optional[datetime] = None
        self._failover_callback: Optional[FailoverCallback] = None
        self._notification_callback: Optional[NotificationCallback] = None

    def set_failover_callback(self, cb: FailoverCallback) -> None:
        """cb: async (from_provider, to_provider) -> None — применяет switch."""
        self._failover_callback = cb

    def set_notification_callback(self, cb: NotificationCallback) -> None:
        """cb: async (msg: str) -> None — шлёт owner."""
        self._notification_callback = cb

    def record_success(self, provider: str) -> None:
        """Reset consecutive counter, инкремент total_successes."""
        provider = str(provider or "").strip()
        if not provider:
            return
        s = self._get_state(provider)
        s.consecutive_failures = 0
        s.total_successes += 1
        s.last_error_code = ""
        logger.debug("provider_success_recorded", provider=provider)

    def record_failure(self, provider: str, error_code: str) -> None:
        """Инкремент счётчиков, обновление last_error_code."""
        provider = str(provider or "").strip()
        if not provider:
            return
        s = self._get_state(provider)
        s.consecutive_failures += 1
        s.total_failures += 1
        s.last_failure_at = datetime.now(timezone.utc)
        s.last_error_code = str(error_code or "unknown")
        logger.warning(
            "provider_failure_recorded",
            provider=provider,
            consecutive=s.consecutive_failures,
            total=s.total_failures,
            error=s.last_error_code,
        )

    async def maybe_failover(
        self, current_provider: str, fallback_chain: list[str]
    ) -> FailoverResult:
        """Check: если threshold breached — execute failover."""
        if not _auto_failover_enabled():
            return FailoverResult(triggered=False, reason="disabled_by_env")

        current_provider = str(current_provider or "").strip()
        if not current_provider:
            return FailoverResult(triggered=False, reason="no_current_provider")

        s = self._get_state(current_provider)
        if s.consecutive_failures < self._threshold:
            return FailoverResult(
                triggered=False,
                reason=f"under_threshold:{s.consecutive_failures}/{self._threshold}",
            )

        # Cooldown check — защита от оscиляции.
        now = datetime.now(timezone.utc)
        if self._last_failover_at:
            elapsed = (now - self._last_failover_at).total_seconds()
            if elapsed < self._cooldown_sec:
                return FailoverResult(
                    triggered=False, reason=f"cooldown:{int(elapsed)}s"
                )

        # Выбираем следующего жизнеспособного кандидата из цепочки.
        next_provider = self._pick_next(current_provider, fallback_chain or [])
        if not next_provider:
            return FailoverResult(triggered=False, reason="no_viable_fallback")

        logger.warning(
            "provider_failover_triggered",
            from_provider=current_provider,
            to_provider=next_provider,
            consecutive_failures=s.consecutive_failures,
            last_error=s.last_error_code,
        )

        snapshot_failures = s.consecutive_failures
        snapshot_error = s.last_error_code

        if self._failover_callback:
            try:
                await self._failover_callback(current_provider, next_provider)
            except Exception as exc:  # noqa: BLE001
                logger.error("failover_callback_failed", error=str(exc))
                return FailoverResult(
                    triggered=False, reason=f"callback_failed:{exc}"
                )

        self._last_failover_at = now
        # Reset failed provider's counter — даём ему второй шанс после recovery.
        s.consecutive_failures = 0

        if self._notification_callback:
            try:
                msg = (
                    f"🔄 Auto-failover: `{current_provider}` → `{next_provider}`\n"
                    f"Причина: {snapshot_failures} consecutive failures "
                    f"(err: {snapshot_error or 'unknown'})"
                )
                await self._notification_callback(msg)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failover_notification_failed", error=str(exc))

        return FailoverResult(
            triggered=True,
            from_provider=current_provider,
            to_provider=next_provider,
            reason=f"threshold_exceeded:{self._threshold}",
        )

    def _pick_next(self, current: str, chain: list[str]) -> Optional[str]:
        """Возвращает первого жизнеспособного кандидата в chain, не равного current."""
        for candidate in chain:
            cand = str(candidate or "").strip()
            if not cand or cand == current:
                continue
            c_state = self._states.get(cand)
            if c_state and c_state.consecutive_failures >= self._threshold:
                # Этот тоже падает — пропускаем.
                continue
            return cand
        return None

    def _get_state(self, provider: str) -> ProviderState:
        if provider not in self._states:
            self._states[provider] = ProviderState(provider=provider)
        return self._states[provider]

    def get_all_states(self) -> dict[str, ProviderState]:
        """Возвращает копию внутреннего состояния для диагностики."""
        return dict(self._states)

    def reset(self) -> None:
        """Полный сброс: health-таблица + cooldown timer."""
        self._states.clear()
        self._last_failover_at = None


# Singleton для использования в openclaw_client.
failover_policy = ProviderFailoverPolicy()
