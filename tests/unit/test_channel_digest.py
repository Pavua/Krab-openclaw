# -*- coding: utf-8 -*-
"""Тесты ChannelDigestBuilder (Idea 6)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.channel_digest import (
    ChannelDigestBuilder,
    _pseudonimize,
)


def _make_archive(tmp_path: Path) -> Path:
    """Создаёт минимально-совместимую с modern memory_archive БД."""
    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE chats (
            chat_id TEXT PRIMARY KEY,
            title TEXT,
            chat_type TEXT,
            last_indexed_at TEXT,
            message_count INTEGER NOT NULL DEFAULT 0
        ) WITHOUT ROWID;

        CREATE TABLE messages (
            message_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            sender_id TEXT,
            timestamp TEXT NOT NULL,
            text_redacted TEXT NOT NULL,
            reply_to_id TEXT,
            PRIMARY KEY (chat_id, message_id)
        ) WITHOUT ROWID;

        CREATE TABLE response_feedback (
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            positive_count INTEGER NOT NULL DEFAULT 0,
            negative_count INTEGER NOT NULL DEFAULT 0,
            last_updated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        ) WITHOUT ROWID;

        CREATE TABLE message_media_summaries (
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            model_name TEXT,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        ) WITHOUT ROWID;
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _fixed_now() -> datetime:
    return datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_empty_input_returns_empty_string(tmp_path: Path) -> None:
    """Пустая archive.db → пустая строка (caller skip публикации)."""
    db = _make_archive(tmp_path)
    builder = ChannelDigestBuilder(archive_path=db, now_fn=_fixed_now)

    md = builder.build_digest(source_chats=[-1001234567890], hours_back=24)
    assert md == ""


def test_hot_topics_extracted_by_message_count(tmp_path: Path) -> None:
    """Чат с большим числом сообщений попадает в Hottest topics."""
    db = _make_archive(tmp_path)
    now = _fixed_now()
    recent = (now - timedelta(hours=2)).isoformat()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO chats VALUES ('-1001234567890', 'Test', 'supergroup', ?, 0)",
        (recent,),
    )
    # 4 сообщения в окне (больше DIGEST_MIN_MESSAGES_FOR_HOT=3).
    long_text = "Это тестовое сообщение с достаточной длиной для preview." * 2
    for i in range(4):
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, '999', ?, ?, NULL)",
            (str(100 + i), "-1001234567890", recent, long_text),
        )
    conn.commit()
    conn.close()

    builder = ChannelDigestBuilder(archive_path=db, now_fn=_fixed_now)
    md = builder.build_digest(source_chats=[-1001234567890], hours_back=24)

    assert "Hottest topics" in md
    assert "group_567890" in md  # псевдоним -1001234567890 → group_567890
    assert "4 сообщений" in md
    # Sample preview присутствует
    assert "тестовое сообщение" in md


def test_insights_from_response_feedback(tmp_path: Path) -> None:
    """positive_count > 0 → попадает в Insights."""
    db = _make_archive(tmp_path)
    now = _fixed_now()
    recent = (now - timedelta(minutes=30)).isoformat()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO chats VALUES ('-1009999999999', 'X', 'supergroup', ?, 0)",
        (recent,),
    )
    conn.execute(
        "INSERT INTO messages VALUES ('555', '-1009999999999', '777', ?, ?, NULL)",
        (recent, "Полезный ответ Краба про Phase 2 retrieval."),
    )
    conn.execute(
        "INSERT INTO response_feedback VALUES ('-1009999999999', '555', 3, 0, ?)",
        (recent,),
    )
    conn.commit()
    conn.close()

    builder = ChannelDigestBuilder(archive_path=db, now_fn=_fixed_now)
    md = builder.build_digest(source_chats=[-1009999999999], hours_back=24)

    assert "Insights" in md
    assert "👍 ×3" in md
    assert "Phase 2 retrieval" in md
    assert "group_999999" in md


def test_pseudonimize_masks_chat_ids() -> None:
    """Group/private chat_id корректно псевдонимизируются."""
    # Group / supergroup
    assert _pseudonimize("-1001234567890") == "group_567890"
    assert _pseudonimize("-100123456").startswith("group_")
    # Private (positive int) → private_<hex6>, без раскрытия id
    masked = _pseudonimize("123456789")
    assert masked.startswith("private_")
    assert "123456789" not in masked
    assert len(masked) == len("private_") + 6


def test_media_summaries_section(tmp_path: Path) -> None:
    """message_media_summaries попадают в секцию Voice & media."""
    db = _make_archive(tmp_path)
    now = _fixed_now()
    recent = (now - timedelta(minutes=10)).isoformat()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO chats VALUES ('-1005550000001', 'V', 'supergroup', ?, 0)",
        (recent,),
    )
    conn.execute(
        "INSERT INTO message_media_summaries VALUES "
        "('-1005550000001', '42', 'voice', "
        "'Голосовое: обсуждение релиза Phase 2.', 'gemini-3', ?)",
        (recent,),
    )
    conn.commit()
    conn.close()

    builder = ChannelDigestBuilder(archive_path=db, now_fn=_fixed_now)
    md = builder.build_digest(source_chats=[-1005550000001], hours_back=24)

    assert "Voice & media summaries" in md
    assert "[voice]" in md
    assert "обсуждение релиза" in md


def test_missing_archive_returns_empty(tmp_path: Path) -> None:
    """Несуществующий archive.db → пустая строка, без exceptions."""
    builder = ChannelDigestBuilder(archive_path=tmp_path / "nope.db", now_fn=_fixed_now)
    assert builder.build_digest(hours_back=24) == ""
