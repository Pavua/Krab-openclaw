"""Тесты wire-up Idea 28+29 (sensitive chats + PII redactor) и Feature G
(auto-recluster trigger) в `memory_archive.add_message()`.

Покрывают:
  - no_archive level → silent skip (запись не появляется);
  - redact_only level → текст пропускается через redactor.redact();
  - normal chat → обычный archive без модификации текста;
  - отсутствие registry/redactor — graceful (no crash);
  - env flag KRAB_SENSITIVE_CHATS_ENABLED=0 отключает sensitivity-проверку;
  - auto-recluster: при достижении RECLUSTER_THRESHOLD (1000) вызывается callback
    и счётчик сбрасывается.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any
from unittest.mock import patch

import pytest

from src.core.memory_archive import (
    RECLUSTER_THRESHOLD,
    add_message,
    create_schema,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    create_schema(c)
    # chat-row, чтобы FK не блокировал insert messages.
    c.execute(
        "INSERT INTO chats (chat_id, title, chat_type) VALUES ('1', 'test', 'private');"
    )
    c.execute(
        "INSERT INTO chats (chat_id, title, chat_type) VALUES ('2', 'redact', 'private');"
    )
    c.execute(
        "INSERT INTO chats (chat_id, title, chat_type) VALUES ('3', 'normal', 'private');"
    )
    c.commit()
    yield c
    c.close()


class _FakeRegistry:
    """Тестовая sensitivity-регистратура с явными overrides."""

    def __init__(self, *, skip: set[str] | None = None, redact: set[str] | None = None) -> None:
        self.skip = skip or set()
        self.redact = redact or set()

    def should_skip_archive(self, chat_id: Any) -> bool:
        return str(chat_id) in self.skip

    def should_redact(self, chat_id: Any) -> bool:
        return str(chat_id) in self.redact


class _FakeRedactor:
    """Простой redactor — заменяет 'SECRET' на '<REDACTED>'."""

    def redact(self, text: str) -> str:
        return text.replace("SECRET", "<REDACTED>")


def _count_messages(conn: sqlite3.Connection, chat_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id = ?;", (chat_id,)
    ).fetchone()[0]


def test_no_archive_level_skips_silent(conn: sqlite3.Connection) -> None:
    """Чат с level=no_archive → запись не попадает в БД."""
    registry = _FakeRegistry(skip={"1"})
    result = add_message(
        conn,
        chat_id="1",
        message_id="m1",
        text="hello SECRET",
        sensitivity_registry=registry,
        redactor=_FakeRedactor(),
    )
    assert result == "skipped_sensitive"
    assert _count_messages(conn, "1") == 0


def test_redact_only_level_redacts_text(conn: sqlite3.Connection) -> None:
    """Чат с level=redact_only → текст пропускается через redactor."""
    registry = _FakeRegistry(redact={"2"})
    result = add_message(
        conn,
        chat_id="2",
        message_id="m1",
        text="hello SECRET data",
        sensitivity_registry=registry,
        redactor=_FakeRedactor(),
    )
    assert result == "archived"
    row = conn.execute(
        "SELECT text_redacted FROM messages WHERE chat_id = '2' AND message_id = 'm1';"
    ).fetchone()
    assert row[0] == "hello <REDACTED> data"


def test_normal_chat_passes_unchanged(conn: sqlite3.Connection) -> None:
    """Обычный чат — текст не трогаем, обычный insert."""
    registry = _FakeRegistry()  # ни skip, ни redact
    result = add_message(
        conn,
        chat_id="3",
        message_id="m1",
        text="plain SECRET text",
        sensitivity_registry=registry,
        redactor=_FakeRedactor(),
    )
    assert result == "archived"
    row = conn.execute(
        "SELECT text_redacted FROM messages WHERE chat_id = '3' AND message_id = 'm1';"
    ).fetchone()
    # Текст НЕ редактируется, т.к. should_redact вернул False.
    assert row[0] == "plain SECRET text"


def test_missing_registry_and_redactor_graceful(conn: sqlite3.Connection) -> None:
    """Без registry и redactor — обычный путь, без падений."""
    result = add_message(
        conn,
        chat_id="3",
        message_id="m2",
        text="just text",
    )
    assert result == "archived"
    assert _count_messages(conn, "3") == 1


def test_env_flag_disabled_skips_sensitivity_check(conn: sqlite3.Connection) -> None:
    """KRAB_SENSITIVE_CHATS_ENABLED=0 → registry полностью игнорируется."""
    registry = _FakeRegistry(skip={"1"})
    with patch.dict(os.environ, {"KRAB_SENSITIVE_CHATS_ENABLED": "0"}):
        result = add_message(
            conn,
            chat_id="1",
            message_id="m_envoff",
            text="hello",
            sensitivity_registry=registry,
        )
    assert result == "archived"
    assert _count_messages(conn, "1") == 1


def test_recluster_callback_triggered_at_threshold(conn: sqlite3.Connection) -> None:
    """При достижении RECLUSTER_THRESHOLD callback вызывается, счётчик сбрасывается."""
    calls: list[int] = []

    def _cb(c: sqlite3.Connection) -> None:
        calls.append(1)

    # Заполняем до THRESHOLD-1: callback не должен сработать.
    for i in range(RECLUSTER_THRESHOLD - 1):
        add_message(
            conn,
            chat_id="3",
            message_id=f"r{i}",
            text="x",
            recluster_callback=_cb,
        )
    assert calls == []

    # THRESHOLD-е сообщение — callback должен сработать.
    add_message(
        conn,
        chat_id="3",
        message_id=f"r{RECLUSTER_THRESHOLD - 1}",
        text="x",
        recluster_callback=_cb,
    )
    assert len(calls) == 1

    # Счётчик сброшен — следующий insert не триггерит ещё раз.
    add_message(
        conn,
        chat_id="3",
        message_id="r_after",
        text="x",
        recluster_callback=_cb,
    )
    assert len(calls) == 1
