# -*- coding: utf-8 -*-
"""
Тесты команды !slowmode — управление slowmode в группах.

Покрываем:
1) !slowmode status — показывает текущий slowmode
2) !slowmode (без аргументов) — alias для status
3) !slowmode 60 — устанавливает значение
4) !slowmode 0 — выключает
5) !slowmode off — выключает (алиас)
6) !slowmode выкл — выключает (алиас RU)
7) Все валидные значения: 10, 30, 60, 300, 900, 3600
8) Невалидное число → UserInputError
9) Нечисловой аргумент → UserInputError
10) Чат не группа (PRIVATE) → UserInputError
11) CHAT_ADMIN_REQUIRED → UserInputError
12) Прочая ошибка Pyrogram → UserInputError
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_slowmode


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------

@asynccontextmanager
async def raises_user_input(match_text: str) -> AsyncIterator[None]:
    """Контекст-менеджер: ожидаем UserInputError с user_message содержащим match_text."""
    try:
        yield
    except UserInputError as exc:
        assert match_text in exc.user_message, (
            f"Ожидали '{match_text}' в user_message, получили: {exc.user_message!r}"
        )
    else:
        pytest.fail(f"Ожидали UserInputError с '{match_text}', но исключение не было выброшено")


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_bot(
    *,
    slow_mode_delay: int | None = 0,
    set_slow_mode_side_effect=None,
) -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""
    full_chat = SimpleNamespace(slow_mode_delay=slow_mode_delay)
    get_chat_mock = AsyncMock(return_value=full_chat)
    set_slow_mode_mock = AsyncMock(side_effect=set_slow_mode_side_effect)

    bot = SimpleNamespace(
        client=SimpleNamespace(
            get_chat=get_chat_mock,
            set_slow_mode=set_slow_mode_mock,
        ),
    )
    return bot


def _make_message(
    text: str = "!slowmode",
    chat_type: str = "SUPERGROUP",
    chat_id: int = -100123456,
    chat_title: str = "Test Group",
) -> SimpleNamespace:
    """Минимальный mock pyrogram.Message."""
    chat_type_obj = MagicMock()
    chat_type_obj.name = chat_type

    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(
            id=chat_id,
            type=chat_type_obj,
            title=chat_title,
        ),
        reply=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# status / без аргументов
# ---------------------------------------------------------------------------

class TestSlowmodeStatus:
    @pytest.mark.asyncio
    async def test_status_no_arg_shows_current(self) -> None:
        """!slowmode без аргументов показывает текущее значение."""
        bot = _make_bot(slow_mode_delay=60)
        msg = _make_message("!slowmode")

        await handle_slowmode(bot, msg)

        msg.reply.assert_awaited_once()
        text = msg.reply.call_args[0][0]
        assert "Slowmode" in text
        assert "1 мин" in text

    @pytest.mark.asyncio
    async def test_status_explicit_shows_current(self) -> None:
        """!slowmode status показывает текущее значение."""
        bot = _make_bot(slow_mode_delay=0)
        msg = _make_message("!slowmode status")

        await handle_slowmode(bot, msg)

        msg.reply.assert_awaited_once()
        text = msg.reply.call_args[0][0]
        assert "выключен" in text

    @pytest.mark.asyncio
    async def test_status_delay_none_treated_as_zero(self) -> None:
        """slow_mode_delay=None трактуется как 0 (выключен)."""
        bot = _make_bot(slow_mode_delay=None)
        msg = _make_message("!slowmode status")

        await handle_slowmode(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "выключен" in text

    @pytest.mark.asyncio
    async def test_status_unknown_delay_shown_as_sec(self) -> None:
        """Нестандартное значение задержки отображается в секундах."""
        bot = _make_bot(slow_mode_delay=45)
        msg = _make_message("!slowmode status")

        await handle_slowmode(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "45 сек" in text

    @pytest.mark.asyncio
    async def test_status_get_chat_error_raises(self) -> None:
        """Если get_chat бросает исключение — UserInputError."""
        bot = _make_bot()
        bot.client.get_chat = AsyncMock(side_effect=Exception("RPC error"))
        msg = _make_message("!slowmode status")

        async with raises_user_input("Не удалось получить"):
            await handle_slowmode(bot, msg)


# ---------------------------------------------------------------------------
# Установка slowmode
# ---------------------------------------------------------------------------

class TestSlowmodeSet:
    @pytest.mark.asyncio
    async def test_set_60_seconds(self) -> None:
        """!slowmode 60 устанавливает 60 секунд."""
        bot = _make_bot()
        msg = _make_message("!slowmode 60")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 60)
        text = msg.reply.call_args[0][0]
        assert "1 мин" in text

    @pytest.mark.asyncio
    async def test_set_10_seconds(self) -> None:
        """!slowmode 10."""
        bot = _make_bot()
        msg = _make_message("!slowmode 10")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 10)
        assert "10 сек" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_30_seconds(self) -> None:
        """!slowmode 30."""
        bot = _make_bot()
        msg = _make_message("!slowmode 30")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 30)
        assert "30 сек" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_300_seconds(self) -> None:
        """!slowmode 300."""
        bot = _make_bot()
        msg = _make_message("!slowmode 300")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 300)
        assert "5 мин" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_900_seconds(self) -> None:
        """!slowmode 900."""
        bot = _make_bot()
        msg = _make_message("!slowmode 900")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 900)
        assert "15 мин" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_3600_seconds(self) -> None:
        """!slowmode 3600."""
        bot = _make_bot()
        msg = _make_message("!slowmode 3600")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 3600)
        assert "1 час" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_0_seconds_disabled(self) -> None:
        """!slowmode 0 выключает slowmode."""
        bot = _make_bot()
        msg = _make_message("!slowmode 0")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 0)
        assert "выключен" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# !slowmode off / выкл
# ---------------------------------------------------------------------------

class TestSlowmodeOff:
    @pytest.mark.asyncio
    async def test_off_disables_slowmode(self) -> None:
        """!slowmode off выключает slowmode (seconds=0)."""
        bot = _make_bot()
        msg = _make_message("!slowmode off")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 0)
        assert "выключен" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_vykl_alias_disables_slowmode(self) -> None:
        """!slowmode выкл — русский алиас."""
        bot = _make_bot()
        msg = _make_message("!slowmode выкл")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once_with(-100123456, 0)
        assert "выключен" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Ошибки валидации
# ---------------------------------------------------------------------------

class TestSlowmodeValidation:
    @pytest.mark.asyncio
    async def test_invalid_number_raises(self) -> None:
        """Число не из допустимых → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!slowmode 45")

        async with raises_user_input("Недопустимое значение"):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_invalid_number_120_raises(self) -> None:
        """!slowmode 120 — не допустимое значение."""
        bot = _make_bot()
        msg = _make_message("!slowmode 120")

        async with raises_user_input("Недопустимое значение"):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_non_numeric_arg_raises(self) -> None:
        """Нечисловой аргумент → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!slowmode abc")

        async with raises_user_input("Неверный аргумент"):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_random_word_raises(self) -> None:
        """Произвольное слово → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!slowmode включить")

        async with raises_user_input("Неверный аргумент"):
            await handle_slowmode(bot, msg)


