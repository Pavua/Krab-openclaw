# -*- coding: utf-8 -*-
"""
Тесты команд управления сообщениями: !del, !purge, !autodel.

Покрываем:
- !del N — удаляет N сообщений бота из истории чата
- !del без аргумента — удаляет 1 сообщение
- !del с неверным аргументом — UserInputError
- !del N > 100 — UserInputError
- !del не-owner — UserInputError
- !purge — удаляет все сообщения бота за 1 час
- !purge не-owner — UserInputError
- !purge пустая история — тихо выходит
- !purge > 100 сообщений — разбивает на пачки
- !autodel <N> — включает автоудаление
- !autodel 0 — выключает
- !autodel status — показывает текущее состояние
- !autodel без аргумента — показывает статус
- !autodel отрицательное — UserInputError
- !autodel не-owner — UserInputError
- get_autodel_delay — вспомогательная функция
- schedule_autodel — планирует asyncio task
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _AUTODEL_STATE_KEY,
    _set_autodel_delay,
    get_autodel_delay,
    handle_autodel,
    handle_del,
    handle_purge,
    schedule_autodel,
)


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_bot(
    args: str = "",
    *,
    access_level: AccessLevel = AccessLevel.OWNER,
    bot_id: int = 999,
) -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""
    bot = SimpleNamespace(
        me=SimpleNamespace(id=bot_id),
        client=SimpleNamespace(
            delete_messages=AsyncMock(return_value=True),
            get_chat_history=None,  # переопределяется в тестах
        ),
        _get_command_args=lambda _: args,
        _get_access_profile=lambda user: AccessProfile(level=access_level, source="test"),
        _runtime_state={},
    )
    return bot


def _make_message(*, chat_id: int = 100, from_user_id: int = 1) -> SimpleNamespace:
    """Минимальный mock pyrogram.Message."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=from_user_id),
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(),
        delete=AsyncMock(),
    )


def _make_history_msg(msg_id: int, user_id: int, ts: float | None = None) -> SimpleNamespace:
    """Mock сообщения в истории чата."""
    date = SimpleNamespace(timestamp=lambda: ts or time.time())
    return SimpleNamespace(
        id=msg_id,
        from_user=SimpleNamespace(id=user_id),
        date=date,
    )


async def _async_gen(items):
    """Вспомогательный async generator из списка."""
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# !del
# ---------------------------------------------------------------------------

class TestHandleDel:
    @pytest.mark.asyncio
    async def test_del_default_удаляет_одно_сообщение(self) -> None:
        """!del без аргумента — удаляет 1 сообщение бота."""
        bot = _make_bot("")
        bot_msg = _make_history_msg(10, bot.me.id)
        other_msg = _make_history_msg(11, 777)
        bot.client.get_chat_history = MagicMock(
            return_value=_async_gen([bot_msg, other_msg])
        )
        msg = _make_message(chat_id=100)

        await handle_del(bot, msg)

        msg.delete.assert_awaited_once()
        bot.client.delete_messages.assert_awaited_once_with(100, message_ids=[10])

    @pytest.mark.asyncio
    async def test_del_n_удаляет_n_сообщений(self) -> None:
        """!del 3 — удаляет ровно 3 сообщения бота."""
        bot = _make_bot("3")
        history = [_make_history_msg(i, bot.me.id) for i in range(10, 15)]
        history.insert(2, _make_history_msg(99, 777))  # чужое — пропускается
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))
        msg = _make_message()

        await handle_del(bot, msg)

        call_ids = bot.client.delete_messages.call_args[1]["message_ids"]
        assert len(call_ids) == 3

    @pytest.mark.asyncio
    async def test_del_не_owner_вызывает_ошибку(self) -> None:
        """!del от не-owner — UserInputError."""
        bot = _make_bot("1", access_level=AccessLevel.PARTIAL)
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_del(bot, msg)

    @pytest.mark.asyncio
    async def test_del_неверный_аргумент(self) -> None:
        """!del abc — UserInputError."""
        bot = _make_bot("abc")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_del(bot, msg)

    @pytest.mark.asyncio
    async def test_del_n_больше_100(self) -> None:
        """!del 101 — UserInputError."""
        bot = _make_bot("101")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_del(bot, msg)

    @pytest.mark.asyncio
    async def test_del_n_ноль(self) -> None:
        """!del 0 — UserInputError (n < 1)."""
        bot = _make_bot("0")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_del(bot, msg)

    @pytest.mark.asyncio
    async def test_del_пустая_история_тихий_выход(self) -> None:
        """!del когда нет сообщений бота — ничего не удаляет, не падает."""
        bot = _make_bot("5")
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([]))
        msg = _make_message()

        await handle_del(bot, msg)  # не должен упасть

        bot.client.delete_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_del_api_error_logируется(self) -> None:
        """!del когда delete_messages бросает исключение — не пробрасывает."""
        bot = _make_bot("1")
        bot_msg = _make_history_msg(42, bot.me.id)
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([bot_msg]))
        bot.client.delete_messages = AsyncMock(side_effect=Exception("Flood wait"))
        msg = _make_message()

        await handle_del(bot, msg)  # не должен упасть

    @pytest.mark.asyncio
    async def test_del_command_itself_deleted(self) -> None:
        """!del удаляет само сообщение-команду."""
        bot = _make_bot("1")
        bot_msg = _make_history_msg(5, bot.me.id)
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([bot_msg]))
        msg = _make_message()

        await handle_del(bot, msg)

        msg.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# !purge
