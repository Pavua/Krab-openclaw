# -*- coding: utf-8 -*-
"""
Тесты команды !blocked из src/handlers/command_handlers.py.

Покрытие:
1.  !blocked list — пустой список
2.  !blocked list — несколько пользователей
3.  !blocked list — пользователь без username
4.  !blocked list — пользователь с username
5.  !blocked list — ошибка get_blocked → UserInputError
6.  !blocked (без аргументов) — вызывает list
7.  !blocked add в reply → блокирует автора
8.  !blocked add @username → блокирует по username
9.  !blocked add 123456 → блокирует по числовому ID
10. !blocked add без аргументов и без reply → UserInputError
11. !blocked add в reply без from_user и без sender_chat → UserInputError
12. !blocked add — ошибка block_user → UserInputError
13. !blocked ban — алиас add
14. !blocked block — алиас add
15. !blocked remove @username — разблокирует
16. !blocked remove 123456 — разблокирует по ID
17. !blocked remove без аргумента → UserInputError
18. !blocked remove — ошибка unblock_user → UserInputError
19. !blocked unblock — алиас remove
20. !blocked del — алиас remove
21. !blocked rm — алиас remove
22. !blocked список — русский алиас list
23. !blocked заблок @username — русский алиас add
24. !blocked разблок @username — русский алиас remove
25. !blocked <неизвестное> — справка
26. !blocked add в reply через sender_chat → блокирует chat_id
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_blocked


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_user(
    user_id: int = 100,
    first_name: str = "Иван",
    last_name: str | None = None,
    username: str | None = None,
) -> MagicMock:
    """Создаёт мок Pyrogram User."""
    u = MagicMock()
    u.id = user_id
    u.first_name = first_name
    u.last_name = last_name
    u.username = username
    return u


async def _async_gen(*items):
    """Вспомогательный async-генератор для get_blocked."""
    for item in items:
        yield item


def _make_bot(blocked_users=(), args: str = "") -> MagicMock:
    """Создаёт мок userbot с async client."""
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.get_blocked = MagicMock(return_value=_async_gen(*blocked_users))
    bot.client.block_user = AsyncMock()
    bot.client.unblock_user = AsyncMock()
    bot._get_command_args = MagicMock(return_value=args)
    return bot


def _make_message(
    text: str = "!blocked",
    reply_user_id: int | None = None,
    reply_sender_chat_id: int | None = None,
) -> MagicMock:
    """Создаёт мок Telegram-сообщения."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()

    if reply_user_id is not None:
        replied = MagicMock()
        replied.from_user = MagicMock()
        replied.from_user.id = reply_user_id
        replied.sender_chat = None
        msg.reply_to_message = replied
    elif reply_sender_chat_id is not None:
        replied = MagicMock()
        replied.from_user = None
        replied.sender_chat = MagicMock()
        replied.sender_chat.id = reply_sender_chat_id
        msg.reply_to_message = replied
    else:
        msg.reply_to_message = None

    return msg


# ---------------------------------------------------------------------------
# Тесты !blocked list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_list_пустой():
    """!blocked list — если никого нет, сообщает об этом."""
    bot = _make_bot(blocked_users=[], args="list")
    msg = _make_message("!blocked list")

    await handle_blocked(bot, msg)

    msg.reply.assert_called_once()
    assert "пуст" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_blocked_list_несколько_пользователей():
    """!blocked list — выводит список при наличии заблокированных."""
    users = [
        _make_user(101, "Алиса", username="alice"),
        _make_user(102, "Боб", username=None),
    ]
    bot = _make_bot(blocked_users=users, args="list")
    msg = _make_message("!blocked list")

    await handle_blocked(bot, msg)

    msg.reply.assert_called_once()
    text = msg.reply.call_args[0][0]
    assert "101" in text
    assert "102" in text
    assert "Алиса" in text
    assert "@alice" in text


@pytest.mark.asyncio
async def test_blocked_list_пользователь_без_username():
    """!blocked list — пользователь без username отображается без скобок с @."""
    users = [_make_user(200, "Пётр", username=None)]
    bot = _make_bot(blocked_users=users, args="list")
    msg = _make_message("!blocked list")

    await handle_blocked(bot, msg)

    text = msg.reply.call_args[0][0]
    assert "200" in text
    assert "@" not in text


