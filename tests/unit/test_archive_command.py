# -*- coding: utf-8 -*-
"""
Тесты команды !archive — архивация и метаданные Memory Layer.

Покрываем:
- !archive [no args] — архивировать текущий чат
- !archive list — список архивированных диалогов
- !archive stats — статистика archive.db
- !archive growth — рост archive.db
- Owner-only (AccessLevel проверка)
- Неизвестный subcommand → help
- Ошибки при работе с БД
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_archive


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    bot.me = MagicMock()
    bot.me.id = 999
    return bot


def _make_owner_message(
    args: str = "",
    chat_id: int = -42,
    user_id: int = 777,
) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()
    return msg


def _make_access_profile(level: AccessLevel = AccessLevel.OWNER) -> MagicMock:
    profile = MagicMock()
    profile.level = level
    return profile


# ---------------------------------------------------------------------------
# Тесты доступа
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_owner_only() -> None:
    """Только owner может использовать !archive."""
    bot = _make_bot("stats")
    msg = _make_owner_message()

    bot._get_access_profile = MagicMock(return_value=_make_access_profile(AccessLevel.GUEST))

    try:
        await handle_archive(bot, msg)
        assert False, "Should have raised UserInputError"
    except UserInputError as e:
        assert "владельцу" in str(e.user_message or "")


# ---------------------------------------------------------------------------
# Тесты archive stats (Memory Layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_stats_success() -> None:
    """!archive stats показывает статистику archive.db."""
    bot = _make_bot("stats")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    mock_handler = MagicMock()
    mock_handler.handle_stats = MagicMock(return_value="📚 **Archive Stats**\n• Size: 42.5 MB")
    mock_handler.close = MagicMock()

    with patch("src.handlers.memory_commands.MemoryCommandHandler", return_value=mock_handler):
        await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    call_args = msg.reply.call_args[0][0]
    assert "Archive Stats" in call_args


@pytest.mark.asyncio
async def test_archive_growth_success() -> None:
    """!archive growth показывает рост archive.db."""
    bot = _make_bot("growth")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    mock_handler = MagicMock()
    mock_stats = MagicMock()
    mock_stats.db_size_bytes = 50_000_000  # 50 MB
    mock_stats.messages = 5000
    mock_stats.chats = 50
    mock_stats.chunks = 10000
    mock_stats.vectors = 8000
    mock_handler.collect_stats = MagicMock(return_value=mock_stats)
    mock_handler.close = MagicMock()

    with patch("src.handlers.memory_commands.MemoryCommandHandler", return_value=mock_handler):
        await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    call_args = msg.reply.call_args[0][0]
    assert "Archive Growth" in call_args
    assert "MB" in call_args
    assert "5" in call_args  # messages formatted with spaces as "5 000"


@pytest.mark.asyncio
async def test_archive_growth_db_not_exists() -> None:
    """!archive growth когда archive.db нет."""
    bot = _make_bot("growth")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    mock_handler = MagicMock()
    mock_stats = MagicMock()
    mock_stats.db_size_bytes = 0
    mock_handler.collect_stats = MagicMock(return_value=mock_stats)
    mock_handler.close = MagicMock()

    with patch("src.handlers.memory_commands.MemoryCommandHandler", return_value=mock_handler):
        await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    call_args = msg.reply.call_args[0][0]
    assert "не существует" in call_args


# ---------------------------------------------------------------------------
# Тесты Telegram диалогов архивации
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_default_archives_chat() -> None:
    """!archive (без args) архивирует текущий чат."""
    bot = _make_bot("")  # no args
    msg = _make_owner_message(chat_id=-100123)
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())
    bot.client = AsyncMock()
    bot.client.archive_chats = AsyncMock()

    # Если нет Memory subcommand, используем default Telegram архивацию
    await handle_archive(bot, msg)

    bot.client.archive_chats.assert_called_once_with(-100123)


@pytest.mark.asyncio
async def test_archive_list_shows_chats() -> None:
    """!archive list показывает архивированные чаты."""
    bot = _make_bot("list")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    # Mock client.get_dialogs
    mock_dialog1 = MagicMock()
    mock_dialog1.chat.id = -100123
    mock_dialog1.chat.title = "Test Channel"

    mock_dialog2 = MagicMock()
    mock_dialog2.chat.id = 456
    mock_dialog2.chat.first_name = "John"

    async def _get_dialogs(folder_id=None):
        for d in [mock_dialog1, mock_dialog2]:
            yield d

    bot.client = AsyncMock()
    bot.client.get_dialogs = _get_dialogs

    await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    call_args = msg.reply.call_args[0][0]
    assert "Архивированные чаты" in call_args
    assert "-100123" in call_args
    assert "Test Channel" in call_args


# ---------------------------------------------------------------------------
# Тесты неизвестного subcommand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_unknown_subcommand_shows_help() -> None:
    """Неизвестный subcommand показывает справку."""
    bot = _make_bot("unknown-arg")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())
    bot.client = AsyncMock()

    await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    call_args = msg.reply.call_args[0][0]
    assert "Archive commands" in call_args
    assert "list" in call_args
    assert "stats" in call_args
    assert "growth" in call_args
