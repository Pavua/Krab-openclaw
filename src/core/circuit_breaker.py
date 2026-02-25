# -*- coding: utf-8 -*-
"""
Circuit Breaker для OpenClaw Gateway.

Роль модуля:
    Классический circuit breaker паттерн для защиты OpenClaw от каскадных отказов.
    При серии ошибок gateway — перестаём долбить его запросами («OPEN»),
    через recovery_timeout делаем один probe («HALF_OPEN»),
    при успехе возвращаемся к нормальной работе («CLOSED»).

Состояния FSM:
    CLOSED    — нормальный режим, все запросы проходят.
    OPEN      — circuit «разомкнут»: запросы отклоняются немедленно без I/O.
    HALF_OPEN — один «probe» запрос разрешён; по его результату → CLOSED или OPEN.

Переходы:
    CLOSED    → OPEN      : failure_threshold отказов за window_seconds
    OPEN      → HALF_OPEN : прошло recovery_timeout_sec секунд
    HALF_OPEN → CLOSED    : probe запрос успешен
    HALF_OPEN → OPEN      : probe запрос упал

Зачем эти пороги (см. ADR_R24_routing_stability.md):
    - failure_threshold=5 / window_seconds=60 :
        5 отказов/мин — явная деградация, не флуктуация
    - recovery_timeout_sec=30 :
        Достаточно для перезапуска nginx/gunicorn в OpenClaw
    - probe_timeout_sec=5 :
        Probe должен быть быстрым — если gateway не отвечает за 5с, он всё ещё OPEN

Использование в OpenClawClient:
    self._breaker = CircuitBreaker(config)
    result = await self._breaker.call(self._do_chat_completions_inner(...))
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

# Имена состояний circuit breaker
CB_CLOSED = "CLOSED"
CB_OPEN = "OPEN"
CB_HALF_OPEN = "HALF_OPEN"

T = TypeVar("T")


class CircuitBreakerOpenError(Exception):
    """
    Поднимается, когда circuit breaker в OPEN и запрос блокируется.
    Содержит: reason (строка) и remaining_sec (время до probe).
    """

    def __init__(self, reason: str, remaining_sec: float = 0.0) -> None:
        self.reason = reason
        self.remaining_sec = remaining_sec
        super().__init__(f"Circuit OPEN: {reason} (probe in {remaining_sec:.0f}s)")


@dataclass
class _BreakerState:
    """Внутренний state circuit breaker."""

    state: str = CB_CLOSED
    failure_count: int = 0       # Отказов за текущее окно
    window_start: float = field(default_factory=time.time)
    opened_at: float = 0.0       # Timestamp перехода в OPEN
    half_open_acquired: bool = False  # Разрешён ли один probe
    total_opens: int = 0         # Сколько раз breaker открывался (диагностика)
    total_probes: int = 0        # Количество HALF_OPEN probe-попыток
    last_error: str = ""         # Последнее сообщение об ошибке


class CircuitBreaker:
    """
    Circuit Breaker для защиты внешних вызовов (OpenClaw Gateway).

    Параметры из config:
        BREAKER_FAILURE_THRESHOLD   — число отказов за окно → OPEN (дефолт 5)
        BREAKER_WINDOW_SECONDS      — окно мониторинга ошибок (дефолт 60)
        BREAKER_RECOVERY_TIMEOUT    — секунд в OPEN до probe (дефолт 30)
        BREAKER_PROBE_TIMEOUT       — таймаут probe запроса (дефолт 5)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}

        try:
            self.failure_threshold = max(1, int(cfg.get("BREAKER_FAILURE_THRESHOLD", 5)))
        except (ValueError, TypeError):
            self.failure_threshold = 5

        try:
            self.window_seconds = max(0.01, float(cfg.get("BREAKER_WINDOW_SECONDS", 60)))
        except (ValueError, TypeError):
            self.window_seconds = 60.0

        try:
            self.recovery_timeout_sec = max(0.01, float(cfg.get("BREAKER_RECOVERY_TIMEOUT", 30)))
        except (ValueError, TypeError):
            self.recovery_timeout_sec = 30.0

        try:
            self.probe_timeout_sec = max(1, float(cfg.get("BREAKER_PROBE_TIMEOUT", 5)))
        except (ValueError, TypeError):
            self.probe_timeout_sec = 5.0

        self._state = _BreakerState()
        # asyncio.Lock создаётся лениво (в async-контексте)
        self._lock: asyncio.Lock | None = None

    # ─────────────────────────────────────────────────────────────────── #
    # Публичный API
    # ─────────────────────────────────────────────────────────────────── #

    @property
    def state(self) -> str:
        """Текущее состояние (CLOSED / OPEN / HALF_OPEN)."""
        self._maybe_transition_to_half_open()
        return self._state.state

    async def call(
        self,
        coro: Awaitable[T],
        *,
        is_probe: bool = False,
    ) -> T:
        """
        Оборачивает awaitable в circuit breaker.

        Args:
            coro: Awaitable для выполнения.
            is_probe: если True — это probe-запрос в HALF_OPEN.

        Raises:
            CircuitBreakerOpenError: если circuit OPEN и запрос блокирован.
            Exception: если coro выбросила исключение (также записывает failure).
        """
        lock = self._get_lock()
        async with lock:
            current = self._evaluate_state()

            if current == CB_OPEN:
                remaining = self._remaining_before_probe()
                raise CircuitBreakerOpenError(
                    reason=self._state.last_error or "gateway unavailable",
                    remaining_sec=remaining,
                )

            if current == CB_HALF_OPEN and not is_probe:
                # Параллельные запросы в HALF_OPEN блокируются — только один probe
                raise CircuitBreakerOpenError(
                    reason="probe in progress",
                    remaining_sec=0.0,
                )

            if current == CB_HALF_OPEN:
                self._state.half_open_acquired = True
                self._state.total_probes += 1

        # Выполняем вне lock, чтобы не блокировать весь breaker на время I/O
        try:
            if current == CB_HALF_OPEN:
                # Probe с коротким таймаутом
                result = await asyncio.wait_for(coro, timeout=self.probe_timeout_sec)
            else:
                result = await coro

            # Успех
            self.record_success()
            return result

        except Exception as exc:
            self.record_failure(str(exc))
            raise

    def record_success(self) -> None:
        """Явная запись успеха (использовать если не через call())."""
        st = self._state
        if st.state == CB_HALF_OPEN:
            # Probe успешен — CLOSED
            logger.info(
                "Circuit CLOSED after probe success: total_opens=%d",
                st.total_opens,
            )
            st.state = CB_CLOSED
            st.failure_count = 0
            st.window_start = time.time()
            st.half_open_acquired = False
        elif st.state == CB_CLOSED:
            # Сбрасываем окно при успехе (sliding window упрощённая версия)
            if time.time() - st.window_start > self.window_seconds:
                st.failure_count = 0
                st.window_start = time.time()

    def record_failure(self, error: str = "") -> None:
        """Явная запись ошибки (использовать если не через call())."""
        # Сначала проверяем auto-transition OPEN→HALF_OPEN (время могло истечь)
        self._maybe_transition_to_half_open()

        st = self._state
        st.last_error = str(error)[:200]

        if st.state == CB_HALF_OPEN:
            # Probe failure → OPEN снова
            logger.warning(
                "Circuit OPEN: probe failed error=%s",
                st.last_error[:100],
            )
            st.state = CB_OPEN
            st.opened_at = time.time()
            st.half_open_acquired = False
            st.total_opens += 1  # Повторное открытие тоже считается
            return

        if st.state == CB_OPEN:
            return  # Уже открыт

        # CLOSED — накапливаем в окне
        now = time.time()
        if now - st.window_start > self.window_seconds:
            # Окно истекло, начинаем заново
            st.failure_count = 0
            st.window_start = now

        st.failure_count += 1
        if st.failure_count >= self.failure_threshold:
            st.state = CB_OPEN
            st.opened_at = time.time()
            st.total_opens += 1
            logger.warning(
                "Circuit OPEN: failure threshold reached threshold=%d window_sec=%s error=%s",
                self.failure_threshold, self.window_seconds, st.last_error[:100],
            )

    def get_diagnostics(self) -> dict[str, Any]:
        """
        Состояние breaker для /api/health, get_tier_state_export() и !status.
        """
        self._maybe_transition_to_half_open()
        st = self._state
        remaining = self._remaining_before_probe() if st.state == CB_OPEN else 0.0
        return {
            "state": st.state,
            "failure_count": st.failure_count,
            "failure_threshold": self.failure_threshold,
            "window_seconds": self.window_seconds,
            "recovery_timeout_sec": self.recovery_timeout_sec,
            "total_opens": st.total_opens,
            "total_probes": st.total_probes,
            "remaining_before_probe_sec": round(remaining, 1),
            "last_error": st.last_error,
        }

    # ─────────────────────────────────────────────────────────────────── #
    # Внутренние методы
    # ─────────────────────────────────────────────────────────────────── #

    def _evaluate_state(self) -> str:
        """Возвращает текущее состояние с учётом auto-transition OPEN→HALF_OPEN."""
        self._maybe_transition_to_half_open()
        return self._state.state

    def _maybe_transition_to_half_open(self) -> None:
        """Если OPEN и прошёл recovery_timeout — переводим в HALF_OPEN."""
        st = self._state
        if st.state == CB_OPEN and not st.half_open_acquired:
            elapsed = time.time() - st.opened_at
            if elapsed >= self.recovery_timeout_sec:
                st.state = CB_HALF_OPEN
                logger.info(
                    "Circuit HALF_OPEN: ready for probe open_duration_sec=%s",
                    round(elapsed, 1),
                )

    def _remaining_before_probe(self) -> float:
        """Секунд до перехода в HALF_OPEN."""
        st = self._state
        if st.state != CB_OPEN:
            return 0.0
        elapsed = time.time() - st.opened_at
        return max(0.0, self.recovery_timeout_sec - elapsed)

    def _get_lock(self) -> asyncio.Lock:
        """Ленивая инициализация Lock (должна быть в async-контексте)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock
