# -*- coding: utf-8 -*-
"""Unit-тесты для `!memory clear` subcommand — selective archive.db cleanup."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Env-vars до импорта src.* (иначе config.py падает на TELEGRAM_API_ID).
for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

import pytest  # noqa: E402

from src.core.reset_helpers import (  # noqa: E402
    delete_archive_messages_before,
    list_archive_chats,
)
from src.handlers import command_handlers as ch  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_archive(tmp_path: Path) -> Path:
    """Создаёт минимальную archive.db с тестовыми данными."""
    db = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id TEXT PRIMARY KEY,
            title   TEXT,
            chat_type TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT,
            chat_id    TEXT,
            date       INTEGER,
            text       TEXT,
            PRIMARY KEY (message_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            chat_id  TEXT
        );
        CREATE TABLE IF NOT EXISTS chunk_messages (
            chunk_id   TEXT,
            message_id TEXT,
            chat_id    TEXT
        );
        CREATE TABLE IF NOT EXISTS indexer_state (
            chat_id TEXT PRIMARY KEY
        );
        INSERT INTO chats VALUES ('-100111', 'Alpha Chat', 'supergroup');
        INSERT INTO chats VALUES ('-100222', 'Beta Chat', 'group');
        INSERT INTO messages VALUES ('m1', '-100111', 1700000000, 'hello');
        INSERT INTO messages VALUES ('m2', '-100111', 1700000001, 'world');
        INSERT INTO messages VALUES ('m3', '-100222', 1600000000, 'old msg');
        INSERT INTO chunks VALUES ('c1', '-100111');
        INSERT INTO chunk_messages VALUES ('c1', 'm1', '-100111');
        INSERT INTO indexer_state VALUES ('-100111');
        """
    )
    conn.commit()
    conn.close()
    return db


def _make_bot(owner_id: int = 42) -> MagicMock:
    """Создаёт mock bot с me.id = owner_id."""
    bot = MagicMock()
    bot.me = MagicMock()
    bot.me.id = owner_id
    return bot


def _make_message(text: str, sender_id: int = 42) -> MagicMock:
    mock = MagicMock()
    mock.text = text
    mock.reply = AsyncMock()
    mock.from_user = MagicMock()
    mock.from_user.id = sender_id
    return mock


# ---------------------------------------------------------------------------
# reset_helpers: list_archive_chats
# ---------------------------------------------------------------------------


def test_list_archive_chats_returns_sorted(tmp_path: Path) -> None:
    """list_archive_chats возвращает чаты отсортированные по убыванию count."""
    db = _make_archive(tmp_path)
    chats = list_archive_chats(db_path=db)
    assert len(chats) == 2
    # Alpha Chat — 2 сообщения, должен быть первым
    assert chats[0]["chat_id"] == "-100111"
    assert chats[0]["message_count"] == 2
    assert chats[1]["chat_id"] == "-100222"
    assert chats[1]["message_count"] == 1


def test_list_archive_chats_missing_db(tmp_path: Path) -> None:
    """Если БД нет — возвращает пустой список, не бросает."""
    result = list_archive_chats(db_path=tmp_path / "nonexistent.db")
    assert result == []


# ---------------------------------------------------------------------------
# reset_helpers: delete_archive_messages_before
# ---------------------------------------------------------------------------


def test_delete_archive_messages_before_removes_old(tmp_path: Path) -> None:
    """delete_archive_messages_before удаляет только старые messages."""
    db = _make_archive(tmp_path)
    # cutoff между 1600000000 (Beta msg) и 1700000000 (Alpha msgs)
    cutoff = 1650000000
    deleted = delete_archive_messages_before(cutoff, db_path=db)
    assert deleted == 1  # только m3

    with sqlite3.connect(str(db)) as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert remaining == 2


def test_delete_archive_messages_before_no_match(tmp_path: Path) -> None:
    """Если нет сообщений старше cutoff — возвращает 0, ничего не удаляет."""
    db = _make_archive(tmp_path)
    deleted = delete_archive_messages_before(1000, db_path=db)
    assert deleted == 0


def test_delete_archive_messages_before_missing_db(tmp_path: Path) -> None:
    """Если БД нет — возвращает 0, не бросает."""
    result = delete_archive_messages_before(1700000000, db_path=tmp_path / "none.db")
    assert result == 0


