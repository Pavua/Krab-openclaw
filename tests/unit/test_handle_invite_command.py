# -*- coding: utf-8 -*-
"""
Тесты команды !invite — приглашение пользователей в группу.

Покрываем:
  1. не-owner получает UserInputError
  2. отсутствие аргументов — справка (UserInputError)
  3. !invite @username — успешное добавление
  4. !invite @username — ошибка Pyrogram (UserInputError)
  5. !invite link — успешное создание ссылки
  6. !invite link — ошибка Pyrogram (UserInputError)
  7. !invite link revoke <url> — успешный отзыв
  8. !invite link revoke — без url (UserInputError)
  9. !invite link revoke <url> — ошибка Pyrogram (UserInputError)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_invite


def _make_bot(*, access_level: AccessLevel) -> SimpleNamespace:
    """Минимальный mock бота с заданным уровнем доступа."""
    return SimpleNamespace(
        client=AsyncMock(),
        _get_access_profile=lambda user: AccessProfile(level=access_level, source="test"),
    )


def _make_message(chat_id: int, command_args: list[str]) -> SimpleNamespace:
    """Минимальный mock сообщения с заданными аргументами команды."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=1, username="owner"),
        chat=SimpleNamespace(id=chat_id),
        # command[0] — сама команда ("invite"), [1:] — аргументы
        command=["invite"] + command_args,
        reply=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Проверка доступа
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_rejects_non_owner() -> None:
    """Не-owner получает UserInputError с сообщением про блокировку."""
    bot = _make_bot(access_level=AccessLevel.FULL)
    message = _make_message(chat_id=-100, command_args=["@someone"])

    with pytest.raises(UserInputError) as exc_info:
        await handle_invite(bot, message)

    assert "владельцу" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_invite_rejects_partial_access() -> None:
    """Пользователь с partial-доступом тоже получает отказ."""
    bot = _make_bot(access_level=AccessLevel.PARTIAL)
    message = _make_message(chat_id=-100, command_args=["@someone"])

    with pytest.raises(UserInputError):
        await handle_invite(bot, message)


# ---------------------------------------------------------------------------
# Справка
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_no_args_shows_help() -> None:
    """!invite без аргументов выводит справку через UserInputError."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    message = _make_message(chat_id=-100, command_args=[])

    with pytest.raises(UserInputError) as exc_info:
        await handle_invite(bot, message)

    help_text = exc_info.value.user_message
    assert "!invite @username" in help_text
    assert "!invite link" in help_text
    assert "revoke" in help_text


# ---------------------------------------------------------------------------
# !invite @username — добавление участника
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_add_user_success() -> None:
    """!invite @username — успешно добавляет и отправляет подтверждение."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    bot.client.add_chat_members = AsyncMock()
    message = _make_message(chat_id=-100123, command_args=["@vasya"])

    await handle_invite(bot, message)

    bot.client.add_chat_members.assert_awaited_once_with(-100123, "@vasya")
    message.reply.assert_awaited_once()
    reply_text = message.reply.await_args.args[0]
    assert "@vasya" in reply_text
    assert "добавлен" in reply_text


@pytest.mark.asyncio
async def test_invite_add_user_pyrogram_error() -> None:
    """!invite @username — ошибка Pyrogram оборачивается в UserInputError."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    bot.client.add_chat_members = AsyncMock(side_effect=Exception("USER_NOT_MUTUAL_CONTACT"))
    message = _make_message(chat_id=-100123, command_args=["@vasya"])

    with pytest.raises(UserInputError) as exc_info:
        await handle_invite(bot, message)

    assert "@vasya" in exc_info.value.user_message
    assert "USER_NOT_MUTUAL_CONTACT" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_invite_add_user_numeric_id() -> None:
    """!invite 987654321 — работает с числовым user_id."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    bot.client.add_chat_members = AsyncMock()
    message = _make_message(chat_id=-100999, command_args=["987654321"])

    await handle_invite(bot, message)

    bot.client.add_chat_members.assert_awaited_once_with(-100999, "987654321")
    reply_text = message.reply.await_args.args[0]
    assert "987654321" in reply_text


# ---------------------------------------------------------------------------
# !invite link — создание ссылки
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_link_creates_invite_link() -> None:
    """!invite link — создаёт ссылку и выводит её."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    fake_link = MagicMock()
    fake_link.invite_link = "https://t.me/+ABCDEF"
    bot.client.create_chat_invite_link = AsyncMock(return_value=fake_link)
    message = _make_message(chat_id=-100555, command_args=["link"])

    await handle_invite(bot, message)

    bot.client.create_chat_invite_link.assert_awaited_once_with(-100555)
    reply_text = message.reply.await_args.args[0]
    assert "https://t.me/+ABCDEF" in reply_text


@pytest.mark.asyncio
async def test_invite_link_pyrogram_error() -> None:
    """!invite link — ошибка Pyrogram оборачивается в UserInputError."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    bot.client.create_chat_invite_link = AsyncMock(side_effect=Exception("CHAT_ADMIN_REQUIRED"))
    message = _make_message(chat_id=-100555, command_args=["link"])

    with pytest.raises(UserInputError) as exc_info:
        await handle_invite(bot, message)

    assert "CHAT_ADMIN_REQUIRED" in exc_info.value.user_message


# ---------------------------------------------------------------------------
# !invite link revoke — отзыв ссылки
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_link_revoke_success() -> None:
    """!invite link revoke <url> — отзывает ссылку и подтверждает."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    fake_revoked = MagicMock()
    fake_revoked.invite_link = "https://t.me/+ABCDEF"
    bot.client.revoke_chat_invite_link = AsyncMock(return_value=fake_revoked)
    message = _make_message(
        chat_id=-100777, command_args=["link", "revoke", "https://t.me/+ABCDEF"]
    )

    await handle_invite(bot, message)

    bot.client.revoke_chat_invite_link.assert_awaited_once_with(
        -100777, "https://t.me/+ABCDEF"
    )
    reply_text = message.reply.await_args.args[0]
    assert "отозвана" in reply_text
    assert "https://t.me/+ABCDEF" in reply_text


@pytest.mark.asyncio
async def test_invite_link_revoke_no_url() -> None:
    """!invite link revoke без url — UserInputError с подсказкой."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    message = _make_message(chat_id=-100777, command_args=["link", "revoke"])

    with pytest.raises(UserInputError) as exc_info:
        await handle_invite(bot, message)

    assert "revoke" in exc_info.value.user_message
    assert "<url>" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_invite_link_revoke_pyrogram_error() -> None:
    """!invite link revoke — ошибка Pyrogram оборачивается в UserInputError."""
    bot = _make_bot(access_level=AccessLevel.OWNER)
    bot.client.revoke_chat_invite_link = AsyncMock(
        side_effect=Exception("INVITE_HASH_INVALID")
    )
    message = _make_message(
        chat_id=-100777, command_args=["link", "revoke", "https://t.me/+BADLINK"]
    )

    with pytest.raises(UserInputError) as exc_info:
        await handle_invite(bot, message)

    assert "INVITE_HASH_INVALID" in exc_info.value.user_message
