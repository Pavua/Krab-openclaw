# -*- coding: utf-8 -*-
"""
Юнит-тесты для !help command handler.

Покрываем:
  - handle_help отправляет хотя бы одно сообщение
  - текст содержит все категории разделов
  - текст содержит ключевые команды из каждой категории
  - пагинация: если combined > PAGE_LIMIT — два reply
  - пагинация: если combined <= PAGE_LIMIT — один reply
  - каждая часть не превышает Telegram-лимит 4096 символов
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.command_handlers import handle_help

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_message() -> tuple[SimpleNamespace, SimpleNamespace]:
    """Возвращает (bot, message) stubs."""
    msg = SimpleNamespace(reply=AsyncMock())
    bot = SimpleNamespace()
    return bot, msg


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestHandleHelp:
    """Тесты !help handler."""

    @pytest.mark.asyncio
    async def test_help_sends_at_least_one_reply(self) -> None:
        """handle_help должен отправить хотя бы одно сообщение."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        assert msg.reply.called
        assert msg.reply.call_count >= 1

    @pytest.mark.asyncio
    async def test_help_reply_count_one_or_two(self) -> None:
        """Должно быть ровно 1 или 2 reply — не больше."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        assert msg.reply.call_count in (1, 2)

    @pytest.mark.asyncio
    async def test_help_contains_krab_header(self) -> None:
        """Текст должен содержать заголовок 🦀 Krab Commands."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "Krab Commands" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_section_basic(self) -> None:
        """Секция Основные должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "Основные" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_section_ai(self) -> None:
        """Секция AI должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "AI" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_section_swarm(self) -> None:
        """Секция Swarm должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "Swarm" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_section_costs(self) -> None:
        """Секция Расходы и бюджет должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "бюджет" in all_text.lower() or "costs" in all_text.lower()

    @pytest.mark.asyncio
    async def test_help_contains_section_notes(self) -> None:
        """Секция заметки / закладки должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "закладк" in all_text.lower() or "bookmark" in all_text.lower()

    @pytest.mark.asyncio
    async def test_help_contains_section_management(self) -> None:
        """Секция управления сообщениями должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!del" in all_text or "!purge" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_section_modes(self) -> None:
        """Секция режимов (!voice, !тишина) должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!voice" in all_text
        assert "тишина" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_section_system(self) -> None:
        """Секция системных команд должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!sysinfo" in all_text or "!mac" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_section_dev(self) -> None:
        """Секция Dev / AI CLI должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!agent" in all_text or "!codex" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_scheduler_commands(self) -> None:
        """Команды планировщика должны присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!remind" in all_text
        assert "!schedule" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_translator_section(self) -> None:
        """Секция Translator должна присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!translator" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_new_commands(self) -> None:
        """Новые команды session 7 должны присутствовать в справке."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        # Команды добавленные в session 7
        for cmd in [
            "!catchup",
            "!monitor",
            "!alias",
            "!autodel",
            "!collect",
            "!fwd",
            "!react",
            "!export",
        ]:
            assert cmd in all_text, f"Команда {cmd!r} отсутствует в !help"

    @pytest.mark.asyncio
    async def test_help_each_message_fits_telegram_limit(self) -> None:
        """Каждое reply не должно превышать 4096 символов (Telegram лимит)."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        for call in msg.reply.call_args_list:
            text = str(call.args[0])
            assert len(text) <= 4096, f"Сообщение превышает лимит: {len(text)} символов"

    @pytest.mark.asyncio
    async def test_help_total_text_length_reasonable(self) -> None:
        """Суммарный текст справки должен быть содержательным (>1000 символов)."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        total_len = sum(len(str(call.args[0])) for call in msg.reply.call_args_list)
        assert total_len > 1000, "Суммарный текст справки слишком короткий"

    @pytest.mark.asyncio
    async def test_help_pagination_two_messages(self) -> None:
        """С текущим объёмом справки ожидается ровно 2 reply (пагинация)."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        # part1 + part2 вместе > 4000, поэтому должно быть 2 сообщения
        assert msg.reply.call_count == 2, (
            f"Ожидалось 2 сообщения (пагинация), получено {msg.reply.call_count}"
        )

    @pytest.mark.asyncio
    async def test_help_first_part_has_part1_marker(self) -> None:
        """Первое сообщение должно содержать маркер (1/2)."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        if msg.reply.call_count == 2:
            first_text = str(msg.reply.call_args_list[0].args[0])
            assert "1/2" in first_text

    @pytest.mark.asyncio
    async def test_help_second_part_has_part2_marker(self) -> None:
        """Второе сообщение должно содержать маркер (2/2)."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        if msg.reply.call_count == 2:
            second_text = str(msg.reply.call_args_list[1].args[0])
            assert "2/2" in second_text

    @pytest.mark.asyncio
    async def test_help_contains_help_itself(self) -> None:
        """Команда !help должна упоминать сама себя в справке."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!help" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_swarm_research(self) -> None:
        """!swarm research из session 6/7 должен присутствовать."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "research" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_digest(self) -> None:
        """!digest должен присутствовать в справке."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!digest" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_costs(self) -> None:
        """!costs должен присутствовать в справке."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!costs" in all_text

    @pytest.mark.asyncio
    async def test_help_contains_budget(self) -> None:
        """!budget должен присутствовать в справке."""
        bot, msg = _make_message()
        await handle_help(bot, msg)
        all_text = " ".join(str(call.args[0]) for call in msg.reply.call_args_list)
        assert "!budget" in all_text