# ---------------------------------------------------------------------------

class TestHandlePurge:
    @pytest.mark.asyncio
    async def test_purge_удаляет_сообщения_за_час(self) -> None:
        """!purge — удаляет сообщения бота за последний час."""
        bot = _make_bot()
        now = time.time()
        history = [
            _make_history_msg(1, bot.me.id, now - 100),
            _make_history_msg(2, 777, now - 200),
            _make_history_msg(3, bot.me.id, now - 1800),
            _make_history_msg(4, bot.me.id, now - 3700),  # старше 1 часа — не берём
        ]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))
        msg = _make_message(chat_id=200)

        await handle_purge(bot, msg)

        # Должны удалить 3 сообщения (в пределах часа, от бота)
        call_ids = bot.client.delete_messages.call_args[1]["message_ids"]
        assert set(call_ids) == {1, 3}

    @pytest.mark.asyncio
    async def test_purge_не_owner(self) -> None:
        """!purge от не-owner — UserInputError."""
        bot = _make_bot(access_level=AccessLevel.PARTIAL)
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_purge(bot, msg)

    @pytest.mark.asyncio
    async def test_purge_пустая_история(self) -> None:
        """!purge — нет сообщений бота, ничего не удаляет, не падает."""
        bot = _make_bot()
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([]))
        msg = _make_message()

        await handle_purge(bot, msg)

        bot.client.delete_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_purge_разбивает_на_пачки(self) -> None:
        """!purge — более 100 сообщений бьётся по 100."""
        bot = _make_bot()
        now = time.time()
        history = [_make_history_msg(i, bot.me.id, now - 10) for i in range(150)]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))
        msg = _make_message()

        await handle_purge(bot, msg)

        assert bot.client.delete_messages.await_count == 2  # 100 + 50
        first_call = bot.client.delete_messages.call_args_list[0][1]["message_ids"]
        second_call = bot.client.delete_messages.call_args_list[1][1]["message_ids"]
        assert len(first_call) == 100
        assert len(second_call) == 50

    @pytest.mark.asyncio
    async def test_purge_удаляет_команду(self) -> None:
        """!purge удаляет само сообщение с командой."""
        bot = _make_bot()
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([]))
        msg = _make_message()

        await handle_purge(bot, msg)

        msg.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_purge_chunk_error_не_пробрасывает(self) -> None:
        """!purge — ошибка в одной пачке не пробрасывается."""
        bot = _make_bot()
        now = time.time()
        history = [_make_history_msg(i, bot.me.id, now - 10) for i in range(5)]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))
        bot.client.delete_messages = AsyncMock(side_effect=Exception("flood"))
        msg = _make_message()

        await handle_purge(bot, msg)  # не падает


# ---------------------------------------------------------------------------
# !autodel
# ---------------------------------------------------------------------------

