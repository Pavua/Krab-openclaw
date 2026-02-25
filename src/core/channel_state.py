# -*- coding: utf-8 -*-
"""
Channel State Machine (Anti-Flap Router).

Роль модуля:
    Формализованная state machine для каждого канала маршрутизации (local / cloud).
    Устраняет «дрожание» роутинга — однократная ошибка не приводит к немедленному
    переключению. Смена состояния требует N подряд ошибок/успехов (гистерезис).

Состояния канала:
    HEALTHY  — стандартная работа, все запросы разрешены.
    DEGRADED — зафиксированы ошибки (err_threshold подряд), канал подозрителен.
               Запросы разрешены, но роутер может предпочесть другой канал.
    LOCKED   — аварийное состояние после cooldown_sec бездействия или срабатывания.
               Канал недоступен для маршрутизации до истечения cooldown или
               накопления ok_threshold успехов.

Переходы:
    HEALTHY  → DEGRADED : err_threshold подряд ошибок
    DEGRADED → HEALTHY  : ok_threshold подряд успехов
    DEGRADED → LOCKED   : lock_trigger() вызван явно (например, после аварийного switch)
    LOCKED   → HEALTHY  : cooldown истёк ИЛИ накоплено ok_threshold успехов

Зачем "хлебные крошки" по порогам (см. ADR_R24_routing_stability.md):
    - err_threshold=3  : одна транзитная ошибка (сеть моргнула) не должна триггерить switch
    - ok_threshold=2   : быстрое восстановление без длинного испытательного периода
    - cooldown_sec=60  : достаточно для перезапуска LM Studio или API quota refresh
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Допустимые имена состояний канала
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
LOCKED = "LOCKED"


@dataclass
class _ChannelState:
    """Внутреннее состояние одного канала (local или cloud)."""

    state: str = HEALTHY          # Текущее состояние
    consecutive_errors: int = 0   # Счётчик подряд идущих ошибок
    consecutive_ok: int = 0       # Счётчик подряд идущих успехов
    locked_at: float = 0.0        # Timestamp момента блокировки
    total_errors: int = 0         # Накопленные ошибки (для диагностики)
    total_ok: int = 0             # Накопленные успехи (для диагностики)
    last_transition: str = ""     # Последний change log (для !status)
    last_transition_ts: float = field(default_factory=time.time)


class ChannelStateMachine:
    """
    Anti-flap state machine для каналов local и cloud.

    Применение в model_manager.py:
        self.channel_state = ChannelStateMachine(config)
        # При успехе local:
        self.channel_state.record_success("local")
        # При ошибке cloud:
        self.channel_state.record_failure("cloud")
        # Перед роутингом:
        if not self.channel_state.is_usable("local"):
            # Канал LOCKED, пропускаем
            ...

    Параметры из config (все опциональны, есть дефолты):
        CHANNEL_ERR_THRESHOLD     — кол-во подряд ошибок для HEALTHY→DEGRADED (дефолт 3)
        CHANNEL_OK_THRESHOLD      — кол-во подряд успехов для DEGRADED/LOCKED→HEALTHY (дефолт 2)
        CHANNEL_LOCK_COOLDOWN_SEC — cooldown в LOCKED секундах (дефолт 60)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}

        # Пороги гистерезиса (см. ADR для обоснования)
        try:
            self.err_threshold = max(1, int(cfg.get("CHANNEL_ERR_THRESHOLD", 3)))
        except (ValueError, TypeError):
            self.err_threshold = 3

        try:
            self.ok_threshold = max(1, int(cfg.get("CHANNEL_OK_THRESHOLD", 2)))
        except (ValueError, TypeError):
            self.ok_threshold = 2

        try:
            self.lock_cooldown_sec = max(0.01, float(cfg.get("CHANNEL_LOCK_COOLDOWN_SEC", 60)))
        except (ValueError, TypeError):
            self.lock_cooldown_sec = 60.0

        # Состояния каналов: инициализируем оба заранее
        self._states: dict[str, _ChannelState] = {
            "local": _ChannelState(),
            "cloud": _ChannelState(),
        }

    # ─────────────────────────────────────────────────────────────────── #
    # Публичный API
    # ─────────────────────────────────────────────────────────────────── #

    def record_success(self, channel: str) -> None:
        """
        Фиксирует успешное выполнение запроса через канал.
        Сбрасывает consecutive_errors, накапливает consecutive_ok.
        Проверяет переход в HEALTHY.
        """
        st = self._get_or_create(channel)
        st.total_ok += 1
        st.consecutive_errors = 0
        st.consecutive_ok += 1

        if st.state in (DEGRADED, LOCKED):
            if st.consecutive_ok >= self.ok_threshold:
                old = st.state
                st.state = HEALTHY
                st.consecutive_ok = 0
                st.last_transition = f"{old}→HEALTHY (ok_threshold={self.ok_threshold})"
                st.last_transition_ts = time.time()
                logger.info(
                    "Channel recovered to HEALTHY: channel=%s transition=%s",
                    channel, st.last_transition,
                )

    def record_failure(self, channel: str) -> None:
        """
        Фиксирует ошибку запроса через канал.
        Накапливает consecutive_errors, сбрасывает consecutive_ok.
        Проверяет переход в DEGRADED.
        """
        st = self._get_or_create(channel)
        st.total_errors += 1
        st.consecutive_ok = 0
        st.consecutive_errors += 1

        if st.state == HEALTHY and st.consecutive_errors >= self.err_threshold:
            st.state = DEGRADED
            st.consecutive_errors = 0
            st.last_transition = f"HEALTHY→DEGRADED (err_threshold={self.err_threshold})"
            st.last_transition_ts = time.time()
            logger.warning(
                "Channel degraded: channel=%s transition=%s",
                channel, st.last_transition,
            )

    def lock_channel(self, channel: str, reason: str = "manual") -> None:
        """
        Явная блокировка канала (например, после аварийного переключения).
        Канал переходит в LOCKED с отсчётом cooldown.
        """
        st = self._get_or_create(channel)
        old = st.state
        st.state = LOCKED
        st.locked_at = time.time()
        st.consecutive_errors = 0
        st.consecutive_ok = 0
        st.last_transition = f"{old}→LOCKED (reason={reason})"
        st.last_transition_ts = time.time()
        logger.warning(
            "Channel LOCKED: channel=%s reason=%s cooldown_sec=%s",
            channel, reason, self.lock_cooldown_sec,
        )

    def is_usable(self, channel: str) -> bool:
        """
        Возвращает True, если канал может принимать запросы.
        LOCKED канал недоступен до истечения cooldown.
        HEALTHY/DEGRADED — доступны.
        """
        st = self._get_or_create(channel)
        if st.state == LOCKED:
            # Проверяем cooldown: если истёк — автоматически переводим в HEALTHY
            if (time.time() - st.locked_at) >= self.lock_cooldown_sec:
                st.state = HEALTHY
                st.consecutive_errors = 0
                st.consecutive_ok = 0
                st.last_transition = f"LOCKED→HEALTHY (cooldown={self.lock_cooldown_sec}s expired)"
                st.last_transition_ts = time.time()
                logger.info("Channel auto-recovered from LOCKED: channel=%s", channel)
                return True
            return False
        return True

    def get_state(self, channel: str) -> str:
        """Возвращает текущее состояние канала (HEALTHY/DEGRADED/LOCKED)."""
        st = self._get_or_create(channel)
        # При LOCKED проверяем cooldown актуально
        if st.state == LOCKED:
            self.is_usable(channel)  # Side-effect: авто-восстановление если cooldown истёк
        return st.state

    def reset(self, channel: str) -> None:
        """Принудительный сброс канала в HEALTHY (для тестов / ручного управления)."""
        st = self._get_or_create(channel)
        st.state = HEALTHY
        st.consecutive_errors = 0
        st.consecutive_ok = 0
        st.locked_at = 0.0
        st.last_transition = "RESET→HEALTHY (manual)"
        st.last_transition_ts = time.time()

    def get_diagnostics(self) -> dict[str, Any]:
        """
        Возвращает диагностику всех каналов для /api/health и !status.
        Формат совместим с экспортом openclaw_client.get_tier_state_export().
        """
        result: dict[str, Any] = {}
        for channel, st in self._states.items():
            # При LOCKED проверяем cooldown актуально
            if st.state == LOCKED:
                self.is_usable(channel)
            remaining_cooldown = 0.0
            if st.state == LOCKED and st.locked_at > 0:
                remaining_cooldown = max(
                    0.0,
                    self.lock_cooldown_sec - (time.time() - st.locked_at),
                )
            result[channel] = {
                "state": st.state,
                "consecutive_errors": st.consecutive_errors,
                "consecutive_ok": st.consecutive_ok,
                "total_errors": st.total_errors,
                "total_ok": st.total_ok,
                "last_transition": st.last_transition,
                "last_transition_ts": st.last_transition_ts,
                "remaining_cooldown_sec": round(remaining_cooldown, 1),
                "err_threshold": self.err_threshold,
                "ok_threshold": self.ok_threshold,
                "lock_cooldown_sec": self.lock_cooldown_sec,
            }
        return result

    # ─────────────────────────────────────────────────────────────────── #
    # Внутренние методы
    # ─────────────────────────────────────────────────────────────────── #

    def _get_or_create(self, channel: str) -> _ChannelState:
        """Возвращает или создаёт состояние канала (поддержка произвольных имён)."""
        if channel not in self._states:
            self._states[channel] = _ChannelState()
        return self._states[channel]
