# -*- coding: utf-8 -*-
"""
Тесты команды !archive — архивация и статистика Memory Layer.

Покрываем:
- !archive [no args] — архивировать текущий чат
- !archive list — список архивированных диалогов
- !archive stats — статистика archive.db
- !archive growth — рост archive.db
- Owner-only (AccessLevel проверка)
- Ошибки при работе с БД
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
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


def _create_test_db(tmp_path: Path, msg_count: int = 5, chunk_count: int = 3) -> Path:
    """Создаёт тестовую archive.db с заданным кол-вом сообщений и чанков."""
    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, date TEXT, content TEXT)"
    )
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, data TEXT)")
    for i in range(msg_count):
        conn.execute("INSERT INTO messages VALUES (?, ?, ?)", (i, "2026-01-01", f"msg{i}"))
    for i in range(chunk_count):
        conn.execute("INSERT INTO chunks VALUES (?, ?)", (i, f"chunk{i}"))
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Тесты доступа
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_owner_only() -> None:
    """Только owner может использовать !archive."""
    bot = _make_bot("stats")
    msg = _make_owner_message()

    bot._get_access_profile = MagicMock(return_value=_make_access_profile(AccessLevel.GUEST))

    with pytest.raises(UserInputError) as exc_info:
        await handle_archive(bot, msg)

    assert "владельцу" in str(exc_info.value.user_message or "")


# ---------------------------------------------------------------------------
# Тесты !archive stats (Memory Layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_stats_success() -> None:
    """!archive stats показывает статистику archive.db (размер, сообщения, чанки)."""
    bot = _make_bot("stats")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    with tempfile.TemporaryDirectory() as tmp:
        db_path = _create_test_db(Path(tmp), msg_count=10, chunk_count=4)

        with patch("src.core.archive_growth_monitor.ARCHIVE_DB", db_path):
            await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    reply = msg.reply.call_args[0][0]
    assert "Archive.db stats" in reply
    assert "Сообщений" in reply
    assert "10" in reply
    assert "Чанков" in reply
    assert "4" in reply


@pytest.mark.asyncio
async def test_archive_stats_db_not_found() -> None:
    """!archive stats когда archive.db не существует."""
    bot = _make_bot("stats")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    missing = Path("/tmp/_krab_test_nonexistent_archive_xyz.db")
    # Убеждаемся что файл реально отсутствует
    missing.unlink(missing_ok=True)

    with patch("src.core.archive_growth_monitor.ARCHIVE_DB", missing):
        await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    reply = msg.reply.call_args[0][0]
    assert "не найден" in reply


# ---------------------------------------------------------------------------
# Тесты !archive growth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_growth_no_data() -> None:
    """!archive growth когда archive.db нет → сообщение об отсутствии."""
    bot = _make_bot("growth")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    missing = Path("/tmp/_krab_test_nonexistent_archive_xyz.db")
    missing.unlink(missing_ok=True)

    with patch("src.core.archive_growth_monitor.ARCHIVE_DB", missing):
        await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    reply = msg.reply.call_args[0][0]
    assert "не найден" in reply


@pytest.mark.asyncio
async def test_archive_growth_with_history() -> None:
    """!archive growth с достаточной историей снапшотов — показывает скорость роста."""
    bot = _make_bot("growth")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    import time

    from src.core.archive_growth_monitor import GrowthSnapshot

    now = int(time.time())
    fake_snap = GrowthSnapshot(ts=now, size_mb=12.0, message_count=1200)
    fake_summary = {
        "snapshots": 2,
        "days_tracked": 1.0,
        "first_size_mb": 10.0,
        "latest_size_mb": 12.0,
        "latest_messages": 1200,
        "growth_mb_per_day": 2.0,
        "growth_messages_per_day": 200,
    }

    with (
        patch("src.core.archive_growth_monitor.take_snapshot", return_value=fake_snap),
        patch("src.core.archive_growth_monitor.growth_summary", return_value=fake_summary),
    ):
        await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    reply = msg.reply.call_args[0][0]
    assert "Archive.db growth" in reply
    assert "2.00 MB/день" in reply
    assert "1,200" in reply


@pytest.mark.asyncio
async def test_archive_growth_insufficient_history() -> None:
    """!archive growth с 1 снапшотом — показывает текущее состояние без динамики."""
    bot = _make_bot("growth")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    import time

    from src.core.archive_growth_monitor import GrowthSnapshot

    now = int(time.time())
    fake_snap = GrowthSnapshot(ts=now, size_mb=5.0, message_count=500)
    fake_summary = {"snapshots": 1, "summary": "Not enough data"}

    with (
        patch("src.core.archive_growth_monitor.take_snapshot", return_value=fake_snap),
        patch("src.core.archive_growth_monitor.growth_summary", return_value=fake_summary),
    ):
        await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    reply = msg.reply.call_args[0][0]
    assert "Archive.db growth" in reply
    assert "Not enough data" in reply
    assert "5.00 MB" in reply


# ---------------------------------------------------------------------------
# Тесты Telegram архивации
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_default_archives_chat() -> None:
    """!archive (без args) архивирует текущий чат."""
    bot = _make_bot("")  # нет аргументов
    msg = _make_owner_message(chat_id=-100123)
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())
    bot.client = AsyncMock()
    bot.client.archive_chats = AsyncMock()

    await handle_archive(bot, msg)

    bot.client.archive_chats.assert_called_once_with(-100123)
    msg.reply.assert_called_once()
    assert "архив" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_archive_list_shows_chats() -> None:
    """!archive list показывает архивированные чаты."""
    bot = _make_bot("list")
    msg = _make_owner_message()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile())

    mock_dialog1 = MagicMock()
    mock_dialog1.chat.id = -100123
    mock_dialog1.chat.title = "Test Channel"
    mock_dialog1.chat.first_name = None

    mock_dialog2 = MagicMock()
    mock_dialog2.chat.id = 456
    mock_dialog2.chat.title = None
    mock_dialog2.chat.first_name = "John"

    async def _get_dialogs(folder_id=None):
        for d in [mock_dialog1, mock_dialog2]:
            yield d

    bot.client = AsyncMock()
    bot.client.get_dialogs = _get_dialogs

    await handle_archive(bot, msg)

    msg.reply.assert_called_once()
    reply = msg.reply.call_args[0][0]
    assert "Архивированные чаты" in reply
    assert "-100123" in reply
    assert "Test Channel" in reply