class TestHandleAutodel:
    @pytest.mark.asyncio
    async def test_autodel_включить(self) -> None:
        """!autodel 30 — включает автоудаление на 30 сек."""
        bot = _make_bot("30")
        msg = _make_message(chat_id=100)

        await handle_autodel(bot, msg)

        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "30" in reply_text
        assert "включено" in reply_text.lower()
        assert get_autodel_delay(bot, 100) == 30.0

    @pytest.mark.asyncio
    async def test_autodel_выключить(self) -> None:
        """!autodel 0 — выключает автоудаление."""
        bot = _make_bot("0")
        bot._runtime_state = {_AUTODEL_STATE_KEY: {"100": 60.0}}
        msg = _make_message(chat_id=100)

        await handle_autodel(bot, msg)

        msg.reply.assert_awaited_once()
        assert get_autodel_delay(bot, 100) is None

    @pytest.mark.asyncio
    async def test_autodel_status_включено(self) -> None:
        """!autodel status — показывает текущую задержку."""
        bot = _make_bot("status")
        bot._runtime_state = {_AUTODEL_STATE_KEY: {"100": 45.0}}
        msg = _make_message(chat_id=100)

        await handle_autodel(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "45" in reply_text
        assert "включено" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_autodel_status_выключено(self) -> None:
        """!autodel status — сообщает что выключено."""
        bot = _make_bot("status")
        msg = _make_message(chat_id=100)

        await handle_autodel(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "выключено" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_autodel_без_аргумента_показывает_статус(self) -> None:
        """!autodel без аргументов — эквивалент status."""
        bot = _make_bot("")
        msg = _make_message(chat_id=100)

        await handle_autodel(bot, msg)

        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_autodel_не_owner(self) -> None:
        """!autodel от не-owner — UserInputError."""
        bot = _make_bot("30", access_level=AccessLevel.PARTIAL)
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_autodel(bot, msg)

    @pytest.mark.asyncio
    async def test_autodel_неверный_аргумент(self) -> None:
        """!autodel xyz — UserInputError."""
        bot = _make_bot("xyz")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_autodel(bot, msg)

    @pytest.mark.asyncio
    async def test_autodel_отрицательное(self) -> None:
        """!autodel -5 — UserInputError."""
        bot = _make_bot("-5")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_autodel(bot, msg)

    @pytest.mark.asyncio
    async def test_autodel_float_значение(self) -> None:
        """!autodel 10.5 — принимает дробные секунды."""
        bot = _make_bot("10.5")
        msg = _make_message(chat_id=200)

        await handle_autodel(bot, msg)

        assert get_autodel_delay(bot, 200) == 10.5

    @pytest.mark.asyncio
    async def test_autodel_разные_чаты_независимы(self) -> None:
        """autodel настройки независимы для разных chat_id."""
        bot = _make_bot("60")
        msg1 = _make_message(chat_id=111)
        msg2 = _make_message(chat_id=222)

        await handle_autodel(bot, msg1)
        assert get_autodel_delay(bot, 111) == 60.0
        assert get_autodel_delay(bot, 222) is None

        bot2 = _make_bot("0")
        bot2._runtime_state = {_AUTODEL_STATE_KEY: {"111": 60.0, "222": 30.0}}
        msg3 = _make_message(chat_id=111)
        await handle_autodel(bot2, msg3)
        assert get_autodel_delay(bot2, 111) is None
        assert get_autodel_delay(bot2, 222) == 30.0


# ---------------------------------------------------------------------------
# get_autodel_delay — чистая функция
# ---------------------------------------------------------------------------

class TestGetAutodelDelay:
    def test_нет_runtime_state(self) -> None:
        """Нет _runtime_state — возвращает None."""
        bot = SimpleNamespace()
        assert get_autodel_delay(bot, 100) is None

    def test_пустой_state(self) -> None:
        """Пустой _runtime_state — None."""
        bot = SimpleNamespace(_runtime_state={})
        assert get_autodel_delay(bot, 100) is None

    def test_нет_настройки_для_чата(self) -> None:
        """Настройка есть для другого чата — None."""
        bot = SimpleNamespace(_runtime_state={_AUTODEL_STATE_KEY: {"999": 30.0}})
        assert get_autodel_delay(bot, 100) is None

    def test_возвращает_задержку(self) -> None:
        """Настройка есть — возвращает float."""
        bot = SimpleNamespace(_runtime_state={_AUTODEL_STATE_KEY: {"100": 45.0}})
        assert get_autodel_delay(bot, 100) == 45.0

    def test_нулевое_значение_возвращает_none(self) -> None:
        """Задержка 0 — считается выключенной."""
        bot = SimpleNamespace(_runtime_state={_AUTODEL_STATE_KEY: {"100": 0}})
        assert get_autodel_delay(bot, 100) is None


# ---------------------------------------------------------------------------
# _set_autodel_delay — чистая функция
# ---------------------------------------------------------------------------

class TestSetAutodelDelay:
    def test_сохраняет_задержку(self) -> None:
        """Устанавливает задержку для чата."""
        bot = SimpleNamespace(_runtime_state={})
        _set_autodel_delay(bot, 100, 30.0)
        assert get_autodel_delay(bot, 100) == 30.0

    def test_обнуление_удаляет_ключ(self) -> None:
        """Задержка 0 — удаляет ключ из словаря."""
        bot = SimpleNamespace(_runtime_state={_AUTODEL_STATE_KEY: {"100": 30.0}})
        _set_autodel_delay(bot, 100, 0)
        assert get_autodel_delay(bot, 100) is None

    def test_нет_runtime_state_создаёт_его(self) -> None:
        """Нет _runtime_state — создаёт атрибут."""
        bot = SimpleNamespace()
        _set_autodel_delay(bot, 100, 10.0)
        assert get_autodel_delay(bot, 100) == 10.0


# ---------------------------------------------------------------------------
# schedule_autodel — интеграционный тест
# ---------------------------------------------------------------------------

class TestScheduleAutodel:
    @pytest.mark.asyncio
    async def test_планирует_удаление(self) -> None:
        """schedule_autodel создаёт task, который удаляет сообщение."""
        client = SimpleNamespace(delete_messages=AsyncMock())
        # Используем очень маленькую задержку
        schedule_autodel(client, 100, 42, 0.01)
        await asyncio.sleep(0.05)
        client.delete_messages.assert_awaited_once_with(100, message_ids=[42])

    @pytest.mark.asyncio
    async def test_ошибка_удаления_не_пробрасывается(self) -> None:
        """schedule_autodel — ошибка в delete_messages не пробрасывается."""
        client = SimpleNamespace(delete_messages=AsyncMock(side_effect=Exception("forbidden")))
        schedule_autodel(client, 100, 42, 0.01)
        await asyncio.sleep(0.05)
        # Не должен бросить исключение
