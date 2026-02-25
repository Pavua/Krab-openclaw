# -*- coding: utf-8 -*-
"""
Тесты R24: Circuit Breaker для OpenClaw Gateway.

Проверяют:
- CLOSED → OPEN после N отказов в окне времени
- OPEN блокирует запросы немедленно
- OPEN → HALF_OPEN после recovery_timeout
- HALF_OPEN probe: успех → CLOSED, неудача → OPEN

Запуск:
    python -m pytest tests/test_r24_circuit_breaker.py -v
"""

import asyncio
import time
import pytest

from src.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CB_CLOSED,
    CB_OPEN,
    CB_HALF_OPEN,
)


def make_breaker(**kwargs) -> CircuitBreaker:
    """Хелпер: создаёт CircuitBreaker с тестовыми параметрами."""
    config = {
        "BREAKER_FAILURE_THRESHOLD": 3,
        "BREAKER_WINDOW_SECONDS": 60,
        "BREAKER_RECOVERY_TIMEOUT": 0.1,   # 100мс для быстрых тестов
        "BREAKER_PROBE_TIMEOUT": 5,
    }
    config.update(kwargs)
    return CircuitBreaker(config)


async def _dummy_coro() -> str:
    """Тестовая корутина — хелпер для тестов CircuitBreaker.

    R25-core: выделена в отдельную именованную функцию (не lambda/local def),
    чтобы можно было создать экземпляр и явно close() при блокировке breaker'а.
    """
    return "ok"


class TestCircuitBreakerClosed:
    """Тесты CLOSED состояния."""

    def test_initial_state_is_closed(self):
        """Начальное состояние — CLOSED."""
        cb = make_breaker()
        assert cb.state == CB_CLOSED

    def test_single_failure_stays_closed(self):
        """Одна ошибка не открывает breaker."""
        cb = make_breaker(BREAKER_FAILURE_THRESHOLD=3)
        cb.record_failure("test error")
        assert cb.state == CB_CLOSED

    def test_closed_to_open_after_threshold(self):
        """failure_threshold подряд ошибок → OPEN."""
        cb = make_breaker(BREAKER_FAILURE_THRESHOLD=3, BREAKER_WINDOW_SECONDS=60)
        cb.record_failure("err1")
        cb.record_failure("err2")
        assert cb.state == CB_CLOSED  # 2 < threshold
        cb.record_failure("err3")
        assert cb.state == CB_OPEN    # 3 = threshold → OPEN

    def test_window_reset_clears_failure_count(self):
        """При истечении window_seconds счётчик сбрасывается."""
        cb = make_breaker(BREAKER_FAILURE_THRESHOLD=3, BREAKER_WINDOW_SECONDS=0.05)
        cb.record_failure("err1")
        cb.record_failure("err2")
        asyncio.run(asyncio.sleep(0.1))  # Ждём истечения окна
        # Следующая ошибка — первая в новом окне
        cb.record_failure("err3")
        assert cb.state == CB_CLOSED  # Счётчик сбросился, 1 < 3

    def test_success_in_closed_does_not_change_state(self):
        """Успех в CLOSED не меняет состояние."""
        cb = make_breaker()
        cb.record_success()
        assert cb.state == CB_CLOSED


