# -*- coding: utf-8 -*-
"""
Тесты для _handle_callback_query, _cb_confirm, _cb_page, _cb_action
в KraabUserbot (src/userbot_bridge.py).

Используем AsyncMock для cq и проверяем корректное поведение роутера.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot_bridge import KraabUserbot


# ─────────────────────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def bot(tmp_path) -> KraabUserbot:
    """Минимальный экземпляр KraabUserbot без реального Telegram-клиента."""
    instance = KraabUserbot.__new__(KraabUserbot)
    return instance


def make_cq(data: str) -> AsyncMock:
    """Создаёт AsyncMock callback query с заданным data и reply-методами."""
    cq = AsyncMock()
    cq.data = data
    cq.message = AsyncMock()
    cq.message.reply = AsyncMock()
    cq.answer = AsyncMock()
    return cq


# ─────────────────────────────────────────────────────────────────────────────
# _handle_callback_query — роутинг по prefix
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleCallbackQueryRouter:
    @pytest.mark.asyncio
    async def test_routes_confirm(self, bot):
        cq = make_cq("confirm:reset:yes")
        await bot._handle_callback_query(cq)
        # answer должен быть вызван (подтверждение)
        cq.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_page(self, bot):
        cq = make_cq("page:results:2")
        await bot._handle_callback_query(cq)
        cq.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_action(self, bot):
        cq = make_cq("action:health_recheck")
        await bot._handle_callback_query(cq)
        cq.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_prefix_answers_warning(self, bot):
        cq = make_cq("unknown:data")
        await bot._handle_callback_query(cq)
        cq.answer.assert_awaited_once()
        # Аргумент ответа содержит признак предупреждения
        call_arg = cq.answer.call_args[0][0]
        assert "⚠️" in call_arg

    @pytest.mark.asyncio
    async def test_empty_data_answers_warning(self, bot):
        cq = make_cq("")
        await bot._handle_callback_query(cq)
        cq.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_handler_does_not_raise(self, bot):
        """Ошибка внутри обработчика не должна пробрасываться наружу."""
        cq = make_cq("confirm:x:yes")
        # message.reply бросает исключение — handler должен его поглотить
        cq.message.reply.side_effect = RuntimeError("network error")
        # Не должен упасть
        await bot._handle_callback_query(cq)


# ─────────────────────────────────────────────────────────────────────────────
# _cb_confirm
# ─────────────────────────────────────────────────────────────────────────────


class TestCbConfirm:
    @pytest.mark.asyncio
    async def test_yes_answer_contains_check(self, bot):
        cq = make_cq("confirm:action:yes")
        await bot._cb_confirm(cq, "confirm:action:yes")
        call_arg = cq.answer.call_args[0][0]
        assert "✅" in call_arg

    @pytest.mark.asyncio
    async def test_no_answer_contains_cross(self, bot):
        cq = make_cq("confirm:action:no")
        await bot._cb_confirm(cq, "confirm:action:no")
        call_arg = cq.answer.call_args[0][0]
        assert "❌" in call_arg

    @pytest.mark.asyncio
    async def test_yes_replies_with_action_id(self, bot):
        cq = make_cq("confirm:do_delete:yes")
        await bot._cb_confirm(cq, "confirm:do_delete:yes")
        cq.message.reply.assert_awaited_once()
        reply_text = cq.message.reply.call_args[0][0]
        assert "do_delete" in reply_text

    @pytest.mark.asyncio
    async def test_no_replies_with_action_id(self, bot):
        cq = make_cq("confirm:do_delete:no")
        await bot._cb_confirm(cq, "confirm:do_delete:no")
        cq.message.reply.assert_awaited_once()
        reply_text = cq.message.reply.call_args[0][0]
        assert "do_delete" in reply_text

    @pytest.mark.asyncio
    async def test_malformed_data_answers_warning(self, bot):
        cq = make_cq("confirm:only_two_parts")
        await bot._cb_confirm(cq, "confirm:only_two_parts")
        call_arg = cq.answer.call_args[0][0]
        assert "⚠️" in call_arg

    @pytest.mark.asyncio
    async def test_malformed_no_reply_sent(self, bot):
        cq = make_cq("confirm:x")
        await bot._cb_confirm(cq, "confirm:x")
        cq.message.reply.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# _cb_page
# ─────────────────────────────────────────────────────────────────────────────


class TestCbPage:
    @pytest.mark.asyncio
    async def test_valid_page_answers_with_number(self, bot):
        cq = make_cq("page:res:2")
        await bot._cb_page(cq, "page:res:2")
        # Страница 2 → «Страница 3» (1-based)
        call_arg = cq.answer.call_args[0][0]
        assert "3" in call_arg

    @pytest.mark.asyncio
    async def test_page_zero_answers_page_one(self, bot):
        cq = make_cq("page:x:0")
        await bot._cb_page(cq, "page:x:0")
        call_arg = cq.answer.call_args[0][0]
        assert "1" in call_arg

    @pytest.mark.asyncio
    async def test_noop_answers_empty(self, bot):
        cq = make_cq("page:x:noop")
        await bot._cb_page(cq, "page:x:noop")
        # answer вызван с пустым аргументом
        cq.answer.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_invalid_page_number_answers_warning(self, bot):
        cq = make_cq("page:x:abc")
        await bot._cb_page(cq, "page:x:abc")
        call_arg = cq.answer.call_args[0][0]
        assert "⚠️" in call_arg

    @pytest.mark.asyncio
    async def test_malformed_data_answers_warning(self, bot):
        cq = make_cq("page:onlytwoparts")
        await bot._cb_page(cq, "page:onlytwoparts")
        call_arg = cq.answer.call_args[0][0]
        assert "⚠️" in call_arg


# ─────────────────────────────────────────────────────────────────────────────
# _cb_action
# ─────────────────────────────────────────────────────────────────────────────


class TestCbAction:
    @pytest.mark.asyncio
    async def test_swarm_team_traders_answers(self, bot):
        cq = make_cq("action:swarm_team:traders")
        await bot._cb_action(cq, "action:swarm_team:traders")
        cq.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swarm_team_replies_with_command_hint(self, bot):
        cq = make_cq("action:swarm_team:coders")
        await bot._cb_action(cq, "action:swarm_team:coders")
        cq.message.reply.assert_awaited_once()
        reply_text = cq.message.reply.call_args[0][0]
        assert "coders" in reply_text
        assert "!swarm" in reply_text

    @pytest.mark.asyncio
    async def test_costs_detail_answers(self, bot):
        cq = make_cq("action:costs_detail")
        with patch("src.core.cost_analytics.cost_analytics") as mock_ca:
            mock_ca.build_usage_report_dict.return_value = {"by_model": {}}
            await bot._cb_action(cq, "action:costs_detail")
        cq.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_costs_detail_no_data_replies_info(self, bot):
        cq = make_cq("action:costs_detail")
        with patch("src.core.cost_analytics.cost_analytics") as mock_ca:
            mock_ca.build_usage_report_dict.return_value = {"by_model": {}}
            await bot._cb_action(cq, "action:costs_detail")
        cq.message.reply.assert_awaited_once()
        reply_text = cq.message.reply.call_args[0][0]
        assert "нет" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_costs_detail_with_model_data_replies_table(self, bot):
        cq = make_cq("action:costs_detail")
        model_data = {
            "gemini-pro": {"calls": 5, "tokens": 1000, "cost_usd": 0.01},
        }
        with patch("src.core.cost_analytics.cost_analytics") as mock_ca:
            mock_ca.build_usage_report_dict.return_value = {"by_model": model_data}
            await bot._cb_action(cq, "action:costs_detail")
        reply_text = cq.message.reply.call_args[0][0]
        assert "gemini-pro" in reply_text

    @pytest.mark.asyncio
    async def test_health_recheck_answers(self, bot):
        cq = make_cq("action:health_recheck")
        await bot._cb_action(cq, "action:health_recheck")
        cq.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_health_recheck_replies(self, bot):
        cq = make_cq("action:health_recheck")
        await bot._cb_action(cq, "action:health_recheck")
        cq.message.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_action_answers_warning(self, bot):
        cq = make_cq("action:totally_unknown")
        await bot._cb_action(cq, "action:totally_unknown")
        call_arg = cq.answer.call_args[0][0]
        assert "⚠️" in call_arg

    @pytest.mark.asyncio
    async def test_swarm_team_analysts(self, bot):
        cq = make_cq("action:swarm_team:analysts")
        await bot._cb_action(cq, "action:swarm_team:analysts")
        reply_text = cq.message.reply.call_args[0][0]
        assert "analysts" in reply_text

    @pytest.mark.asyncio
    async def test_swarm_team_creative(self, bot):
        cq = make_cq("action:swarm_team:creative")
        await bot._cb_action(cq, "action:swarm_team:creative")
        reply_text = cq.message.reply.call_args[0][0]
        assert "creative" in reply_text
