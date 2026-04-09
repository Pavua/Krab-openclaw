# -*- coding: utf-8 -*-
"""
Global sliding-window rate limiter для исходящих Telegram API вызовов.

### Зачем это нужно

B.4–B.6 закрыли частные проблемы: per-chat voice blocklist, chat ban cache,
capability cache. Но все они работают на уровне «не посылать в конкретный
чат». Остался последний пункт из разбора 09.04.2026 с Chado/Nik в OG P
Cod/id BCI inside:

> «не обязательно сообщений, просто сами обращения к телеграм если полит
> часто. getUpdates, resolve, getParticipants и прочие API-запросы тоже
> считаются. Если агент поллит без задержки или дёргает resolve на
> каждое сообщение — Telegram засечёт.»

Telegram SpamBot смотрит не только на количество отправленных сообщений,
но и на суммарный RPS аккаунта по всем API вызовам. У существующего
`_TelegramSendQueue` есть per-chat serialization и FLOOD_WAIT retry, но
**нет глобального кап**, когда несколько чатов одновременно генерируют
трафик. B.7 добавляет этот cap.

### Семантика

Sliding window: в любой момент за последние `window_sec` секунд может
быть сделано не больше `max_per_sec * window_sec` API вызовов. Если
очередной `acquire()` превысил порог, корутина `await asyncio.sleep`
до момента, когда самый старый слот в окне освободится.

**Soft cap, не hard**: мы не отказываем в вызове, мы **замедляем**.
Это принципиально — отмена вызова означала бы потерю сообщения,
а нам нужно доставить, просто с небольшой задержкой.

### Что НЕ делает

- Не per-chat throttling — для этого есть `_TelegramSendQueue`.
- Не приоритизирует вызовы (owner > background). Можно добавить позже.
- Не персистит state через рестарты — sliding window начинается заново
  после рестарта (это ok: Telegram SpamBot смотрит rolling среднее,
  не абсолютное значение).

### Default: 20 req/s

Pyrogram/Telethon документация предупреждает о ~30 req/s как soft cap
на user-бот. Берём 20 с запасом чтобы оставить место для: (1) входящих
updates, (2) occasional bursts, (3) MCP сервера которые тоже ходят
через тот же аккаунт.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from .logger import get_logger

logger = get_logger(__name__)


# Default: 20 API-вызовов в секунду. Конфигурируется через
# `config.TELEGRAM_GLOBAL_RATE_MAX_PER_SEC`, но дефолт держим в
# модуле чтобы тесты работали без config.
_DEFAULT_MAX_PER_SEC: int = 20
_DEFAULT_WINDOW_SEC: float = 1.0


class GlobalTelegramRateLimiter:
    """
    Sliding-window rate limiter для исходящих Telegram API вызовов.

    Thread-model: используется в asyncio context, защищён asyncio.Lock.
    Одновременно несколько await acquire() → serialized через lock,
    но inside lock работа быстрая (deque ops + optional sleep).

    Тесты: инжектируются через `storage=None` (state только in-memory),
    так что тест может создать свой instance без загрязнения singleton.
    """

    def __init__(
        self,
        *,
        max_per_sec: int = _DEFAULT_MAX_PER_SEC,
        window_sec: float = _DEFAULT_WINDOW_SEC,
    ) -> None:
        self._max_per_sec = int(max_per_sec)
        self._window_sec = float(window_sec)
        # deque of monotonic timestamps of recent acquires
        self._recent: deque[float] = deque()
        self._lock: asyncio.Lock | None = None
        self._total_acquired: int = 0
        self._total_waited: int = 0
        self._total_wait_sec: float = 0.0

    def configure(self, *, max_per_sec: int, window_sec: float = 1.0) -> None:
        """Runtime-reconfigure лимита. Вызывается из bootstrap по config."""
        self._max_per_sec = max(1, int(max_per_sec))
        self._window_sec = max(0.1, float(window_sec))
        logger.info(
            "telegram_rate_limiter_configured",
            max_per_sec=self._max_per_sec,
            window_sec=self._window_sec,
        )

    def _get_lock(self) -> asyncio.Lock:
        """Lazy-create lock — нельзя создавать в __init__ до running loop'а."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self, purpose: str = "unknown") -> None:
        """
        Блокирует корутину пока не появится слот в окне.

        `purpose` — free-form string для логов (send_message, get_chat,
        get_chat_history, send_reaction, ...). Не влияет на логику, но
        помогает при диагностике «что жрёт rate budget».
        """
        lock = self._get_lock()
        async with lock:
            now = time.monotonic()
            # Evict старые timestamps из окна.
            window_start = now - self._window_sec
            while self._recent and self._recent[0] < window_start:
                self._recent.popleft()

            if len(self._recent) >= self._max_per_sec:
                # Окно полно — нужно подождать пока освободится самый старый слот.
                oldest = self._recent[0]
                wait_sec = self._window_sec - (now - oldest) + 0.001
                if wait_sec > 0:
                    self._total_waited += 1
                    self._total_wait_sec += wait_sec
                    logger.info(
                        "telegram_rate_limiter_wait",
                        purpose=purpose,
                        wait_sec=round(wait_sec, 3),
                        current_in_window=len(self._recent),
                        max_per_sec=self._max_per_sec,
                    )
                    await asyncio.sleep(wait_sec)
                    now = time.monotonic()
                    # Re-evict после sleep — часть старых timestamps должна была
                    # выйти из окна.
                    window_start = now - self._window_sec
                    while self._recent and self._recent[0] < window_start:
                        self._recent.popleft()

            self._recent.append(now)
            self._total_acquired += 1

    def stats(self) -> dict[str, float | int]:
        """Снимок метрик для `!stats` / owner UI / regression тестов."""
        return {
            "max_per_sec": self._max_per_sec,
            "window_sec": self._window_sec,
            "current_in_window": len(self._recent),
            "total_acquired": self._total_acquired,
            "total_waited": self._total_waited,
            "total_wait_sec": round(self._total_wait_sec, 3),
        }

    def reset_counters(self) -> None:
        """Сбрасывает счётчики (owner-command `!stats reset` или в тестах)."""
        self._total_acquired = 0
        self._total_waited = 0
        self._total_wait_sec = 0.0


# Module-level singleton, pattern совпадает с silence_manager / chat_ban_cache /
# chat_capability_cache.
telegram_rate_limiter = GlobalTelegramRateLimiter()
