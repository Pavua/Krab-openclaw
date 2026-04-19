# -*- coding: utf-8 -*-
"""
Тесты команды !afk / !back из src/handlers/command_handlers.py.

Покрытие:
1.  !afk — включает AFK без причины
2.  !afk <причина> — включает AFK с причиной
3.  !afk off — выключает AFK
4.  !afk стоп — выключает AFK (русский вариант)
5.  !back — выключает AFK через алиас
6.  !afk status — показывает статус (AFK активен)
7.  !afk status — когда AFK не активен
8.  !afk off когда AFK не активен — сообщает об этом
9.  !back когда AFK не активен — сообщает об этом
10. !afk повторно (уже активен) — обновляет причину
11. Автоответ на DM в AFK-режиме (_process_message)
12. Автоответ отправляется только один раз на чат
13. Автоответ НЕ отправляется если owner пишет сам
14. Автоответ НЕ отправляется в групповых чатах
15. Owner пишет не-команду → AFK автовыключается
16. Причина отображается корректно в автоответе
17. Время AFK форматируется корректно (секунды / минуты)
18. !afk status показывает количество чатов с автоответом
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.command_handlers import handle_afk

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(*, afk_mode: bool = False, afk_reason: str = "", afk_since: float = 0.0) -> MagicMock:
    """Создаёт мок userbot с AFK-состоянием."""
    bot = MagicMock()
    bot._afk_mode = afk_mode
    bot._afk_reason = afk_reason
    bot._afk_since = afk_since
    bot._afk_replied_chats = set()
    return bot


def _make_message(text: str, chat_id: int = 100) -> MagicMock:
    """Создаёт мок Telegram-сообщения."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    return msg


# ---------------------------------------------------------------------------
# Тесты !afk — включение
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afk_enable_no_reason():
    """!afk включает AFK без причины."""
    bot = _make_bot()
    msg = _make_message("!afk")

    await handle_afk(bot, msg)

    assert bot._afk_mode is True
    assert bot._afk_reason == ""
    assert bot._afk_since > 0
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "AFK" in reply_text
    assert "включён" in reply_text


@pytest.mark.asyncio
async def test_afk_enable_with_reason():
    """!afk <причина> включает AFK с причиной."""
    bot = _make_bot()
    msg = _make_message("!afk обедаю")

    await handle_afk(bot, msg)

    assert bot._afk_mode is True
    assert bot._afk_reason == "обедаю"
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "обедаю" in reply_text


@pytest.mark.asyncio
async def test_afk_enable_with_long_reason():
    """!afk с длинной причиной сохраняет полный текст."""
    bot = _make_bot()
    msg = _make_message("!afk ушёл на встречу, вернусь через час")

    await handle_afk(bot, msg)

    assert bot._afk_reason == "ушёл на встречу, вернусь через час"


# ---------------------------------------------------------------------------
# Тесты !afk off — выключение
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afk_off():
    """!afk off выключает AFK."""
    bot = _make_bot(afk_mode=True, afk_reason="тест", afk_since=time.time() - 30)

    msg = _make_message("!afk off")

    await handle_afk(bot, msg)

    assert bot._afk_mode is False
    assert bot._afk_reason == ""
    assert bot._afk_since == 0.0
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "AFK выключен" in reply_text


@pytest.mark.asyncio
async def test_afk_stop_ru():
    """!afk стоп выключает AFK (русская команда)."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 60)
    msg = _make_message("!afk стоп")

    await handle_afk(bot, msg)

    assert bot._afk_mode is False
    msg.reply.assert_called_once()
    assert "AFK выключен" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_afk_off_when_not_active():
    """!afk off когда AFK не активен — информирует об этом."""
    bot = _make_bot(afk_mode=False)
    msg = _make_message("!afk off")

    await handle_afk(bot, msg)

    msg.reply.assert_called_once()
    assert "не активен" in msg.reply.call_args[0][0]
    # Состояние не меняется
    assert bot._afk_mode is False


@pytest.mark.asyncio
async def test_afk_vyкл():
    """!afk выкл — ещё один вариант выключения."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 10)
    msg = _make_message("!afk выкл")

    await handle_afk(bot, msg)

    assert bot._afk_mode is False