class TestCircuitBreakerOpen:
    """Тесты OPEN состояния."""

    def test_open_blocks_calls_raising_error(self):
        """В OPEN состоянии call() поднимает CircuitBreakerOpenError."""
        cb = make_breaker(
            BREAKER_FAILURE_THRESHOLD=1,
            BREAKER_RECOVERY_TIMEOUT=3600,  # долгий
        )
        cb.record_failure("gateway down")
        assert cb.state == CB_OPEN

        async def run():
            # R25-core: coroutine создаётся внутри async-контекста и закрывается
            # явно при CircuitBreakerOpenError, чтобы избежать RuntimeWarning
            # "coroutine was never awaited" (CB_OPEN BlockError поднимается до await).
            coro = _dummy_coro()
            try:
                await cb.call(coro)
            except CircuitBreakerOpenError:
                # Coroutine не была awaited — закрываем явно, чтобы не было RuntimeWarning
                coro.close()
                raise

        with pytest.raises(CircuitBreakerOpenError):
            asyncio.run(run())

    def test_open_error_contains_remaining_sec(self):
        """CircuitBreakerOpenError содержит remaining_sec > 0."""
        cb = make_breaker(
            BREAKER_FAILURE_THRESHOLD=1,
            BREAKER_RECOVERY_TIMEOUT=3600,
        )
        cb.record_failure("error")

        async def run():
            # R25-core: явное закрытие coroutine при блокировке CB
            coro = _dummy_coro()
            try:
                await cb.call(coro)
            except CircuitBreakerOpenError as exc:
                coro.close()  # Предотвращаем RuntimeWarning
                return exc.remaining_sec
            return 0.0

        remaining = asyncio.run(run())
        assert remaining > 0

    def test_diagnostics_shows_open_state(self):
        """get_diagnostics() возвращает state=OPEN."""
        cb = make_breaker(BREAKER_FAILURE_THRESHOLD=1, BREAKER_RECOVERY_TIMEOUT=3600)
        cb.record_failure("err")
        diag = cb.get_diagnostics()
        assert diag["state"] == CB_OPEN
        assert diag["total_opens"] == 1


class TestCircuitBreakerHalfOpen:
    """Тесты HALF_OPEN состояния и probe."""

    def test_open_transitions_to_half_open_after_recover(self):
        """После recovery_timeout breaker переходит в HALF_OPEN."""
        cb = make_breaker(
            BREAKER_FAILURE_THRESHOLD=1,
            BREAKER_RECOVERY_TIMEOUT=0.05,
        )
        cb.record_failure("err")
        assert cb.state == CB_OPEN
        asyncio.run(asyncio.sleep(0.1))
        assert cb.state == CB_HALF_OPEN

    def test_probe_success_closes_breaker(self):
        """Успешный probe → CLOSED."""
        cb = make_breaker(
            BREAKER_FAILURE_THRESHOLD=1,
            BREAKER_RECOVERY_TIMEOUT=0.05,
        )
        cb.record_failure("err")
        asyncio.run(asyncio.sleep(0.1))
        assert cb.state == CB_HALF_OPEN

        # Успешный probe
        cb.record_success()
        assert cb.state == CB_CLOSED

    def test_probe_failure_reopens_breaker(self):
        """Провальный probe → OPEN снова."""
        cb = make_breaker(
            BREAKER_FAILURE_THRESHOLD=1,
            BREAKER_RECOVERY_TIMEOUT=0.05,
        )
        cb.record_failure("err1")
        asyncio.run(asyncio.sleep(0.1))
        assert cb.state == CB_HALF_OPEN

        # Probe failure
        cb.record_failure("err2")
        assert cb.state == CB_OPEN

    def test_breaker_reopens_count_increments(self):
        """Повторное OPEN инкрементирует total_opens."""
        cb = make_breaker(
            BREAKER_FAILURE_THRESHOLD=1,
            BREAKER_RECOVERY_TIMEOUT=0.05,
        )
        cb.record_failure("err1")
        asyncio.run(asyncio.sleep(0.1))
        cb.record_failure("err2")  # probe fail → OPEN
        diag = cb.get_diagnostics()
        assert diag["total_opens"] == 2  # первый OPEN + reopened


class TestCircuitBreakerDiagnostics:
    """Тесты метода get_diagnostics()."""

    def test_diagnostics_includes_all_fields(self):
        """get_diagnostics() содержит все ожидаемые ключи."""
        cb = make_breaker()
        diag = cb.get_diagnostics()
        required = [
            "state", "failure_count", "failure_threshold",
            "window_seconds", "recovery_timeout_sec", "total_opens",
            "total_probes", "remaining_before_probe_sec", "last_error"
        ]
        for key in required:
            assert key in diag, f"Ключ '{key}' отсутствует в get_diagnostics()"

    def test_diagnostics_last_error_set_on_failure(self):
        """Последнее сообщение об ошибке сохраняется в last_error."""
        cb = make_breaker()
        cb.record_failure("connection refused: 127.0.0.1:18789")
        diag = cb.get_diagnostics()
        assert "connection refused" in diag["last_error"]
