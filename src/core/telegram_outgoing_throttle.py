# -*- coding: utf-8 -*-
"""Wave 127: pre-emptive Telegram outgoing flooding heuristic.

### Зачем

Wave 23-A (reserve_bot) и Wave 121 (FloodWait observability) ловят
FloodWait post-факт. Wave 127 — pre-emptive: мы отслеживаем outgoing rate
в реальном времени и применяем небольшой `asyncio.sleep(...)` до того,
как Telegram пришлёт FloodWait.

### Семантика

Sliding window 10s per caller. Если в окне накопилось > max_rps * window
вызовов (rate > max_rps), то текущий acquire ждёт `_delay_sec` (default
200 ms) и инкрементирует counter применения throttle.

Это soft-prevention: дешёвый pre-emptive sleep вместо дорогого FloodWait
от сервера (который пытается выждать минимум секунды).

### Отличие от telegram_rate_limiter (B.7)

- B.7 — глобальный hard cap (20 req/s, window 1s) для **всего** Telegram API.
- Wave 127 — per-caller heuristic (handle_ask/voice_reply/etc) с большим
  окном 10s, ориентирована на pattern detection (бурстовая активность от
  одного caller'а).

Оба слоя работают параллельно: B.7 ставит floor (никогда > 20/s глобально),
Wave 127 ставит ceiling (никогда > 25/s на одного caller'а sustained).

### Env-gate

`KRAB_TG_OUTGOING_THROTTLE_ENABLED=1` (default ON).
`KRAB_TG_OUTGOING_MAX_RPS=25` (default), threshold per-caller.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque

from .logger import get_logger
from .metrics import telegram_throttle as _metrics

logger = get_logger(__name__)

# Default config. Можно re-configure через configure() из bootstrap.
_DEFAULT_MAX_RPS: float = 25.0
_DEFAULT_WINDOW_SEC: float = 10.0
_DEFAULT_DELAY_SEC: float = 0.2


def _env_truthy(name: str, default: str = "1") -> bool:
    raw = os.environ.get(name, default).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


class TelegramOutgoingThrottle:
    """Pre-emptive throttle per-caller на основе sliding window 10s.

    Thread-model: asyncio. Лочим вокруг per-caller deque чтобы избежать
    race condition при concurrent acquires от одного caller'а.
    """

    def __init__(
        self,
        *,
        max_rps: float = _DEFAULT_MAX_RPS,
        window_sec: float = _DEFAULT_WINDOW_SEC,
        delay_sec: float = _DEFAULT_DELAY_SEC,
        enabled: bool | None = None,
    ) -> None:
        self._max_rps = float(max_rps)
        self._window_sec = float(window_sec)
        self._delay_sec = float(delay_sec)
        # None → читаем env лениво при первом acquire.
        self._enabled_override: bool | None = enabled
        self._per_caller: dict[str, deque[float]] = defaultdict(deque)
        self._lock: asyncio.Lock | None = None
        self._total_acquired: int = 0
        self._total_throttled: int = 0

    def configure(
        self,
        *,
        max_rps: float | None = None,
        window_sec: float | None = None,
        delay_sec: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Runtime-reconfigure из bootstrap по env/config."""
        if max_rps is not None:
            self._max_rps = max(1.0, float(max_rps))
        if window_sec is not None:
            self._window_sec = max(0.5, float(window_sec))
        if delay_sec is not None:
            self._delay_sec = max(0.0, float(delay_sec))
        if enabled is not None:
            self._enabled_override = bool(enabled)
        logger.info(
            "telegram_outgoing_throttle_configured",
            max_rps=self._max_rps,
            window_sec=self._window_sec,
            delay_sec=self._delay_sec,
            enabled=self.is_enabled(),
        )

    def is_enabled(self) -> bool:
        """Env-gate: override > KRAB_TG_OUTGOING_THROTTLE_ENABLED env."""
        if self._enabled_override is not None:
            return self._enabled_override
        return _env_truthy("KRAB_TG_OUTGOING_THROTTLE_ENABLED", "1")

    def _resolve_max_rps(self) -> float:
        """Env может перекрывать configured value (для quick-tune без рестарта)."""
        env_val = _env_float("KRAB_TG_OUTGOING_MAX_RPS", self._max_rps)
        return max(1.0, env_val)

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _evict(self, dq: deque[float], now: float) -> None:
        cutoff = now - self._window_sec
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _current_rate(self, dq: deque[float], now: float) -> float:
        """Rate как count_in_window / window_sec (msg/sec)."""
        self._evict(dq, now)
        if self._window_sec <= 0:
            return 0.0
        return len(dq) / self._window_sec

    async def acquire(self, caller: str = "unknown") -> bool:
        """Регистрирует outgoing call, throttle'ит если rate > max_rps.

        Возвращает True если был применён pre-emptive delay, иначе False.
        Best-effort: при любой ошибке логирует и пропускает (не блокирует
        отправку).
        """
        clean_caller = (str(caller) or "unknown")[:80]
        if not self.is_enabled():
            return False
        try:
            lock = self._get_lock()
            async with lock:
                now = time.monotonic()
                dq = self._per_caller[clean_caller]
                self._evict(dq, now)
                rate = len(dq) / self._window_sec if self._window_sec > 0 else 0.0
                max_rps = self._resolve_max_rps()
                throttled = False
                if rate > max_rps:
                    throttled = True
                    self._total_throttled += 1
                    _metrics.inc_throttle_applied(clean_caller)
                    logger.info(
                        "telegram_outgoing_throttle_applied",
                        caller=clean_caller,
                        current_rate=round(rate, 2),
                        max_rps=max_rps,
                        delay_sec=self._delay_sec,
                    )
                # Регистрируем сам call (после evict, до sleep).
                dq.append(now)
                self._total_acquired += 1
                new_rate = len(dq) / self._window_sec if self._window_sec > 0 else 0.0
                _metrics.set_outgoing_rate(clean_caller, new_rate)
            # sleep ВНЕ lock'а — иначе блокируем других callers.
            if throttled and self._delay_sec > 0:
                await asyncio.sleep(self._delay_sec)
            return throttled
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram_outgoing_throttle_error",
                caller=clean_caller,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    def stats(self) -> dict[str, float | int]:
        """Снимок для !stats / owner UI / тестов."""
        per_caller: dict[str, int] = {k: len(v) for k, v in self._per_caller.items()}
        return {
            "enabled": self.is_enabled(),
            "max_rps": self._resolve_max_rps(),
            "window_sec": self._window_sec,
            "delay_sec": self._delay_sec,
            "total_acquired": self._total_acquired,
            "total_throttled": self._total_throttled,
            "per_caller_in_window": per_caller,
        }

    def reset_counters(self) -> None:
        """Reset для тестов / !stats reset."""
        self._total_acquired = 0
        self._total_throttled = 0
        self._per_caller.clear()


# Module-level singleton, pattern совпадает с telegram_rate_limiter.
telegram_outgoing_throttle = TelegramOutgoingThrottle()


__all__ = [
    "TelegramOutgoingThrottle",
    "telegram_outgoing_throttle",
]