@pytest.mark.asyncio
async def test_blocked_list_пользователь_с_username():
    """!blocked list — username отображается с символом @."""
    users = [_make_user(300, "Катя", username="katya")]
    bot = _make_bot(blocked_users=users, args="list")
    msg = _make_message("!blocked list")

    await handle_blocked(bot, msg)

    text = msg.reply.call_args[0][0]
    assert "@katya" in text


@pytest.mark.asyncio
async def test_blocked_list_ошибка_get_blocked():
    """!blocked list — ошибка get_blocked поднимает UserInputError."""
    async def _bad_gen():
        raise Exception("NETWORK_ERROR")
        yield  # noqa: unreachable

    bot = _make_bot(args="list")
    bot.client.get_blocked = MagicMock(return_value=_bad_gen())
    msg = _make_message("!blocked list")

    with pytest.raises(UserInputError) as exc_info:
        await handle_blocked(bot, msg)

    assert "список заблокированных" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_blocked_без_аргументов_вызывает_list():
    """!blocked без аргументов ведёт себя как !blocked list."""
    bot = _make_bot(blocked_users=[], args="")
    msg = _make_message("!blocked")

    await handle_blocked(bot, msg)

    # Должен позвать get_blocked (list-ветка)
    bot.client.get_blocked.assert_called()


# ---------------------------------------------------------------------------
# Тесты !blocked add
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_add_по_reply_from_user():
    """!blocked add в reply — блокирует from_user.id."""
    bot = _make_bot(args="add")
    msg = _make_message("!blocked add", reply_user_id=555)

    await handle_blocked(bot, msg)

    bot.client.block_user.assert_called_once_with(555)
    assert "заблокирован" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_blocked_add_по_reply_sender_chat():
    """!blocked add в reply на канал — блокирует sender_chat.id."""
    bot = _make_bot(args="add")
    msg = _make_message("!blocked add", reply_sender_chat_id=777)

    await handle_blocked(bot, msg)

    bot.client.block_user.assert_called_once_with(777)


@pytest.mark.asyncio
async def test_blocked_add_по_username_строкой():
    """!blocked add @username — блокирует по username-строке."""
    bot = _make_bot(args="add @testuser")
    msg = _make_message("!blocked add @testuser")

    await handle_blocked(bot, msg)

    bot.client.block_user.assert_called_once_with("testuser")


@pytest.mark.asyncio
async def test_blocked_add_по_числовому_id():
    """!blocked add 123456 — блокирует по int user_id."""
    bot = _make_bot(args="add 123456")
    msg = _make_message("!blocked add 123456")

    await handle_blocked(bot, msg)

    bot.client.block_user.assert_called_once_with(123456)


@pytest.mark.asyncio
async def test_blocked_add_без_аргументов_UserInputError():
    """!blocked add без reply и без аргументов → UserInputError."""
    bot = _make_bot(args="add")
    msg = _make_message("!blocked add")  # нет reply

    with pytest.raises(UserInputError) as exc_info:
        await handle_blocked(bot, msg)

    assert "Укажи цель" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_blocked_add_reply_без_from_user_и_sender_chat():
    """!blocked add в reply без from_user и sender_chat → UserInputError."""
    bot = _make_bot(args="add")
    msg = _make_message("!blocked add")
    replied = MagicMock()
    replied.from_user = None
    replied.sender_chat = None
    msg.reply_to_message = replied

    with pytest.raises(UserInputError) as exc_info:
        await handle_blocked(bot, msg)

    assert "автора" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_blocked_add_ошибка_block_user():
    """!blocked add — ошибка block_user → UserInputError."""
    bot = _make_bot(args="add @baduser")
    bot.client.block_user = AsyncMock(side_effect=Exception("FLOOD_WAIT"))
    msg = _make_message("!blocked add @baduser")

    with pytest.raises(UserInputError) as exc_info:
        await handle_blocked(bot, msg)

    assert "заблокировать" in exc_info.value.user_message
    assert "FLOOD_WAIT" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_blocked_ban_алиас():
    """!blocked ban — алиас add."""
    bot = _make_bot(args="ban @someone")
    msg = _make_message("!blocked ban @someone")

    await handle_blocked(bot, msg)

    bot.client.block_user.assert_called_once_with("someone")