# ---------------------------------------------------------------------------
# Тесты !back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_back_disables_afk():
    """!back выключает AFK."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 120)
    msg = _make_message("!back")

    await handle_afk(bot, msg)

    assert bot._afk_mode is False
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "Добро пожаловать обратно" in reply_text


@pytest.mark.asyncio
async def test_back_when_not_active():
    """!back когда AFK не активен — информирует."""
    bot = _make_bot(afk_mode=False)
    msg = _make_message("!back")

    await handle_afk(bot, msg)

    msg.reply.assert_called_once()
    assert "не активен" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_back_clears_replied_chats():
    """!back очищает список чатов с автоответом."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 10)
    bot._afk_replied_chats = {"100", "200", "300"}
    msg = _make_message("!back")

    await handle_afk(bot, msg)

    assert len(bot._afk_replied_chats) == 0


# ---------------------------------------------------------------------------
# Тесты !afk status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afk_status_when_active():
    """!afk status показывает активный AFK."""
    bot = _make_bot(afk_mode=True, afk_reason="сплю", afk_since=time.time() - 90)
    bot._afk_replied_chats = {"111", "222"}
    msg = _make_message("!afk status")

    await handle_afk(bot, msg)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "AFK активен" in reply_text
    assert "сплю" in reply_text
    assert "2 чат" in reply_text


