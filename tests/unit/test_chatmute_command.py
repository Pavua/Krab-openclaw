# -*- coding: utf-8 -*-
"""
Тесты команды !chatmute из src/handlers/command_handlers.py.

Покрытие:
1.  !chatmute off  — отключает уведомления (mute навсегда)
2.  !chatmute мute — алиас off
3.  !chatmute выкл — русский алиас off
4.  !chatmute тихо — русский алиас off
5.  !chatmute on   — включает уведомления (unmute)
6.  !chatmute unmute — алиас on
7.  !chatmute вкл  — русский алиас on
8.  !chatmute громко — русский алиас on
9.  !chatmute status — показывает статус (muted навсегда)
10. !chatmute статус — русский алиас status (muted навсегда)
11. !chatmute status — показывает статус (включено)
12. !chatmute status — показывает статус (muted до конкретного времени)
13. !chatmute (без аргументов) — показывает справку
14. !chatmute <неизвестный аргумент> — показывает справку
15. !chatmute off — MTProto ошибка → UserInputError
16. !chatmute on  — MTProto ошибка → UserInputError
17. _MUTE_FOREVER_UNTIL — корректное значение int32 max
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _MUTE_FOREVER_UNTIL,
    handle_chatmute,
)


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(chat_id: int = 12345) -> MagicMock:
    """Создаёт мок userbot с async client и resolve_peer/invoke."""
    bot = MagicMock()
    bot.client = AsyncMock()
    bot.client.resolve_peer = AsyncMock(return_value=MagicMock())
    bot.client.invoke = AsyncMock(return_value=MagicMock(mute_until=0))
    bot._get_command_args = MagicMock(return_value="")
    return bot


def _make_message(text: str, chat_id: int = 12345) -> MagicMock:
    """Создаёт мок Telegram-сообщения."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    return msg


def _bot_with_args(args: str) -> MagicMock:
    """Создаёт бот с заданными аргументами команды."""
    bot = _make_bot()
    bot._get_command_args = MagicMock(return_value=args)
    return bot


# ---------------------------------------------------------------------------
# Тесты !chatmute off — отключение уведомлений
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatmute_off_вызывает_UpdateNotifySettings():
    """!chatmute off вызывает MTProto UpdateNotifySettings с mute_until=MAX."""
    from pyrogram import raw

    bot = _bot_with_args("off")
    msg = _make_message("!chatmute off")

    await handle_chatmute(bot, msg)

    bot.client.invoke.assert_called_once()
    call_args = bot.client.invoke.call_args[0][0]
    # Тип вызова — UpdateNotifySettings
    assert isinstance(call_args, raw.functions.account.UpdateNotifySettings)
    # Настройки содержат mute_until = MAX
    assert call_args.settings.mute_until == _MUTE_FOREVER_UNTIL
    assert call_args.settings.silent is True


@pytest.mark.asyncio
async def test_chatmute_off_отвечает_про_отключение():
    """!chatmute off отвечает сообщением об отключении."""
    bot = _bot_with_args("off")
    msg = _make_message("!chatmute off")

    await handle_chatmute(bot, msg)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "отключены" in reply_text
    assert "chatmute on" in reply_text


@pytest.mark.asyncio
async def test_chatmute_mute_алиас():
    """!chatmute mute — алиас off, тоже mute'ит."""
    from pyrogram import raw

    bot = _bot_with_args("mute")
    msg = _make_message("!chatmute mute")

    await handle_chatmute(bot, msg)

    call_args = bot.client.invoke.call_args[0][0]
    assert isinstance(call_args, raw.functions.account.UpdateNotifySettings)
    assert call_args.settings.mute_until == _MUTE_FOREVER_UNTIL


@pytest.mark.asyncio
async def test_chatmute_выкл_алиас():
    """!chatmute выкл — русский алиас off."""
    from pyrogram import raw

    bot = _bot_with_args("выкл")
    msg = _make_message("!chatmute выкл")

    await handle_chatmute(bot, msg)

    call_args = bot.client.invoke.call_args[0][0]
    assert call_args.settings.mute_until == _MUTE_FOREVER_UNTIL


@pytest.mark.asyncio
async def test_chatmute_тихо_алиас():
    """!chatmute тихо — русский алиас off."""
    from pyrogram import raw

    bot = _bot_with_args("тихо")
    msg = _make_message("!chatmute тихо")

    await handle_chatmute(bot, msg)

    call_args = bot.client.invoke.call_args[0][0]
    assert call_args.settings.mute_until == _MUTE_FOREVER_UNTIL