@pytest.mark.asyncio
async def test_blocked_block_алиас():
    """!blocked block — алиас add."""
    bot = _make_bot(args="block @x")
    msg = _make_message("!blocked block @x")

    await handle_blocked(bot, msg)

    bot.client.block_user.assert_called_once_with("x")


@pytest.mark.asyncio
async def test_blocked_заблок_алиас():
    """!blocked заблок — русский алиас add."""
    bot = _make_bot(args="заблок @ivan")
    msg = _make_message("!blocked заблок @ivan")

    await handle_blocked(bot, msg)

    bot.client.block_user.assert_called_once_with("ivan")


# ---------------------------------------------------------------------------
# Тесты !blocked remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_remove_по_username():
    """!blocked remove @username — разблокирует по username."""
    bot = _make_bot(args="remove @oldfriend")
    msg = _make_message("!blocked remove @oldfriend")

    await handle_blocked(bot, msg)

    bot.client.unblock_user.assert_called_once_with("oldfriend")
    assert "разблокирован" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_blocked_remove_по_id():
    """!blocked remove 654321 — разблокирует по числовому ID."""
    bot = _make_bot(args="remove 654321")
    msg = _make_message("!blocked remove 654321")

    await handle_blocked(bot, msg)

    bot.client.unblock_user.assert_called_once_with(654321)


@pytest.mark.asyncio
async def test_blocked_remove_без_аргумента_UserInputError():
    """!blocked remove без аргумента → UserInputError."""
    bot = _make_bot(args="remove")
    msg = _make_message("!blocked remove")

    with pytest.raises(UserInputError) as exc_info:
        await handle_blocked(bot, msg)

    assert "Укажи пользователя" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_blocked_remove_ошибка_unblock_user():
    """!blocked remove — ошибка unblock_user → UserInputError."""
    bot = _make_bot(args="remove @x")
    bot.client.unblock_user = AsyncMock(side_effect=Exception("PEER_ID_INVALID"))
    msg = _make_message("!blocked remove @x")

    with pytest.raises(UserInputError) as exc_info:
        await handle_blocked(bot, msg)

    assert "разблокировать" in exc_info.value.user_message
    assert "PEER_ID_INVALID" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_blocked_unblock_алиас():
    """!blocked unblock — алиас remove."""
    bot = _make_bot(args="unblock @y")
    msg = _make_message("!blocked unblock @y")

    await handle_blocked(bot, msg)

    bot.client.unblock_user.assert_called_once_with("y")


@pytest.mark.asyncio
async def test_blocked_del_алиас():
    """!blocked del — алиас remove."""
    bot = _make_bot(args="del @z")
    msg = _make_message("!blocked del @z")

    await handle_blocked(bot, msg)

    bot.client.unblock_user.assert_called_once_with("z")


@pytest.mark.asyncio
async def test_blocked_rm_алиас():
    """!blocked rm — алиас remove."""
    bot = _make_bot(args="rm 999")
    msg = _make_message("!blocked rm 999")

    await handle_blocked(bot, msg)

    bot.client.unblock_user.assert_called_once_with(999)


@pytest.mark.asyncio
async def test_blocked_разблок_алиас():
    """!blocked разблок — русский алиас remove."""
    bot = _make_bot(args="разблок @masha")
    msg = _make_message("!blocked разблок @masha")

    await handle_blocked(bot, msg)

    bot.client.unblock_user.assert_called_once_with("masha")


@pytest.mark.asyncio
async def test_blocked_список_алиас():
    """!blocked список — русский алиас list."""
    bot = _make_bot(blocked_users=[], args="список")
    msg = _make_message("!blocked список")

    await handle_blocked(bot, msg)

    bot.client.get_blocked.assert_called()
    assert "пуст" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Тесты справки
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_неизвестная_подкоманда_справка():
    """!blocked <неизвестное> — выводит справку."""
    bot = _make_bot(args="xyz")
    msg = _make_message("!blocked xyz")

    await handle_blocked(bot, msg)

    text = msg.reply.call_args[0][0]
    assert "blocked list" in text
    assert "blocked add" in text
    assert "blocked remove" in text


@pytest.mark.asyncio
async def test_blocked_справка_содержит_все_подкоманды():
    """Справка содержит list, add, remove."""
    bot = _make_bot(args="help")
    msg = _make_message("!blocked help")

    await handle_blocked(bot, msg)

    text = msg.reply.call_args[0][0]
    assert "list" in text
    assert "add" in text
    assert "remove" in text