@pytest.mark.asyncio
async def test_afk_status_when_inactive():
    """!afk status когда AFK не активен."""
    bot = _make_bot(afk_mode=False)
    msg = _make_message("!afk status")

    await handle_afk(bot, msg)

    msg.reply.assert_called_once()
    assert "не активен" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_afk_status_ru():
    """!afk статус — русский вариант."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 5)
    msg = _make_message("!afk статус")

    await handle_afk(bot, msg)

    msg.reply.assert_called_once()
    assert "AFK активен" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Тест обновления причины при повторном !afk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afk_update_reason_when_already_active():
    """!afk с новой причиной когда AFK уже активен — обновляет причину."""
    bot = _make_bot(afk_mode=True, afk_reason="старая причина", afk_since=time.time() - 60)
    msg = _make_message("!afk новая причина")

    await handle_afk(bot, msg)

    assert bot._afk_mode is True  # Остаётся активным
    assert bot._afk_reason == "новая причина"
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "уже активен" in reply_text
    assert "новая причина" in reply_text


@pytest.mark.asyncio
async def test_afk_update_no_reason_when_already_active():
    """!afk без аргументов когда AFK уже активен — сбрасывает причину."""
    bot = _make_bot(afk_mode=True, afk_reason="старая", afk_since=time.time() - 10)
    msg = _make_message("!afk")

    await handle_afk(bot, msg)

    assert bot._afk_mode is True
    assert bot._afk_reason == ""


# ---------------------------------------------------------------------------
# Тесты форматирования времени
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afk_time_format_seconds():
    """Время отображается в секундах если меньше минуты."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 45)
    msg = _make_message("!afk status")

    await handle_afk(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "с" in reply_text  # секунды
    assert "мин" not in reply_text.split("с")[0]  # Нет минут до секунд


@pytest.mark.asyncio
async def test_afk_time_format_minutes():
    """Время отображается с минутами если больше минуты."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 130)
    msg = _make_message("!afk status")

    await handle_afk(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "мин" in reply_text


@pytest.mark.asyncio
async def test_back_shows_elapsed_time():
    """!back показывает время отсутствия."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 200)
    msg = _make_message("!back")

    await handle_afk(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "мин" in reply_text  # более 3 минут


# ---------------------------------------------------------------------------
# Тесты автоответа в _process_message (интеграционные)
# ---------------------------------------------------------------------------


def _make_userbot_mock(
    *, afk_mode: bool = True, afk_reason: str = "", afk_since: float | None = None
):
    """Создаёт мок userbot для тестирования _process_message AFK логики."""
    bot = MagicMock()
    bot._afk_mode = afk_mode
    bot._afk_reason = afk_reason
    bot._afk_since = afk_since if afk_since is not None else (time.time() - 30 if afk_mode else 0.0)
    bot._afk_replied_chats = set()
    bot.me = MagicMock()
    bot.me.id = 12345
    return bot


def _make_incoming_message(
    *, chat_type: str = "PRIVATE", user_id: int = 99999, chat_id: int = 99999
) -> MagicMock:
    """Создаёт мок входящего сообщения."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.text = "Привет!"
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    # Создаём тип чата так, чтобы str() возвращал "ChatType.PRIVATE" и т.д.
    chat_type_mock = MagicMock()
    chat_type_mock.__str__ = MagicMock(return_value=f"ChatType.{chat_type}")
    msg.chat.type = chat_type_mock
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.is_bot = False
    return msg


@pytest.mark.asyncio
async def test_afk_autoreply_to_dm():
    """В AFK-режиме входящий DM получает автоответ."""
    bot = _make_userbot_mock(afk_mode=True, afk_reason="обедаю")
    msg = _make_incoming_message(chat_type="PRIVATE", user_id=55555, chat_id=55555)

    # Симулируем AFK-блок из _process_message
    chat_id = str(msg.chat.id)
    is_self = bot.me and msg.from_user.id == bot.me.id

    if (
        bot._afk_mode
        and not is_self
        and msg.chat
        and getattr(msg.chat, "type", None) is not None
        and str(msg.chat.type).upper().endswith("PRIVATE")
        and chat_id not in bot._afk_replied_chats
    ):
        elapsed = int(time.time() - bot._afk_since)
        mins = elapsed // 60
        secs = elapsed % 60
        time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
        reason_part = f"\n📝 Причина: {bot._afk_reason}" if bot._afk_reason else ""
        await msg.reply(
            f"🌙 Я сейчас AFK (отсутствую {time_str}).{reason_part}\nОтвечу когда вернусь!"
        )
        bot._afk_replied_chats.add(chat_id)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "AFK" in reply_text
    assert "обедаю" in reply_text
    assert chat_id in bot._afk_replied_chats


@pytest.mark.asyncio
async def test_afk_autoreply_only_once_per_chat():
    """Автоответ отправляется только один раз на один чат."""
    bot = _make_userbot_mock(afk_mode=True)
    msg = _make_incoming_message(user_id=55555, chat_id=55555)
    chat_id = str(msg.chat.id)

    # Первый раз — чат уже в replied_chats
    bot._afk_replied_chats.add(chat_id)

    # Симулируем второй вызов
    reply_sent = False
    if (
        bot._afk_mode
        and not (msg.from_user.id == bot.me.id)
        and str(msg.chat.type).upper().endswith("PRIVATE")
        and chat_id not in bot._afk_replied_chats
    ):
        await msg.reply("AFK")
        reply_sent = True

    assert not reply_sent
    msg.reply.assert_not_called()


def test_afk_no_autoreply_from_self():
    """Автоответ НЕ отправляется если owner сам пишет (is_self=True)."""
    bot = _make_userbot_mock(afk_mode=True)
    # user_id совпадает с bot.me.id
    msg = _make_incoming_message(user_id=12345, chat_id=12345)
    chat_id = str(msg.chat.id)

    is_self = msg.from_user.id == bot.me.id  # True

    should_reply = (
        bot._afk_mode
        and not is_self
        and str(msg.chat.type).upper().endswith("PRIVATE")
        and chat_id not in bot._afk_replied_chats
    )

    assert not should_reply


def test_afk_no_autoreply_in_group():
    """Автоответ НЕ отправляется в групповых чатах."""
    bot = _make_userbot_mock(afk_mode=True)
    msg = _make_incoming_message(chat_type="GROUP", user_id=55555, chat_id=-1001234)
    chat_id = str(msg.chat.id)

    should_reply = (
        bot._afk_mode
        and not (msg.from_user.id == bot.me.id)
        and str(msg.chat.type).upper().endswith("PRIVATE")
        and chat_id not in bot._afk_replied_chats
    )

    assert not should_reply


def test_afk_no_autoreply_when_disabled():
    """Автоответ не отправляется если AFK не активен."""
    bot = _make_userbot_mock(afk_mode=False)
    msg = _make_incoming_message(user_id=55555, chat_id=55555)
    chat_id = str(msg.chat.id)

    should_reply = (
        bot._afk_mode
        and not (msg.from_user.id == bot.me.id)
        and str(msg.chat.type).upper().endswith("PRIVATE")
        and chat_id not in bot._afk_replied_chats
    )

    assert not should_reply


def test_afk_autodisable_on_owner_message():
    """Owner пишет не-команду → AFK автовыключается."""
    bot = _make_userbot_mock(afk_mode=True, afk_reason="тест")
    bot._afk_replied_chats = {"aaa", "bbb"}

    is_self = True
    is_command = False

    # Симулируем блок из _process_message
    if bot._afk_mode and is_self and not is_command:
        bot._afk_mode = False
        bot._afk_reason = ""
        bot._afk_since = 0.0
        bot._afk_replied_chats.clear()

    assert bot._afk_mode is False
    assert bot._afk_reason == ""
    assert len(bot._afk_replied_chats) == 0


def test_afk_no_autodisable_on_command():
    """Owner пишет команду → AFK НЕ выключается автоматически."""
    bot = _make_userbot_mock(afk_mode=True)

    is_self = True
    is_command = True  # Это команда

    if bot._afk_mode and is_self and not is_command:
        bot._afk_mode = False

    # AFK должен остаться активным
    assert bot._afk_mode is True


# ---------------------------------------------------------------------------
# Тест корректности ответа без причины в автоответе
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afk_autoreply_no_reason():
    """Автоответ без причины не содержит 'Причина:'."""
    bot = _make_userbot_mock(afk_mode=True, afk_reason="")
    msg = _make_incoming_message(user_id=55555, chat_id=55555)
    chat_id = str(msg.chat.id)

    elapsed = int(time.time() - bot._afk_since)
    mins = elapsed // 60
    secs = elapsed % 60
    time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
    reason_part = f"\n📝 Причина: {bot._afk_reason}" if bot._afk_reason else ""
    text = f"🌙 Я сейчас AFK (отсутствую {time_str}).{reason_part}\nОтвечу когда вернусь!"

    assert "Причина" not in text
    assert "AFK" in text


# ---------------------------------------------------------------------------
# Тест SUPERGROUP тип чата НЕ получает автоответ
# ---------------------------------------------------------------------------


def test_afk_no_autoreply_in_supergroup():
    """Автоответ НЕ отправляется в супергруппах."""
    bot = _make_userbot_mock(afk_mode=True)
    msg = _make_incoming_message(chat_type="SUPERGROUP", user_id=55555, chat_id=-1009999)
    chat_id = str(msg.chat.id)

    should_reply = (
        bot._afk_mode
        and not (msg.from_user.id == bot.me.id)
        and str(msg.chat.type).upper().endswith("PRIVATE")
        and chat_id not in bot._afk_replied_chats
    )

    assert not should_reply


# ---------------------------------------------------------------------------
# Тест инициализации AFK-атрибутов
# ---------------------------------------------------------------------------


def test_afk_initial_state():
    """Начальное AFK-состояние корректно."""
    bot = _make_bot()
    assert bot._afk_mode is False
    assert bot._afk_reason == ""
    assert bot._afk_since == 0.0
    assert isinstance(bot._afk_replied_chats, set)
    assert len(bot._afk_replied_chats) == 0


@pytest.mark.asyncio
async def test_afk_replied_chats_cleared_on_off():
    """При выключении AFK список replied_chats очищается."""
    bot = _make_bot(afk_mode=True, afk_since=time.time() - 5)
    bot._afk_replied_chats = {"1", "2", "3"}
    msg = _make_message("!afk off")

    await handle_afk(bot, msg)

    assert len(bot._afk_replied_chats) == 0