# ---------------------------------------------------------------------------
# _handle_memory_clear: preview (no args)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_no_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """!memory clear без аргументов — preview с топом чатов."""
    db = _make_archive(tmp_path)
    monkeypatch.setattr(ch, "_ARCHIVE_DB_PATH_FOR_CLEAR", db)

    msg = _make_message("!memory clear")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)

    msg.reply.assert_awaited_once()
    reply_text = msg.reply.call_args.args[0]
    assert "Archive preview" in reply_text
    assert "-100111" in reply_text
    assert "Alpha Chat" in reply_text
    assert "--chat=" in reply_text
    assert "--before=" in reply_text


# ---------------------------------------------------------------------------
# _handle_memory_clear: --chat without --confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_chat_without_confirm_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """!memory clear --chat=X без --confirm — показывает счётчик и предупреждение."""
    db = _make_archive(tmp_path)
    monkeypatch.setattr(ch, "_ARCHIVE_DB_PATH_FOR_CLEAR", db)

    msg = _make_message("!memory clear --chat=-100111")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "--confirm" in reply_text
    assert "2" in reply_text  # 2 сообщения


# ---------------------------------------------------------------------------
# _handle_memory_clear: --chat --confirm deletes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_chat_with_confirm_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """!memory clear --chat=X --confirm — реально удаляет сообщения."""
    db = _make_archive(tmp_path)
    monkeypatch.setattr(ch, "_ARCHIVE_DB_PATH_FOR_CLEAR", db)

    msg = _make_message("!memory clear --chat=-100111 --confirm")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "2" in reply_text
    assert "🗑️" in reply_text

    # Проверяем, что записи удалены
    with sqlite3.connect(str(db)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id = ?", ("-100111",)
        ).fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# _handle_memory_clear: --before with confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_before_date_with_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """!memory clear --before=YYYY-MM-DD --confirm — удаляет старые сообщения."""
    db = _make_archive(tmp_path)
    monkeypatch.setattr(ch, "_ARCHIVE_DB_PATH_FOR_CLEAR", db)

    # 1650000000 ≈ 2022-04 — удалит m3 (date=1600000000), оставит m1,m2
    msg = _make_message("!memory clear --before=2022-04-15 --confirm")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "🗑️" in reply_text

    with sqlite3.connect(str(db)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert total == 2


@pytest.mark.asyncio
async def test_clear_before_date_without_confirm_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """!memory clear --before без --confirm — предупреждение с числом."""
    db = _make_archive(tmp_path)
    monkeypatch.setattr(ch, "_ARCHIVE_DB_PATH_FOR_CLEAR", db)

    msg = _make_message("!memory clear --before=2022-04-15")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "--confirm" in reply_text


# ---------------------------------------------------------------------------
# _handle_memory_clear: missing archive.db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_archive_db_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Если archive.db нет — вежливый ответ, не бросает исключение."""
    monkeypatch.setattr(ch, "_ARCHIVE_DB_PATH_FOR_CLEAR", tmp_path / "nonexistent.db")

    msg = _make_message("!memory clear")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "Archive не существует" in reply_text


# ---------------------------------------------------------------------------
# ACL: non-owner cannot use !memory clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_blocked_for_non_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Не-владелец получает отказ на !memory clear."""
    db = _make_archive(tmp_path)
    monkeypatch.setattr(ch, "_ARCHIVE_DB_PATH_FOR_CLEAR", db)

    msg = _make_message("!memory clear", sender_id=999)
    bot = _make_bot(owner_id=42)  # owner != sender
    await ch.handle_memory(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "🚫" in reply_text
    assert "владельцу" in reply_text


# ---------------------------------------------------------------------------
# Backward compatibility: stats и recent не поломаны
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_subcommand_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """!memory stats всё ещё диспатчится в _handle_memory_stats."""
    called = {"flag": False}

    async def _mock_stats(message: object) -> None:  # noqa: ARG001
        called["flag"] = True

    monkeypatch.setattr(ch, "_handle_memory_stats", _mock_stats)

    msg = _make_message("!memory stats")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)
    assert called["flag"] is True


@pytest.mark.asyncio
async def test_recent_subcommand_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """!memory recent не сломан после добавления clear."""
    monkeypatch.setattr(ch, "list_workspace_memory_entries", lambda limit, source_filter: [])

    msg = _make_message("!memory recent")
    bot = _make_bot()
    await ch.handle_memory(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "нет подходящих записей" in reply_text