# ---------------------------------------------------------------------------
# Тип чата
# ---------------------------------------------------------------------------

class TestSlowmodeChatType:
    @pytest.mark.asyncio
    async def test_private_chat_raises(self) -> None:
        """В приватном чате slowmode недоступен."""
        bot = _make_bot()
        msg = _make_message("!slowmode 60", chat_type="PRIVATE")

        async with raises_user_input("только в группах"):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_group_allowed(self) -> None:
        """В обычной группе slowmode работает."""
        bot = _make_bot()
        msg = _make_message("!slowmode 60", chat_type="GROUP")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_supergroup_allowed(self) -> None:
        """В супергруппе slowmode работает."""
        bot = _make_bot()
        msg = _make_message("!slowmode 60", chat_type="SUPERGROUP")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_allowed(self) -> None:
        """В канале slowmode работает."""
        bot = _make_bot()
        msg = _make_message("!slowmode 60", chat_type="CHANNEL")

        await handle_slowmode(bot, msg)

        bot.client.set_slow_mode.assert_awaited_once()


# ---------------------------------------------------------------------------
# Ошибки API Telegram
# ---------------------------------------------------------------------------

class TestSlowmodeApiErrors:
    @pytest.mark.asyncio
    async def test_admin_required_error(self) -> None:
        """CHAT_ADMIN_REQUIRED → UserInputError о правах."""
        bot = _make_bot(
            set_slow_mode_side_effect=Exception("CHAT_ADMIN_REQUIRED: not an admin")
        )
        msg = _make_message("!slowmode 60")

        async with raises_user_input("Нет прав администратора"):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_generic_api_error(self) -> None:
        """Произвольная ошибка Pyrogram → UserInputError."""
        bot = _make_bot(
            set_slow_mode_side_effect=Exception("FLOOD_WAIT_5")
        )
        msg = _make_message("!slowmode 60")

        async with raises_user_input("Ошибка установки slowmode"):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_admin_keyword_in_error_detected(self) -> None:
        """Сообщение с 'admin' в тексте → UserInputError о правах."""
        bot = _make_bot(
            set_slow_mode_side_effect=Exception("You must be an admin")
        )
        msg = _make_message("!slowmode 300")

        async with raises_user_input("Нет прав администратора"):
            await handle_slowmode(bot, msg)


# ---------------------------------------------------------------------------
# Отображение имени чата в ответе
# ---------------------------------------------------------------------------

class TestSlowmodeChatDisplay:
    @pytest.mark.asyncio
    async def test_chat_title_in_reply(self) -> None:
        """Название чата присутствует в ответе при установке."""
        bot = _make_bot()
        msg = _make_message("!slowmode 60", chat_title="Моя группа")

        await handle_slowmode(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "Моя группа" in text

    @pytest.mark.asyncio
    async def test_chat_id_used_when_no_title(self) -> None:
        """Если title=None, используется chat_id."""
        bot = _make_bot()
        msg = _make_message("!slowmode 60")
        msg.chat.title = None

        await handle_slowmode(bot, msg)

        text = msg.reply.call_args[0][0]
        assert str(msg.chat.id) in text

    @pytest.mark.asyncio
    async def test_off_reply_contains_disabled_message(self) -> None:
        """При slowmode off ответ содержит 'выключен'."""
        bot = _make_bot()
        msg = _make_message("!slowmode off")

        await handle_slowmode(bot, msg)

        assert "выключен" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_set_reply_contains_slowmode_emoji(self) -> None:
        """При успешной установке ответ содержит 🐢."""
        bot = _make_bot()
        msg = _make_message("!slowmode 30")

        await handle_slowmode(bot, msg)

        assert "🐢" in msg.reply.call_args[0][0]
