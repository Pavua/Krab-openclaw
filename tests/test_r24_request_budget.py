# -*- coding: utf-8 -*-
"""
Тесты R24: Request Budget Guard.

Проверяют:
- Нормальный путь: бюджет не исчерпан → нет исключений
- BudgetExceededError поднимается при истечении бюджета
- checkpoint() срабатывает после исчерпания
- effective_call_timeout() ограничивает per_call_sec остатком бюджета
- from_config() читает CLOUD_FAIL_FAST_BUDGET_SECONDS и CLOUD_REQUEST_TIMEOUT_SECONDS

Запуск:
    python -m pytest tests/test_r24_request_budget.py -v
"""

import asyncio
import time
import pytest

from src.core.request_budget import RequestBudgetGuard, BudgetExceededError


class TestRequestBudgetGuardBasic:
    """Базовые тесты бюджета времени."""

    def test_budget_ok_within_time(self):
        """Запрос завершённый вовремя не вызывает исключений."""
        async def run():
            async with RequestBudgetGuard(total_sec=10, label="test") as budget:
                assert not budget.is_exceeded()
                return "ok"

        result = asyncio.run(run())
        assert result == "ok"

    def test_elapsed_grows_over_time(self):
        """elapsed() растёт по мере выполнения."""
        async def run():
            async with RequestBudgetGuard(total_sec=10) as budget:
                start_elapsed = budget.elapsed()
                await asyncio.sleep(0.05)
                later_elapsed = budget.elapsed()
                return start_elapsed, later_elapsed

        start, later = asyncio.run(run())
        assert later > start

    def test_remaining_decreases_over_time(self):
        """remaining() уменьшается по мере выполнения."""
        async def run():
            async with RequestBudgetGuard(total_sec=10) as budget:
                r1 = budget.remaining()
                await asyncio.sleep(0.05)
                r2 = budget.remaining()
                return r1, r2

        r1, r2 = asyncio.run(run())
        assert r2 < r1

    def test_remaining_zero_when_exceeded(self):
        """remaining() возвращает 0 после исчерпания."""
        async def run():
            guard = RequestBudgetGuard(total_sec=0.01)
            guard._start = time.monotonic() - 1.0  # Имитируем истечение
            return guard.remaining()

        result = asyncio.run(run())
        assert result == 0.0


class TestRequestBudgetGuardExceeded:
    """Тесты поведения при исчерпании бюджета."""

    def test_checkpoint_raises_when_budget_exceeded(self):
        """checkpoint() поднимает BudgetExceededError при исчерпанном бюджете."""
        async def run():
            guard = RequestBudgetGuard(total_sec=0.01, label="test_route")
            guard._start = time.monotonic() - 1.0  # Уже просрочен
            guard.checkpoint("candidate_1")  # Должен поднять исключение

        with pytest.raises(BudgetExceededError):
            asyncio.run(run())

    def test_budget_exceeded_error_has_reason(self):
        """BudgetExceededError содержит reason, elapsed, total."""
        guard = RequestBudgetGuard(total_sec=10.0, label="route_query")
        guard._start = time.monotonic() - 15.0  # Явно просрочен

        try:
            guard.checkpoint("cloud_candidate_0")
        except BudgetExceededError as exc:
            assert "route_query" in exc.reason
            assert exc.elapsed > 10.0
            assert exc.total == 10.0
        else:
            pytest.fail("Ожидался BudgetExceededError")

    def test_budget_exceeded_error_message_is_human_readable(self):
        """BudgetExceededError.__str__ содержит читаемый текст."""
        exc = BudgetExceededError(reason="route_query:candidate_2", elapsed=45.3, total=40.0)
        msg = str(exc)
        assert "Budget exceeded" in msg
        assert "route_query:candidate_2" in msg
        assert "40.0" in msg

    def test_is_exceeded_true_when_time_gone(self):
        """is_exceeded() возвращает True при истечении."""
        guard = RequestBudgetGuard(total_sec=0.01)
        guard._start = time.monotonic() - 1.0
        assert guard.is_exceeded() is True

    def test_is_exceeded_false_when_time_left(self):
        """is_exceeded() возвращает False когда время ещё есть."""
        guard = RequestBudgetGuard(total_sec=60.0)
        guard._start = time.monotonic()
        assert guard.is_exceeded() is False


class TestRequestBudgetCallTimeout:
    """Тесты effective_call_timeout()."""

    def test_effective_timeout_caps_at_remaining(self):
        """effective_call_timeout() ≤ remaining() когда remaining < per_call."""
        guard = RequestBudgetGuard(total_sec=5.0, per_call_sec=22.0)
        guard._start = time.monotonic() - 4.5  # Осталось ~0.5с
        # per_call=22, но remaining≈0.5 → effective ≈ 0.5
        effective = guard.effective_call_timeout()
        assert effective <= 1.0  # не более 1с при ~0.5с остатке

    def test_effective_timeout_uses_per_call_when_plenty_left(self):
        """effective_call_timeout() = per_call когда времени много."""
        guard = RequestBudgetGuard(total_sec=100.0, per_call_sec=22.0)
        guard._start = time.monotonic()
        effective = guard.effective_call_timeout()
        assert effective == 22.0

    def test_effective_timeout_returns_positive(self):
        """effective_call_timeout() всегда > 0."""
        guard = RequestBudgetGuard(total_sec=0.01, per_call_sec=10.0)
        guard._start = time.monotonic() - 1.0  # Просрочен
        effective = guard.effective_call_timeout()
        assert effective > 0  # Минимальное значение 0.1 по контракту


class TestRequestBudgetFromConfig:
    """Тесты фабричного метода from_config()."""

    def test_from_config_reads_total_sec(self):
        """from_config() читает CLOUD_FAIL_FAST_BUDGET_SECONDS."""
        guard = RequestBudgetGuard.from_config(
            {"CLOUD_FAIL_FAST_BUDGET_SECONDS": 55},
            label="test",
        )
        assert guard.total_sec == 55.0

    def test_from_config_reads_per_call_sec(self):
        """from_config() читает CLOUD_REQUEST_TIMEOUT_SECONDS."""
        guard = RequestBudgetGuard.from_config(
            {"CLOUD_REQUEST_TIMEOUT_SECONDS": 15},
            label="test",
        )
        assert guard.per_call_sec == 15.0

    def test_from_config_uses_defaults(self):
        """from_config() с пустым конфигом использует дефолтные значения."""
        guard = RequestBudgetGuard.from_config({}, label="test")
        assert guard.total_sec == 40.0
        assert guard.per_call_sec == 22.0

    def test_from_config_override_total(self):
        """override_total перекрывает значение из конфига."""
        guard = RequestBudgetGuard.from_config(
            {"CLOUD_FAIL_FAST_BUDGET_SECONDS": 100},
            label="test",
            override_total=30.0,
        )
        assert guard.total_sec == 30.0
