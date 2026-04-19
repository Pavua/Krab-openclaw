# -*- coding: utf-8 -*-
"""
Тесты команды !who — инфо о пользователе/чате.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.command_handlers import handle_who


def _make_bot(command_args: str = "") -> SimpleNamespace:
    """Минимальный mock-бот с клиентом."""
    bot = SimpleNamespace(
        _get_command_args=lambda msg: command_args,
        client=MagicMock(),
        me=SimpleNamespace(id=1),
    )
    return bot


def _make_message(
    reply_to_message=None,
    chat_id: int = -100123,
) -> SimpleNamespace:
    """Минимальный mock-Message."""
    return SimpleNamespace(
        reply_to_message=reply_to_message,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=42),
        reply=AsyncMock(),
    )


def _make_user(
    user_id: int = 123456789,
    first_name: str = "Pavel",
    last_name: str = "Durov",
    username: str = "durov",
    is_bot: bool = False,
    is_premium: bool = True,
    is_verified: bool = False,
    is_restricted: bool = False,
    is_scam: bool = False,
    is_fake: bool = False,
    status=None,
    phone_number: str | None = None,
) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.first_name = first_name
    user.last_name = last_name
    user.username = username
    user.is_bot = is_bot
    user.is_premium = is_premium
    user.is_verified = is_verified
    user.is_restricted = is_restricted
    user.is_scam = is_scam
    user.is_fake = is_fake
    user.status = status
    user.phone_number = phone_number
    return user


def _make_chat_info(bio: str | None = "Founder of Telegram") -> MagicMock:
    chat = MagicMock()
    chat.bio = bio
    return chat


# ─────────────────────────────────────────────
# 1. !who @username — инфо по имени
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_who_by_username_shows_user_info() -> None:
    """!who @durov — должен показать имя, username, ID."""
    bot = _make_bot("@durov")
    message = _make_message()

    user = _make_user()
    common_chats = [MagicMock(), MagicMock(), MagicMock()]
    chat_info = _make_chat_info()

    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=common_chats)
    bot.client.get_chat = AsyncMock(return_value=chat_info)

    await handle_who(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]

    assert "User Info" in text
    assert "Pavel Durov" in text
    assert "@durov" in text
    assert "123456789" in text
    assert "Premium:** да" in text
    assert "Общих чатов:** 3" in text
    assert "Founder of Telegram" in text


@pytest.mark.asyncio
async def test_who_by_username_strips_at_sign() -> None:
    """!who durov (без @) тоже должен работать."""
    bot = _make_bot("durov")
    message = _make_message()

    user = _make_user()
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    # get_users должен быть вызван с "durov" (без @)
    bot.client.get_users.assert_awaited_once_with("durov")


@pytest.mark.asyncio
async def test_who_by_numeric_id() -> None:
    """!who 123456789 — числовой ID."""
    bot = _make_bot("123456789")
    message = _make_message()

    user = _make_user()
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    # get_users должен быть вызван с int(123456789)
    bot.client.get_users.assert_awaited_once_with(123456789)


# ─────────────────────────────────────────────
# 2. !who — reply на сообщение
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_who_reply_shows_author_info() -> None:
    """!who в ответ на сообщение — показывает автора."""
    bot = _make_bot("")
    replied = SimpleNamespace(
        from_user=SimpleNamespace(id=999),
        sender_chat=None,
    )
    message = _make_message(reply_to_message=replied)

    user = _make_user(user_id=999, first_name="Test", last_name="User", username="testuser")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    bot.client.get_users.assert_awaited_once_with(999)
    text = message.reply.await_args.args[0]
    assert "Test User" in text


@pytest.mark.asyncio
async def test_who_reply_sender_chat() -> None:
    """!who — reply на канал (sender_chat вместо from_user)."""
    bot = _make_bot("")
    replied = SimpleNamespace(
        from_user=None,
        sender_chat=SimpleNamespace(id=-100777),
    )
    message = _make_message(reply_to_message=replied)

    chat = MagicMock()
    chat.id = -100777
    chat.title = "Test Channel"
    chat.first_name = None
    chat.username = "testchannel"
    chat.type = "CHANNEL"
    chat.members_count = 1000
    chat.description = "Описание канала"
    bot.client.get_chat = AsyncMock(return_value=chat)

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "Chat Info" in text
    assert "Test Channel" in text


@pytest.mark.asyncio
async def test_who_reply_no_sender() -> None:
    """!who — reply на сообщение без from_user и sender_chat."""
    bot = _make_bot("")
    replied = SimpleNamespace(from_user=None, sender_chat=None)
    message = _make_message(reply_to_message=replied)

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "Не могу определить" in text


# ─────────────────────────────────────────────
# 3. !who без аргументов и без reply — текущий чат
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_who_no_args_shows_current_chat() -> None:
    """!who без аргументов — показывает инфо о текущем чате."""
    bot = _make_bot("")
    message = _make_message(chat_id=-100999)

    chat = MagicMock()
    chat.id = -100999
    chat.title = "My Group"
    chat.first_name = None
    chat.username = "mygroup"
    chat.type = "GROUP"
    chat.members_count = 42
    chat.description = "Тестовая группа"
    bot.client.get_chat = AsyncMock(return_value=chat)

    await handle_who(bot, message)

    bot.client.get_chat.assert_awaited_once_with(-100999)
    text = message.reply.await_args.args[0]
    assert "Chat Info" in text
    assert "My Group" in text
    assert "@mygroup" in text
    assert "42" in text


@pytest.mark.asyncio
async def test_who_chat_no_username() -> None:
    """Чат без username — показывает 'отсутствует'."""
    bot = _make_bot("")
    message = _make_message(chat_id=-100888)

    chat = MagicMock()
    chat.id = -100888
    chat.title = "Private Group"
    chat.first_name = None
    chat.username = None
    chat.type = "SUPERGROUP"
    chat.members_count = None
    chat.description = None
    bot.client.get_chat = AsyncMock(return_value=chat)

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "отсутствует" in text


# ─────────────────────────────────────────────
# 4. Специальные флаги пользователя
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_who_bot_user_no_common_chats() -> None:
    """Для ботов не запрашиваем общие чаты."""
    bot = _make_bot("@some_bot")
    message = _make_message()

    user = _make_user(is_bot=True, username="some_bot")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    bot.client.get_common_chats = AsyncMock()
    # Проверяем, что в тексте есть "Бот: да" и нет "Общих чатов"
    text = message.reply.await_args.args[0]
    assert "Бот:** да" in text
    assert "Общих чатов" not in text


@pytest.mark.asyncio
async def test_who_scam_user_shows_warning() -> None:
    """SCAM пользователь — предупреждение в заголовке."""
    bot = _make_bot("scammer")
    message = _make_message()

    user = _make_user(is_scam=True, username="scammer")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "SCAM" in text


@pytest.mark.asyncio
async def test_who_fake_user_shows_warning() -> None:
    """FAKE пользователь — предупреждение в заголовке."""
    bot = _make_bot("fake_user")
    message = _make_message()

    user = _make_user(is_fake=True, username="fake_user")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "FAKE" in text


@pytest.mark.asyncio
async def test_who_restricted_user_shows_flag() -> None:
    """Restricted пользователь — флаг в выводе."""
    bot = _make_bot("restricted_user")
    message = _make_message()

    user = _make_user(is_restricted=True, username="restricted_user")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "Restricted" in text


# ─────────────────────────────────────────────
# 5. Обработка ошибок API
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_who_user_not_found_shows_error() -> None:
    """Ошибка get_users — показываем сообщение об ошибке."""
    bot = _make_bot("@nobody")
    message = _make_message()

    bot.client.get_users = AsyncMock(side_effect=Exception("Not found"))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "Ошибка" in text
    assert "Not found" in text


@pytest.mark.asyncio
async def test_who_chat_api_error_shows_error() -> None:
    """Ошибка get_chat (режим чата) — показываем сообщение об ошибке."""
    bot = _make_bot("")
    message = _make_message(chat_id=-100500)

    bot.client.get_chat = AsyncMock(side_effect=Exception("Forbidden"))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "Ошибка" in text
    assert "Forbidden" in text


@pytest.mark.asyncio
async def test_who_common_chats_error_shows_dash() -> None:
    """Ошибка get_common_chats — показывает '—' вместо падения."""
    bot = _make_bot("@someone")
    message = _make_message()

    user = _make_user()
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(side_effect=Exception("Rate limit"))
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    # Не упало, показало прочерк
    assert "Общих чатов:** —" in text


@pytest.mark.asyncio
async def test_who_bio_error_shows_dash() -> None:
    """Ошибка get_chat для bio — показывает '—' вместо падения."""
    bot = _make_bot("@someone")
    message = _make_message()

    user = _make_user()
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(side_effect=Exception("Privacy"))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "Bio:** —" in text


# ─────────────────────────────────────────────
# 6. Форматирование имени
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_who_no_last_name() -> None:
    """Пользователь без фамилии — только имя."""
    bot = _make_bot("single_name")
    message = _make_message()

    user = _make_user(first_name="OnlyFirst", last_name=None, username="single_name")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "OnlyFirst" in text
    # Не должно быть "OnlyFirst None"
    assert "None" not in text


@pytest.mark.asyncio
async def test_who_no_username() -> None:
    """Пользователь без username — показывает '—'."""
    bot = _make_bot("12345")
    message = _make_message()

    user = _make_user(username=None)
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "Username:** —" in text


@pytest.mark.asyncio
async def test_who_phone_number_visible() -> None:
    """Если телефон доступен — показываем его."""
    bot = _make_bot("@contact")
    message = _make_message()

    user = _make_user(phone_number="+79001234567", username="contact")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "+79001234567" in text


@pytest.mark.asyncio
async def test_who_phone_hidden_by_default() -> None:
    """Без телефона — показываем 'скрыт'."""
    bot = _make_bot("@stranger")
    message = _make_message()

    user = _make_user(phone_number=None, username="stranger")
    bot.client.get_users = AsyncMock(return_value=user)
    bot.client.get_common_chats = AsyncMock(return_value=[])
    bot.client.get_chat = AsyncMock(return_value=_make_chat_info(None))

    await handle_who(bot, message)

    text = message.reply.await_args.args[0]
    assert "скрыт" in text