# ---------------------------------------------------------------------------
# Тесты !chatmute on — включение уведомлений
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatmute_on_вызывает_UpdateNotifySettings_с_нулём():
    """!chatmute on вызывает MTProto UpdateNotifySettings с mute_until=0."""
    from pyrogram import raw

    bot = _bot_with_args("on")
    msg = _make_message("!chatmute on")

    await handle_chatmute(bot, msg)

    bot.client.invoke.assert_called_once()
    call_args = bot.client.invoke.call_args[0][0]
    assert isinstance(call_args, raw.functions.account.UpdateNotifySettings)
    assert call_args.settings.mute_until == 0
    assert call_args.settings.silent is False


@pytest.mark.asyncio
async def test_chatmute_on_отвечает_про_включение():
    """!chatmute on отвечает сообщением о включении."""
    bot = _bot_with_args("on")
    msg = _make_message("!chatmute on")

    await handle_chatmute(bot, msg)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "включены" in reply_text


@pytest.mark.asyncio
async def test_chatmute_unmute_алиас():
    """!chatmute unmute — алиас on."""
    from pyrogram import raw

    bot = _bot_with_args("unmute")
    msg = _make_message("!chatmute unmute")

    await handle_chatmute(bot, msg)

    call_args = bot.client.invoke.call_args[0][0]
    assert call_args.settings.mute_until == 0


@pytest.mark.asyncio
async def test_chatmute_вкл_алиас():
    """!chatmute вкл — русский алиас on."""
    from pyrogram import raw

    bot = _bot_with_args("вкл")
    msg = _make_message("!chatmute вкл")

    await handle_chatmute(bot, msg)

    call_args = bot.client.invoke.call_args[0][0]
    assert call_args.settings.mute_until == 0


@pytest.mark.asyncio
async def test_chatmute_громко_алиас():
    """!chatmute громко — русский алиас on."""
    from pyrogram import raw

    bot = _bot_with_args("громко")
    msg = _make_message("!chatmute громко")

    await handle_chatmute(bot, msg)

    call_args = bot.client.invoke.call_args[0][0]
    assert call_args.settings.mute_until == 0


# ---------------------------------------------------------------------------
# Тесты !chatmute status — просмотр статуса
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatmute_status_заглушён_навсегда():
    """!chatmute status показывает 'заглушён навсегда' когда mute_until=MAX."""
    # GetNotifySettings возвращает mute_until = MAX
    bot = _bot_with_args("status")
    bot.client.invoke = AsyncMock(
        return_value=MagicMock(mute_until=_MUTE_FOREVER_UNTIL)
    )
    msg = _make_message("!chatmute status")

    await handle_chatmute(bot, msg)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "Заглушён" in reply_text
    assert "навсегда" in reply_text


