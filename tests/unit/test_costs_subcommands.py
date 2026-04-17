# -*- coding: utf-8 -*-
"""
Тесты для !costs subcommands: today / week / breakdown / budget / trend.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.cost_analytics import CallRecord, CostAnalytics
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (  # noqa: E402
    _costs_aggregate,
    _costs_ascii_trend,
    _costs_filter_calls,
    handle_costs,
)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _make_bot(is_owner: bool = True, args: str = "") -> MagicMock:
    bot = MagicMock()
    level = AccessLevel.OWNER if is_owner else AccessLevel.PARTIAL

    class _FakeProfile:
        def __init__(self):
            self.level = level

    bot._get_access_profile = MagicMock(return_value=_FakeProfile())
    bot._get_command_args = MagicMock(return_value=args)
    return bot


def _make_message(text: str = "!costs") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=100, username="owner")
    msg.chat = SimpleNamespace(id=12345)
    msg.reply = AsyncMock()
    return msg


def _make_analytics_with_calls(*records: tuple) -> CostAnalytics:
    """
    Создать CostAnalytics с набором записей.
    Каждый record — кортеж (model_id, cost_usd, days_ago, channel).
    """
    ca = CostAnalytics()
    for model_id, cost_usd, days_ago, channel in records:
        ts = time.time() - days_ago * 86400
        cr = CallRecord(
            model_id=model_id,
            input_tokens=100,
            output_tokens=50,
            cost_usd=cost_usd,
            timestamp=ts,
            channel=channel or "telegram",
        )
        ca._calls.append(cr)
    return ca


# ─────────────────────────────────────────────
# Unit: _costs_filter_calls
# ─────────────────────────────────────────────


class TestCostsFilterCalls:
    def test_filter_today_returns_current_day(self) -> None:
        """Фильтр за 1 день возвращает только записи за последние 24 часа."""
        now = time.time()
        old = CallRecord("m", 100, 50, 0.01, timestamp=now - 2 * 86400)
        new = CallRecord("m", 100, 50, 0.01, timestamp=now - 3600)
        calls = [old, new]
        result = _costs_filter_calls(calls, days=1)
        assert result == [new]

    def test_filter_none_returns_all(self) -> None:
        now = time.time()
        old = CallRecord("m", 100, 50, 0.01, timestamp=now - 100 * 86400)
        new = CallRecord("m", 100, 50, 0.01, timestamp=now)
        result = _costs_filter_calls([old, new], days=None)
        assert len(result) == 2

    def test_filter_7_days(self) -> None:
        now = time.time()
        r1 = CallRecord("m", 100, 50, 0.01, timestamp=now - 1 * 86400)
        r2 = CallRecord("m", 100, 50, 0.01, timestamp=now - 5 * 86400)
        r3 = CallRecord("m", 100, 50, 0.01, timestamp=now - 10 * 86400)
        result = _costs_filter_calls([r1, r2, r3], days=7)
        assert r1 in result
        assert r2 in result
        assert r3 not in result

    def test_filter_empty_list(self) -> None:
        assert _costs_filter_calls([], days=7) == []


# ─────────────────────────────────────────────
# Unit: _costs_aggregate
# ─────────────────────────────────────────────


class TestCostsAggregate:
    def test_week_aggregates_7_days(self) -> None:
        """Агрегация суммирует cost и вызовы корректно."""
        now = time.time()
        calls = [
            CallRecord("google/gemini-3-pro", 100, 50, 0.05, timestamp=now - 1 * 86400),
            CallRecord("google/gemini-3-pro", 100, 50, 0.10, timestamp=now - 3 * 86400),
            CallRecord("openai/gpt-4", 100, 50, 0.20, timestamp=now - 6 * 86400),
        ]
        agg = _costs_aggregate(calls)
        assert agg["calls_count"] == 3
        assert abs(agg["total_cost"] - 0.35) < 1e-9
        assert "google/gemini-3-pro" in agg["by_model"]
        assert agg["by_model"]["google/gemini-3-pro"]["calls"] == 2

    def test_breakdown_by_provider(self) -> None:
        """Провайдер извлекается из model_id до «/»."""
        now = time.time()
        calls = [
            CallRecord("google/gemini", 100, 50, 0.10, timestamp=now),
            CallRecord("google/flash", 100, 50, 0.05, timestamp=now),
            CallRecord("anthropic/claude", 100, 50, 0.20, timestamp=now),
        ]
        agg = _costs_aggregate(calls)
        assert "google" in agg["by_provider"]
        assert "anthropic" in agg["by_provider"]
        assert abs(agg["by_provider"]["google"]["cost_usd"] - 0.15) < 1e-9
        assert agg["by_provider"]["anthropic"]["calls"] == 1

    def test_model_without_slash(self) -> None:
        """Модели без «/» в model_id используются как провайдер напрямую."""
        now = time.time()
        calls = [CallRecord("gemini", 100, 50, 0.05, timestamp=now)]
        agg = _costs_aggregate(calls)
        assert "gemini" in agg["by_provider"]

    def test_aggregate_empty(self) -> None:
        agg = _costs_aggregate([])
        assert agg["calls_count"] == 0
        assert agg["total_cost"] == 0.0
        assert agg["by_model"] == {}
        assert agg["by_provider"] == {}


# ─────────────────────────────────────────────
# Unit: _costs_ascii_trend
# ─────────────────────────────────────────────


class TestCostsAsciiTrend:
    def test_trend_ascii_format(self) -> None:
        """Тренд содержит блок-символы и корректные метки."""
        now = time.time()
        calls = [
            CallRecord("m", 100, 50, 0.10, timestamp=now - i * 86400)
            for i in range(30)
        ]
        result = _costs_ascii_trend(calls, days=30)
        assert "Тренд за 30 дней" in result
        assert "total=" in result
        assert "avg=" in result

    def test_trend_empty_calls(self) -> None:
        """Пустой тренд без ошибок."""
        result = _costs_ascii_trend([], days=30)
        assert "Тренд" in result
        assert "total=$0.0000" in result

    def test_trend_bar_length(self) -> None:
        """Строка баров содержит ровно days символов."""
        now = time.time()
        calls = [
            CallRecord("m", 100, 50, 0.01, timestamp=now - i * 86400)
            for i in range(10)
        ]
        result = _costs_ascii_trend(calls, days=10)
        # Найти строку с баром (вторая строка после заголовка)
        lines = result.split("\n")
        bar_line = lines[1].strip("`")
        assert len(bar_line) == 10


# ─────────────────────────────────────────────
# Integration: handle_costs subcommands
# ─────────────────────────────────────────────


class TestHandleCostsSubcommands:
    @pytest.mark.asyncio
    async def test_today_returns_current_day(self) -> None:
        """!costs today — вызывает reply с данными за сегодня."""
        bot = _make_bot(args="today")
        msg = _make_message("!costs today")
        ca = _make_analytics_with_calls(
            ("google/gemini", 0.05, 0, "telegram"),
            ("google/gemini", 0.05, 5, "telegram"),  # старый, не сегодня
        )
        with patch("src.handlers.command_handlers.cost_analytics", ca):
            await handle_costs(bot, msg)
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "сегодня" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_week_aggregates_7_days(self) -> None:
        """!costs week — суммирует расходы за 7 дней."""
        bot = _make_bot(args="week")
        msg = _make_message("!costs week")
        ca = _make_analytics_with_calls(
            ("google/gemini", 0.10, 1, "telegram"),
            ("google/gemini", 0.15, 3, "telegram"),
            ("openai/gpt-4", 0.20, 30, "telegram"),  # старый
        )
        with patch("src.handlers.command_handlers.cost_analytics", ca):
            await handle_costs(bot, msg)
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "7 дней" in reply_text

    @pytest.mark.asyncio
    async def test_breakdown_by_provider(self) -> None:
        """!costs breakdown — показывает разбивку по провайдерам."""
        bot = _make_bot(args="breakdown")
        msg = _make_message("!costs breakdown")
        ca = _make_analytics_with_calls(
            ("google/gemini-3-pro", 0.20, 0, "telegram"),
            ("anthropic/claude-3", 0.30, 1, "api"),
        )
        with patch("src.handlers.command_handlers.cost_analytics", ca):
            await handle_costs(bot, msg)
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "провайдер" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_budget_shows_percent_used(self) -> None:
        """!costs budget — показывает процент использования бюджета."""
        bot = _make_bot(args="budget")
        msg = _make_message("!costs budget")
        ca = CostAnalytics(monthly_budget_usd=10.0)
        now = time.time()
        ca._calls.append(
            CallRecord("google/gemini", 100, 50, 2.50, timestamp=now - 1 * 86400)
        )
        with patch("src.handlers.command_handlers.cost_analytics", ca):
            await handle_costs(bot, msg)
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "%" in reply_text
        assert "$10.00" in reply_text

    @pytest.mark.asyncio
    async def test_trend_called(self) -> None:
        """!costs trend — вызывает reply с ASCII-трендом."""
        bot = _make_bot(args="trend")
        msg = _make_message("!costs trend")
        ca = _make_analytics_with_calls(("google/gemini", 0.05, 0, "telegram"))
        with patch("src.handlers.command_handlers.cost_analytics", ca):
            await handle_costs(bot, msg)
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Тренд" in reply_text

    @pytest.mark.asyncio
    async def test_unknown_subcommand_falls_back_to_default(self) -> None:
        """!costs foobar — падает на default summary."""
        bot = _make_bot(args="foobar")
        msg = _make_message("!costs foobar")
        ca = CostAnalytics()
        ca.record_usage({"prompt_tokens": 100, "completion_tokens": 50}, model_id="gemini")

        with patch("src.handlers.command_handlers.cost_analytics", ca):
            with patch("src.handlers.command_handlers.build_costs_detail_buttons", return_value=None):
                await handle_costs(bot, msg)
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Cost Report" in reply_text

    @pytest.mark.asyncio
    async def test_non_owner_raises(self) -> None:
        """Не-владелец получает UserInputError."""
        bot = _make_bot(is_owner=False)
        msg = _make_message("!costs")
        with pytest.raises(UserInputError):
            await handle_costs(bot, msg)

    @pytest.mark.asyncio
    async def test_empty_args_returns_default(self) -> None:
        """!costs без аргументов — default summary."""
        bot = _make_bot(args="")
        msg = _make_message("!costs")
        ca = CostAnalytics()
        with patch("src.handlers.command_handlers.cost_analytics", ca):
            with patch("src.handlers.command_handlers.build_costs_detail_buttons", return_value=None):
                await handle_costs(bot, msg)
        msg.reply.assert_called_once()
