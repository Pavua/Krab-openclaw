# -*- coding: utf-8 -*-
"""
Тесты для Telegram-команд !costs, !budget, !digest.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.cost_analytics import CostAnalytics
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_budget, handle_costs, handle_digest


def _make_bot(is_owner: bool = True) -> MagicMock:
    """Создаёт mock KraabUserbot с нужным access-профилем."""
    bot = MagicMock()
    level = AccessLevel.OWNER if is_owner else AccessLevel.PARTIAL

    class _FakeProfile:
        def __init__(self):
            self.level = level

    bot._get_access_profile = MagicMock(return_value=_FakeProfile())
    bot._get_command_args = MagicMock(return_value="")
    return bot


def _make_message(text: str = "!costs") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=100, username="owner")
    msg.chat = SimpleNamespace(id=12345)
    msg.reply = AsyncMock()
    return msg


# ─────────────────────────────────────────────
# handle_costs
# ─────────────────────────────────────────────


class TestHandleCosts:
    @pytest.mark.asyncio
    async def test_owner_gets_report(self) -> None:
        """Владелец получает cost report."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!costs")

        analytics = CostAnalytics()
        analytics.record_usage(
            {"prompt_tokens": 100, "completion_tokens": 50}, model_id="gemini", channel="telegram"
        )

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_costs(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Cost Report" in reply_text
        assert "Вызовов:" in reply_text

    @pytest.mark.asyncio
    async def test_non_owner_blocked(self) -> None:
        """Не-владелец получает UserInputError."""
        bot = _make_bot(is_owner=False)
        msg = _make_message("!costs")

        with pytest.raises(UserInputError):
            await handle_costs(bot, msg)

    @pytest.mark.asyncio
    async def test_empty_analytics_report(self) -> None:
        """Пустая аналитика — отчёт без ошибок."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!costs")

        analytics = CostAnalytics()

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_costs(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Cost Report" in reply_text

    @pytest.mark.asyncio
    async def test_by_model_section_present(self) -> None:
        """Секция «По моделям» присутствует при наличии вызовов."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!costs")

        analytics = CostAnalytics()
        analytics.record_usage(
            {"prompt_tokens": 500, "completion_tokens": 200}, model_id="gpt-4o", channel="telegram"
        )
        analytics.record_usage(
            {"prompt_tokens": 100, "completion_tokens": 50}, model_id="gemini", channel="web"
        )

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_costs(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "По моделям" in reply_text
        assert "gpt-4o" in reply_text

    @pytest.mark.asyncio
    async def test_by_channel_section_present(self) -> None:
        """Секция «По каналам» присутствует при наличии данных."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!costs")

        analytics = CostAnalytics()
        analytics.record_usage(
            {"prompt_tokens": 100, "completion_tokens": 50}, model_id="gemini", channel="telegram"
        )

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_costs(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "По каналам" in reply_text
        assert "telegram" in reply_text

    @pytest.mark.asyncio
    async def test_budget_line_with_budget_set(self) -> None:
        """Строка бюджета с процентом при заданном бюджете."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!costs")

        analytics = CostAnalytics(monthly_budget_usd=10.0)
        analytics.record_usage(
            {"prompt_tokens": 100, "completion_tokens": 50}, model_id="gemini", channel="telegram"
        )

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_costs(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "$10.00" in reply_text or "10.00" in reply_text

    @pytest.mark.asyncio
    async def test_budget_line_without_budget(self) -> None:
        """При отсутствии бюджета показывает «не задан»."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!costs")

        analytics = CostAnalytics(monthly_budget_usd=0.0)

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_costs(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "не задан" in reply_text


# ─────────────────────────────────────────────
# handle_budget
# ─────────────────────────────────────────────


class TestHandleBudget:
    @pytest.mark.asyncio
    async def test_non_owner_blocked(self) -> None:
        """Не-владелец заблокирован."""
        bot = _make_bot(is_owner=False)
        msg = _make_message("!budget")

        with pytest.raises(UserInputError):
            await handle_budget(bot, msg)

    @pytest.mark.asyncio
    async def test_show_current_no_budget(self) -> None:
        """Без аргумента показывает «не задан» если бюджет не установлен."""
        bot = _make_bot(is_owner=True)
        bot._get_command_args = MagicMock(return_value="")
        msg = _make_message("!budget")

        analytics = CostAnalytics(monthly_budget_usd=0.0)

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_budget(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "не задан" in reply_text

    @pytest.mark.asyncio
    async def test_show_current_with_budget(self) -> None:
        """Без аргумента показывает текущий бюджет."""
        bot = _make_bot(is_owner=True)
        bot._get_command_args = MagicMock(return_value="")
        msg = _make_message("!budget")

        analytics = CostAnalytics(monthly_budget_usd=20.0)

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_budget(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "20.00" in reply_text

    @pytest.mark.asyncio
    async def test_set_budget(self) -> None:
        """С аргументом устанавливает новый бюджет."""
        bot = _make_bot(is_owner=True)
        bot._get_command_args = MagicMock(return_value="15.50")
        msg = _make_message("!budget 15.50")

        analytics = CostAnalytics(monthly_budget_usd=0.0)

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_budget(bot, msg)

        assert analytics.get_monthly_budget_usd() == 15.50
        reply_text = msg.reply.call_args[0][0]
        assert "15.50" in reply_text

    @pytest.mark.asyncio
    async def test_set_budget_zero_resets(self) -> None:
        """Установка 0 сбрасывает бюджет."""
        bot = _make_bot(is_owner=True)
        bot._get_command_args = MagicMock(return_value="0")
        msg = _make_message("!budget 0")

        analytics = CostAnalytics(monthly_budget_usd=10.0)

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_budget(bot, msg)

        assert analytics.get_monthly_budget_usd() == 0.0
        reply_text = msg.reply.call_args[0][0]
        assert "сброшен" in reply_text.lower() or "без ограничений" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_invalid_value_raises(self) -> None:
        """Некорректное значение вызывает UserInputError."""
        bot = _make_bot(is_owner=True)
        bot._get_command_args = MagicMock(return_value="abc")
        msg = _make_message("!budget abc")

        analytics = CostAnalytics()

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            with pytest.raises(UserInputError):
                await handle_budget(bot, msg)

    @pytest.mark.asyncio
    async def test_negative_value_raises(self) -> None:
        """Отрицательное значение вызывает UserInputError."""
        bot = _make_bot(is_owner=True)
        bot._get_command_args = MagicMock(return_value="-5")
        msg = _make_message("!budget -5")

        analytics = CostAnalytics()

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            with pytest.raises(UserInputError):
                await handle_budget(bot, msg)

    @pytest.mark.asyncio
    async def test_comma_decimal_separator(self) -> None:
        """Поддержка запятой как разделителя десятичных."""
        bot = _make_bot(is_owner=True)
        bot._get_command_args = MagicMock(return_value="12,50")
        msg = _make_message("!budget 12,50")

        analytics = CostAnalytics()

        with patch("src.handlers.command_handlers.cost_analytics", analytics):
            await handle_budget(bot, msg)

        assert analytics.get_monthly_budget_usd() == 12.50


# ─────────────────────────────────────────────
# handle_digest
# ─────────────────────────────────────────────


class TestHandleDigest:
    @pytest.mark.asyncio
    async def test_non_owner_blocked(self) -> None:
        """Не-владелец заблокирован."""
        bot = _make_bot(is_owner=False)
        msg = _make_message("!digest")

        with pytest.raises(UserInputError):
            await handle_digest(bot, msg)

    @pytest.mark.asyncio
    async def test_successful_digest_no_callback(self) -> None:
        """Digest без telegram_callback показывает сводку."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!digest")

        mock_digest = MagicMock()
        mock_digest._telegram_callback = None
        mock_digest.generate_digest = AsyncMock(
            return_value={
                "ok": True,
                "total_rounds": 5,
                "cost_week_usd": 0.25,
                "attention_count": 2,
            }
        )

        with patch("src.handlers.command_handlers.weekly_digest", mock_digest):
            await handle_digest(bot, msg)

        # Первый reply — «Генерирую...», второй — итог
        assert msg.reply.call_count == 2
        last_reply = msg.reply.call_args_list[-1][0][0]
        assert "5" in last_reply  # total_rounds
        assert "0.25" in last_reply or "0.2500" in last_reply

    @pytest.mark.asyncio
    async def test_successful_digest_with_callback(self) -> None:
        """Digest с настроенным telegram_callback — минимальное подтверждение."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!digest")

        mock_digest = MagicMock()
        mock_digest._telegram_callback = AsyncMock()  # callback установлен
        mock_digest.generate_digest = AsyncMock(
            return_value={
                "ok": True,
                "total_rounds": 3,
                "cost_week_usd": 0.10,
                "attention_count": 0,
            }
        )

        with patch("src.handlers.command_handlers.weekly_digest", mock_digest):
            await handle_digest(bot, msg)

        assert msg.reply.call_count == 2
        last_reply = msg.reply.call_args_list[-1][0][0]
        assert "✅" in last_reply

    @pytest.mark.asyncio
    async def test_digest_failure(self) -> None:
        """При ok=False отправляет сообщение об ошибке."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!digest")

        mock_digest = MagicMock()
        mock_digest._telegram_callback = None
        mock_digest.generate_digest = AsyncMock(
            return_value={"ok": False, "error": "inbox unavailable"}
        )

        with patch("src.handlers.command_handlers.weekly_digest", mock_digest):
            await handle_digest(bot, msg)

        last_reply = msg.reply.call_args_list[-1][0][0]
        assert "❌" in last_reply
        assert "inbox unavailable" in last_reply

    @pytest.mark.asyncio
    async def test_digest_exception(self) -> None:
        """При исключении из generate_digest отправляет ошибку."""
        bot = _make_bot(is_owner=True)
        msg = _make_message("!digest")

        mock_digest = MagicMock()
        mock_digest._telegram_callback = None
        mock_digest.generate_digest = AsyncMock(side_effect=RuntimeError("DB error"))

        with patch("src.handlers.command_handlers.weekly_digest", mock_digest):
            await handle_digest(bot, msg)

        last_reply = msg.reply.call_args_list[-1][0][0]
        assert "❌" in last_reply
        assert "DB error" in last_reply