@pytest.mark.asyncio
async def test_chatmute_status_уведомления_включены():
    """!chatmute status показывает 'включены' когда mute_until=0."""
    bot = _bot_with_args("status")
    bot.client.invoke = AsyncMock(return_value=MagicMock(mute_until=0))
    msg = _make_message("!chatmute status")

    await handle_chatmute(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "включены" in reply_text.lower() or "Уведомления включены" in reply_text


@pytest.mark.asyncio
async def test_chatmute_status_заглушён_до_времени():
    """!chatmute status показывает время окончания mute (не MAX)."""
    future_ts = int(time.time()) + 3600  # через час
    bot = _bot_with_args("status")
    bot.client.invoke = AsyncMock(return_value=MagicMock(mute_until=future_ts))
    msg = _make_message("!chatmute status")

    await handle_chatmute(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "Заглушён" in reply_text
    # Дата должна быть в ответе (формат DD.MM.YYYY)
    assert "." in reply_text


@pytest.mark.asyncio
async def test_chatmute_status_mute_истёк():
    """!chatmute status — mute_until в прошлом → уведомления включены."""
    past_ts = int(time.time()) - 3600  # час назад
    bot = _bot_with_args("status")
    bot.client.invoke = AsyncMock(return_value=MagicMock(mute_until=past_ts))
    msg = _make_message("!chatmute status")

    await handle_chatmute(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "включены" in reply_text.lower() or "Уведомления включены" in reply_text


@pytest.mark.asyncio
async def test_chatmute_статус_русский_алиас():
    """!chatmute статус — русский алиас для status."""
    bot = _bot_with_args("статус")
    bot.client.invoke = AsyncMock(return_value=MagicMock(mute_until=0))
    msg = _make_message("!chatmute статус")

    await handle_chatmute(bot, msg)

    msg.reply.assert_called_once()
    # Убеждаемся что не попали в ветку справки
    reply_text = msg.reply.call_args[0][0]
    assert "chatmute off" in reply_text or "chatmute on" in reply_text


# ---------------------------------------------------------------------------
# Тесты !chatmute (справка)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatmute_пустые_аргументы_справка():
    """!chatmute без аргументов показывает справку."""
    bot = _bot_with_args("")
    msg = _make_message("!chatmute")

    await handle_chatmute(bot, msg)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "chatmute off" in reply_text
    assert "chatmute on" in reply_text
    assert "chatmute status" in reply_text


@pytest.mark.asyncio
async def test_chatmute_неизвестный_аргумент_справка():
    """!chatmute <что-то непонятное> показывает справку."""
    bot = _bot_with_args("неизвестно")
    msg = _make_message("!chatmute неизвестно")

    await handle_chatmute(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "chatmute off" in reply_text
    assert "chatmute on" in reply_text


# ---------------------------------------------------------------------------
# Тесты обработки ошибок
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatmute_off_mtproto_ошибка_UserInputError():
    """!chatmute off при MTProto-ошибке поднимает UserInputError."""
    bot = _bot_with_args("off")
    bot.client.invoke = AsyncMock(side_effect=Exception("FLOOD_WAIT"))
    msg = _make_message("!chatmute off")

    with pytest.raises(UserInputError) as exc_info:
        await handle_chatmute(bot, msg)

    assert "Не удалось отключить" in str(exc_info.value.user_message)
    assert "FLOOD_WAIT" in str(exc_info.value.user_message)


@pytest.mark.asyncio
async def test_chatmute_on_mtproto_ошибка_UserInputError():
    """!chatmute on при MTProto-ошибке поднимает UserInputError."""
    bot = _bot_with_args("on")
    bot.client.invoke = AsyncMock(side_effect=Exception("PEER_ID_INVALID"))
    msg = _make_message("!chatmute on")

    with pytest.raises(UserInputError) as exc_info:
        await handle_chatmute(bot, msg)

    assert "Не удалось включить" in str(exc_info.value.user_message)


@pytest.mark.asyncio
async def test_chatmute_off_resolve_peer_ошибка_UserInputError():
    """!chatmute off при ошибке resolve_peer поднимает UserInputError."""
    bot = _bot_with_args("off")
    bot.client.resolve_peer = AsyncMock(side_effect=Exception("PEER_NOT_FOUND"))
    msg = _make_message("!chatmute off")

    with pytest.raises(UserInputError):
        await handle_chatmute(bot, msg)


@pytest.mark.asyncio
async def test_chatmute_status_GetNotifySettings_ошибка_молчит():
    """!chatmute status при ошибке GetNotifySettings не падает, считает mute=0."""
    bot = _bot_with_args("status")
    # GetNotifySettings падает → fallback mute_until=0 → «включены»
    bot.client.invoke = AsyncMock(side_effect=Exception("NETWORK_ERROR"))
    msg = _make_message("!chatmute status")

    # Не должна упасть
    await handle_chatmute(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "включены" in reply_text.lower() or "Уведомления включены" in reply_text


# ---------------------------------------------------------------------------
# Тесты константы _MUTE_FOREVER_UNTIL
# ---------------------------------------------------------------------------


def test_MUTE_FOREVER_UNTIL_это_max_int32():
    """_MUTE_FOREVER_UNTIL == 2147483647 (max int32)."""
    assert _MUTE_FOREVER_UNTIL == 2_147_483_647


def test_MUTE_FOREVER_UNTIL_является_int():
    """_MUTE_FOREVER_UNTIL имеет тип int."""
    assert isinstance(_MUTE_FOREVER_UNTIL, int)


def test_MUTE_FOREVER_UNTIL_больше_текущего_времени():
    """_MUTE_FOREVER_UNTIL всегда в будущем (до ~2038)."""
    assert _MUTE_FOREVER_UNTIL > int(time.time())


# ---------------------------------------------------------------------------
# Тесты использования chat_id из сообщения
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatmute_использует_chat_id_из_message():
    """handle_chatmute передаёт правильный chat_id в resolve_peer."""
    bot = _bot_with_args("off")
    msg = _make_message("!chatmute off", chat_id=999888777)

    await handle_chatmute(bot, msg)

    bot.client.resolve_peer.assert_called_with(999888777)


@pytest.mark.asyncio
async def test_chatmute_on_использует_chat_id_из_message():
    """!chatmute on передаёт правильный chat_id в resolve_peer."""
    bot = _bot_with_args("on")
    msg = _make_message("!chatmute on", chat_id=111222333)

    await handle_chatmute(bot, msg)

    bot.client.resolve_peer.assert_called_with(111222333)
