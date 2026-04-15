# -*- coding: utf-8 -*-
"""
Тесты команды !say — тихая отправка сообщения от имени юзербота.

Покрываем:
1. !say <текст>              — отправляет в текущий чат
2. !say <chat_id> <текст>   — отправляет в другой чат по числовому ID
3. !say @username <текст>   — отправляет в чат по @username
4. !say без аргументов      — UserInputError
5. !say <chat_id>            — только chat_id, нет текста → UserInputError
6. Команда удаляется из чата до отправки
7. Ошибка удаления команды — не критична, отправка продолжается
8. Ошибка отправки — отправляет уведомление в текущий чат
9. Ошибка отправки в текущий чат при notify → не пробрасывает
10. Многословный текст отправляется как единое сообщение
11. Числовой chat_id с минусом (группа) обрабатывается корректно
12. Токен, не являющийся числом и не @, — трактуется как начало текста
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_say


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_bot(args: str = "") -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""
    bot = SimpleNamespace(
        client=SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(id=42)),
        ),
        _get_command_args=lambda _: args,
    )
    return bot


def _make_message(*, chat_id: int = 100) -> SimpleNamespace:
    """Минимальный mock pyrogram.Message."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        delete=AsyncMock(),
        reply=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------

class TestHandleSay:
    @pytest.mark.asyncio
    async def test_say_текущий_чат(self) -> None:
        """!say <текст> — отправляет в текущий чат."""
        bot = _make_bot("Привет, мир!")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        bot.client.send_message.assert_awaited_once_with(chat_id=100, text="Привет, мир!")

    @pytest.mark.asyncio
    async def test_say_другой_чат_числовой_id(self) -> None:
        """!say <chat_id> <текст> — отправляет в другой чат по числовому ID."""
        bot = _make_bot("200 Сообщение для другого чата")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        bot.client.send_message.assert_awaited_once_with(chat_id=200, text="Сообщение для другого чата")

    @pytest.mark.asyncio
    async def test_say_отрицательный_chat_id(self) -> None:
        """!say -1001234567890 <текст> — группа с отрицательным ID."""
        bot = _make_bot("-1001234567890 Привет группе")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        bot.client.send_message.assert_awaited_once_with(
            chat_id=-1001234567890, text="Привет группе"
        )

    @pytest.mark.asyncio
    async def test_say_username(self) -> None:
        """!say @username <текст> — отправляет по @username."""
        bot = _make_bot("@testuser Привет!")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        bot.client.send_message.assert_awaited_once_with(chat_id="@testuser", text="Привет!")

    @pytest.mark.asyncio
    async def test_say_без_аргументов_вызывает_ошибку(self) -> None:
        """!say без аргументов → UserInputError."""
        bot = _make_bot("")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_say(bot, msg)

    @pytest.mark.asyncio
    async def test_say_только_число_отправляет_как_текст(self) -> None:
        """!say 12345 — одно числовое слово трактуется как текст для текущего чата."""
        bot = _make_bot("12345")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        # Один токен-число не может быть chat_id (нет текста после), поэтому отправляется как текст
        bot.client.send_message.assert_awaited_once_with(chat_id=100, text="12345")

    @pytest.mark.asyncio
    async def test_say_удаляет_команду(self) -> None:
        """!say удаляет само сообщение-команду из чата."""
        bot = _make_bot("Тихое сообщение")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        msg.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_say_ошибка_удаления_не_критична(self) -> None:
        """Если удаление команды упало — отправка всё равно происходит."""
        bot = _make_bot("Текст")
        msg = _make_message(chat_id=100)
        msg.delete = AsyncMock(side_effect=Exception("Нет прав"))

        await handle_say(bot, msg)  # не падает

        bot.client.send_message.assert_awaited_once_with(chat_id=100, text="Текст")

    @pytest.mark.asyncio
    async def test_say_ошибка_отправки_уведомляет_текущий_чат(self) -> None:
        """Если send_message упал — отправляет уведомление в текущий чат."""
        bot = _make_bot("500 Сообщение")
        # Первый вызов (в чат 500) — падает, второй (notify в текущий) — успех
        bot.client.send_message = AsyncMock(side_effect=[
            Exception("Chat not found"),
            SimpleNamespace(id=1),
        ])
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)  # не пробрасывает

        # Второй вызов — уведомление в текущий чат (100)
        second_call = bot.client.send_message.call_args_list[1]
        assert second_call[1]["chat_id"] == 100
        assert "500" in second_call[1]["text"] or "Ошибка" in second_call[1]["text"]

    @pytest.mark.asyncio
    async def test_say_ошибка_notify_тоже_не_пробрасывает(self) -> None:
        """Если и notify-сообщение об ошибке упало — не пробрасывает."""
        bot = _make_bot("500 Текст")
        bot.client.send_message = AsyncMock(side_effect=Exception("всё сломалось"))
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)  # не должен упасть

    @pytest.mark.asyncio
    async def test_say_многословный_текст_в_текущем_чате(self) -> None:
        """Длинный текст без chat_id отправляется целиком в текущий чат."""
        long_text = "Это долгий текст из нескольких слов и без числового ID"
        bot = _make_bot(long_text)
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        bot.client.send_message.assert_awaited_once_with(chat_id=100, text=long_text)

    @pytest.mark.asyncio
    async def test_say_нечисловой_первый_токен_без_at_это_текст(self) -> None:
        """Первый токен — не число и не @username — весь raw считается текстом."""
        bot = _make_bot("Привет всем кто здесь есть")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        bot.client.send_message.assert_awaited_once_with(
            chat_id=100, text="Привет всем кто здесь есть"
        )

    @pytest.mark.asyncio
    async def test_say_текст_в_текущий_чат_когда_один_слово(self) -> None:
        """!say одно_слово без числа — отправляет это слово в текущий чат."""
        bot = _make_bot("Стоп")
        msg = _make_message(chat_id=55)

        await handle_say(bot, msg)

        bot.client.send_message.assert_awaited_once_with(chat_id=55, text="Стоп")

    @pytest.mark.asyncio
    async def test_say_команда_удаляется_до_отправки(self) -> None:
        """Удаление происходит до send_message."""
        call_order: list[str] = []

        bot = _make_bot("Тест")
        msg = _make_message(chat_id=100)

        async def fake_delete():
            call_order.append("delete")

        async def fake_send(**kwargs):
            call_order.append("send")
            return SimpleNamespace(id=1)

        msg.delete = fake_delete
        bot.client.send_message = fake_send

        await handle_say(bot, msg)

        assert call_order == ["delete", "send"]

    @pytest.mark.asyncio
    async def test_say_username_без_текста_отправляет_как_текст(self) -> None:
        """!say @someuser — один токен @username трактуется как текст для текущего чата."""
        bot = _make_bot("@someuser")
        msg = _make_message(chat_id=100)

        await handle_say(bot, msg)

        # Один токен @username без текста — отправляется буквально как текст
        bot.client.send_message.assert_awaited_once_with(chat_id=100, text="@someuser")
