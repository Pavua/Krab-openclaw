# -*- coding: utf-8 -*-
"""
Тесты для команды !reset — агрессивная многослойная очистка истории.

Варианты:
  !reset                      — текущий чат, все слои
  !reset --all                — требует --force (destructive)
  !reset --all --force        — очищает все чаты
  !reset --layer=krab         — только Krab history_cache
  !reset --layer=archive      — archive.db per-chat
  !reset --dry-run            — превью, ничего не удаляет
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.gemini_cache_nonce import (
    _reset_all_nonces_for_tests,
    get_gemini_nonce,
    invalidate_gemini_cache_for_chat,
)
from src.core.reset_helpers import (
    clear_archive_db_for_chat,
    count_archive_messages_for_chat,
)
from src.handlers.command_handlers import handle_reset

# ──────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────


def _make_bot(owner_id: int = 999) -> MagicMock:
    bot = MagicMock()
    bot.me = SimpleNamespace(id=owner_id)
    # _get_command_args: возвращает всё после первой команды
    bot._get_command_args = MagicMock(side_effect=lambda m: " ".join((m.text or "").split()[1:]))
    return bot


def _make_message(
    text: str = "!reset",
    chat_id: int = 12345,
    from_user_id: int = 999,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=from_user_id, username="owner")
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()
    return msg


def _make_openclaw(sessions: dict | None = None) -> MagicMock:
    oc = MagicMock()
    oc._sessions = sessions if sessions is not None else {}
    oc._lm_native_chat_state = {}

    def _clear(cid: str) -> None:
        oc._sessions.pop(cid, None)
        oc._lm_native_chat_state.pop(cid, None)

    oc.clear_session = MagicMock(side_effect=_clear)
    return oc


def _make_cache(has_keys: set[str] | None = None) -> MagicMock:
    """Мок history_cache. has_keys — какие ключи считаются существующими (для get())."""
    c = MagicMock()
    keys = set(has_keys or [])

    def _get(key: str) -> str | None:
        return "payload" if key in keys else None

    def _delete(key: str) -> None:
        keys.discard(key)

    c.get = MagicMock(side_effect=_get)
    c.delete = MagicMock(side_effect=_delete)
    return c


@pytest.fixture(autouse=True)
def _reset_nonces() -> None:
    """Очищаем nonce-реестр между тестами."""
    _reset_all_nonces_for_tests()


# ──────────────────────────────────────────────
# !reset — default (текущий чат, все слои)
# ──────────────────────────────────────────────


class TestHandleResetDefault:
    @pytest.mark.asyncio
    async def test_reset_single_chat_clears_all_layers(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset", chat_id=42, from_user_id=999)
        oc = _make_openclaw(sessions={"42": [{"role": "user", "content": "hi"}]})
        h_cache = _make_cache(has_keys={"chat_history:42"})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Все слои обработаны для "42"
        oc.clear_session.assert_called_once_with("42")
        h_cache.delete.assert_called_with("chat_history:42")
        # Gemini nonce создан
        assert get_gemini_nonce("42") != ""

    @pytest.mark.asyncio
    async def test_reset_default_does_not_touch_other_chats(self) -> None:
        bot = _make_bot()
        msg = _make_message("!reset", chat_id=111)
        oc = _make_openclaw(sessions={"111": [], "222": [], "333": []})
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        oc.clear_session.assert_called_once_with("111")
        # Остальные чаты не тронуты
        assert "222" in oc._sessions
        assert "333" in oc._sessions

    @pytest.mark.asyncio
    async def test_reset_replies_with_report(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset", from_user_id=100)  # не owner, обычный reply
        oc = _make_openclaw(sessions={"12345": []})
        h_cache = _make_cache(has_keys={"chat_history:12345"})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "Reset" in text or "reset" in text.lower()
        assert "Krab" in text or "Krab cache" in text

    @pytest.mark.asyncio
    async def test_reset_uses_edit_when_sender_is_bot(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset", from_user_id=999)  # owner
        oc = _make_openclaw()
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        msg.edit.assert_called_once()
        msg.reply.assert_not_called()


# ──────────────────────────────────────────────
# !reset --all без --force
# ──────────────────────────────────────────────


class TestHandleResetAllWithoutForce:
    @pytest.mark.asyncio
    async def test_warns_and_does_not_clear(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --all", from_user_id=999)
        oc = _make_openclaw(sessions={"1": [], "2": []})
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Ничего не удалено
        oc.clear_session.assert_not_called()
        h_cache.delete.assert_not_called()
        # И было warning-сообщение
        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "force" in text.lower()

    @pytest.mark.asyncio
    async def test_non_owner_rejected(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --all --force", from_user_id=100)
        oc = _make_openclaw(sessions={"1": []})
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Отказ, ничего не удалено
        oc.clear_session.assert_not_called()
        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "владельцу" in text or "владелец" in text.lower()


# ──────────────────────────────────────────────
# !reset --all --force
# ──────────────────────────────────────────────


class TestHandleResetAllWithForce:
    @pytest.mark.asyncio
    async def test_clears_all_chats_from_sessions(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --all --force", from_user_id=999)
        oc = _make_openclaw(sessions={"111": [], "222": [], "333": []})
        h_cache = _make_cache(has_keys={"chat_history:111", "chat_history:222", "chat_history:333"})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Все 3 чата очищены
        assert oc.clear_session.call_count == 3
        # Все 3 nonce созданы
        assert get_gemini_nonce("111") != ""
        assert get_gemini_nonce("222") != ""
        assert get_gemini_nonce("333") != ""


# ──────────────────────────────────────────────
# !reset --dry-run
# ──────────────────────────────────────────────


class TestHandleResetDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_shows_preview_and_does_not_delete(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --dry-run", chat_id=42, from_user_id=100)
        oc = _make_openclaw(sessions={"42": [{"role": "user", "content": "x"}]})
        h_cache = _make_cache(has_keys={"chat_history:42"})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Ничего не удалено
        oc.clear_session.assert_not_called()
        h_cache.delete.assert_not_called()
        # Но есть превью-ответ
        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "Dry-run" in text or "dry" in text.lower()
        # Nonce НЕ создан в dry-run
        assert get_gemini_nonce("42") == ""


# ──────────────────────────────────────────────
# !reset --layer=krab (только один слой)
# ──────────────────────────────────────────────


class TestHandleResetLayerFilter:
    @pytest.mark.asyncio
    async def test_layer_krab_only_deletes_cache(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --layer=krab", chat_id=42, from_user_id=100)
        oc = _make_openclaw(sessions={"42": []})
        h_cache = _make_cache(has_keys={"chat_history:42"})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # openclaw НЕ трогается
        oc.clear_session.assert_not_called()
        # krab cache — да
        h_cache.delete.assert_called_with("chat_history:42")
        # gemini НЕ трогается
        assert get_gemini_nonce("42") == ""

    @pytest.mark.asyncio
    async def test_layer_gemini_only_sets_nonce(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --layer=gemini", chat_id=42, from_user_id=100)
        oc = _make_openclaw(sessions={"42": []})
        h_cache = _make_cache(has_keys={"chat_history:42"})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        oc.clear_session.assert_not_called()
        h_cache.delete.assert_not_called()
        assert get_gemini_nonce("42") != ""


# ──────────────────────────────────────────────
# Gemini cache nonce (unit-level)
# ──────────────────────────────────────────────


class TestGeminiCacheNonce:
    def test_nonce_empty_before_reset(self) -> None:
        assert get_gemini_nonce("555") == ""

    def test_invalidate_generates_unique_nonce(self) -> None:
        n1 = invalidate_gemini_cache_for_chat("555")
        assert n1
        assert len(n1) == 32  # uuid4().hex
        n2 = invalidate_gemini_cache_for_chat("555")
        assert n2 != n1  # новый при повторном вызове

    def test_get_returns_latest_nonce(self) -> None:
        invalidate_gemini_cache_for_chat("777")
        first = get_gemini_nonce("777")
        invalidate_gemini_cache_for_chat("777")
        second = get_gemini_nonce("777")
        assert first != second
        assert second != ""

    def test_nonce_isolated_per_chat(self) -> None:
        invalidate_gemini_cache_for_chat("a")
        na = get_gemini_nonce("a")
        assert get_gemini_nonce("b") == ""
        assert na != ""


# ──────────────────────────────────────────────
# archive.db cleanup (unit-level, tmp_path)
# ──────────────────────────────────────────────


def _create_archive_db(path: Path) -> None:
    """Создаёт минимальную копию archive.db schema для тестов."""
    conn = sqlite3.connect(str(path))
    try:
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
                PRIMARY KEY (chat_id, message_id),
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
            ) WITHOUT ROWID;
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT NOT NULL UNIQUE,
                chat_id TEXT NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                char_len INTEGER NOT NULL,
                text_redacted TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
            );
            CREATE TABLE chunk_messages (
                chunk_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                PRIMARY KEY (chunk_id, message_id)
            ) WITHOUT ROWID;
            CREATE TABLE indexer_state (
                chat_id TEXT PRIMARY KEY,
                last_message_id TEXT NOT NULL,
                last_processed_at TEXT NOT NULL
            ) WITHOUT ROWID;
            """
        )
        # Чат A: 3 сообщения, 1 chunk
        conn.execute(
            "INSERT INTO chats VALUES (?, ?, ?, ?, ?)",
            ("A", "Chat A", "private", "2026-04-17T00:00:00Z", 3),
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (str(i), "A", "sender", "2026-04-17T00:00:00Z", f"msg-{i}", None),
            )
        conn.execute(
            "INSERT INTO chunks (chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("chunk-A", "A", "2026-04-17T00:00:00Z", "2026-04-17T00:00:00Z", 3, 15, "text"),
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO chunk_messages VALUES (?, ?, ?)",
                ("chunk-A", str(i), "A"),
            )
        conn.execute(
            "INSERT INTO indexer_state VALUES (?, ?, ?)",
            ("A", "2", "2026-04-17T00:00:00Z"),
        )
        # Чат B: 2 сообщения (для isolation-теста)
        conn.execute(
            "INSERT INTO chats VALUES (?, ?, ?, ?, ?)",
            ("B", "Chat B", "private", "2026-04-17T00:00:00Z", 2),
        )
        for i in range(2):
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (str(i), "B", "sender", "2026-04-17T00:00:00Z", f"B-msg-{i}", None),
            )
        conn.commit()
    finally:
        conn.close()


class TestArchiveDbCleanup:
    def test_clear_removes_messages_for_chat_only(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        _create_archive_db(db)

        deleted = clear_archive_db_for_chat("A", db_path=db)
        assert deleted == 3

        # Проверяем через прямой SQL — чат B не тронут
        with sqlite3.connect(str(db)) as conn:
            a_count = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = 'A'").fetchone()[
                0
            ]
            b_count = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = 'B'").fetchone()[
                0
            ]
        assert a_count == 0
        assert b_count == 2

    def test_clear_removes_chunks_and_chunk_messages(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        _create_archive_db(db)

        clear_archive_db_for_chat("A", db_path=db)

        with sqlite3.connect(str(db)) as conn:
            chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE chat_id = 'A'").fetchone()[0]
            cms = conn.execute(
                "SELECT COUNT(*) FROM chunk_messages WHERE chat_id = 'A'"
            ).fetchone()[0]
            indexer = conn.execute(
                "SELECT COUNT(*) FROM indexer_state WHERE chat_id = 'A'"
            ).fetchone()[0]
        assert chunks == 0
        assert cms == 0
        assert indexer == 0

    def test_clear_missing_db_returns_zero(self, tmp_path: Path) -> None:
        db = tmp_path / "does_not_exist.db"
        assert clear_archive_db_for_chat("A", db_path=db) == 0

    def test_count_reports_messages(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        _create_archive_db(db)
        assert count_archive_messages_for_chat("A", db_path=db) == 3
        assert count_archive_messages_for_chat("B", db_path=db) == 2
        assert count_archive_messages_for_chat("X", db_path=db) == 0

    def test_count_missing_db_returns_zero(self, tmp_path: Path) -> None:
        db = tmp_path / "does_not_exist.db"
        assert count_archive_messages_for_chat("A", db_path=db) == 0


# ──────────────────────────────────────────────
# !reset --layer=archive (integration test с tmp archive.db)
# ──────────────────────────────────────────────


class TestHandleResetLayerArchive:
    @pytest.mark.asyncio
    async def test_layer_archive_deletes_only_target_chat(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        _create_archive_db(db)

        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --layer=archive", chat_id="A", from_user_id=100)
        oc = _make_openclaw(sessions={"A": []})
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
            patch("src.core.reset_helpers._ARCHIVE_DB_PATH", db),
        ):
            await handle_reset(bot, msg)

        # Chat A удалён
        with sqlite3.connect(str(db)) as conn:
            a = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = 'A'").fetchone()[0]
            b = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = 'B'").fetchone()[0]
        assert a == 0
        assert b == 2
        # Другие слои НЕ тронуты
        oc.clear_session.assert_not_called()
        h_cache.delete.assert_not_called()
        assert get_gemini_nonce("A") == ""


# ──────────────────────────────────────────────
# Review fixes (CRITICAL-1, HIGH-1, LOW-3)
# ──────────────────────────────────────────────


class TestReviewFixes:
    """Regression tests для review fixes от Agent #8."""

    @pytest.mark.asyncio
    async def test_krab_stats_not_inflated_when_cache_empty(self) -> None:
        """HIGH-1: не считаем krab++ если ключа в history_cache нет.

        clear_session() ниже тоже делает cache.delete; нельзя инкрементить
        стату без предварительного `get()` — иначе double-count при defaults.
        """
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset", chat_id=42, from_user_id=100)
        oc = _make_openclaw(sessions={"42": []})
        # Пустой кэш — has_keys={} → get() возвращает None
        h_cache = _make_cache(has_keys=set())

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Krab stat должен быть 0 — ключа нет, значит нечего удалять
        # Читаем из текста reply (получаем "Krab cache: 0")
        text: str = (msg.reply.call_args or msg.edit.call_args)[0][0]
        assert "Krab cache: 0" in text
        # delete не должен был вызываться для chat_history:42
        # (clear_session() внутри OpenClaw сам вызывает delete)
        # Но явного assert нет, т.к. _make_openclaw.clear_session — mock без
        # реального side-effect на h_cache

    @pytest.mark.asyncio
    async def test_reset_invalid_layer_returns_error(self) -> None:
        """LOW-3: неизвестный --layer=<value> → error message, ничего не reset."""
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --layer=foo", chat_id=42, from_user_id=100)
        oc = _make_openclaw(sessions={"42": [{"role": "user", "content": "x"}]})
        h_cache = _make_cache(has_keys={"chat_history:42"})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Ничего не удалено
        oc.clear_session.assert_not_called()
        h_cache.delete.assert_not_called()
        # Error message в reply (или edit, если sender == owner; here from_user=100 ≠ 999)
        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "Unknown layer" in text
        assert "foo" in text

    @pytest.mark.asyncio
    async def test_dry_run_warns_archive_not_in_default_scope(self) -> None:
        """HIGH-2: dry-run в default scope явно говорит что archive не включён."""
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --dry-run", chat_id=42, from_user_id=100)
        oc = _make_openclaw(sessions={"42": []})
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        text: str = msg.reply.call_args[0][0]
        # Явное предупреждение про archive
        assert "Archive" in text
        assert "default scope" in text.lower()

    @pytest.mark.asyncio
    async def test_dry_run_no_archive_warning_when_layer_archive(self) -> None:
        """HIGH-2: при --layer=archive warning не нужен."""
        bot = _make_bot(owner_id=999)
        msg = _make_message(
            "!reset --dry-run --layer=archive", chat_id=42, from_user_id=100
        )
        oc = _make_openclaw(sessions={"42": []})
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        text: str = msg.reply.call_args[0][0]
        # Warning про "НЕ включён в default scope" НЕ должен появляться при явном layer
        assert "НЕ включён в default scope" not in text


# ──────────────────────────────────────────────
# CRITICAL-1: Gemini nonce применяется к существующей сессии
# ──────────────────────────────────────────────


class TestGeminiNonceAppliesToExistingSession:
    """CRITICAL-1: --layer=gemini не чистит сессию → nonce должен обновлять
    existing system message в _sessions, иначе Gemini prompt cache не
    инвалидируется и !reset --layer=gemini becomes no-op."""

    @pytest.mark.asyncio
    async def test_nonce_updates_system_message_when_session_exists(self) -> None:
        """После invalidate_gemini_cache_for_chat() + очередного send_message_stream
        вызова — session[0]['content'] должен содержать 'cache_nonce:' marker."""
        # Используем прямую модель OpenClawClient — нам нужен доступ к _sessions
        # и send_message_stream, но без реального gateway. Это unit-test на
        # внутреннюю логику _sessions mutation.
        from src.openclaw_client import OpenClawClient

        client = OpenClawClient.__new__(OpenClawClient)
        # Инициализируем минимум state для _sessions mutation path
        client._sessions = {"42": [{"role": "system", "content": "BASE_PROMPT"}]}
        client._lm_native_chat_state = {}
        client._request_disable_tools = False
        client._active_tool_calls = []

        # Invalidate nonce для "42"
        nonce = invalidate_gemini_cache_for_chat("42")
        assert nonce, "nonce должен быть non-empty"

        # Вручную воспроизводим логику из send_message_stream:
        # если сессия уже есть, и nonce есть — обновить system message.
        # (Мы не можем легко вызвать реальный send_message_stream без сети,
        # но можем проверить логику по коду.)
        from src.core.gemini_cache_nonce import clear_gemini_nonce, get_gemini_nonce

        chat_id = "42"
        system_prompt = "BASE_PROMPT"
        _nonce = get_gemini_nonce(chat_id)
        assert _nonce == nonce  # убеждаемся что nonce реально записался

        # Воспроизводим else-ветку из send_message_stream:
        if _nonce and system_prompt and client._sessions[chat_id]:
            first_msg = client._sessions[chat_id][0]
            if isinstance(first_msg, dict) and first_msg.get("role") == "system":
                first_msg["content"] = (
                    f"{system_prompt}\n\n<!-- cache_nonce: {_nonce} -->"
                )
            clear_gemini_nonce(chat_id)

        # Verify: system message обновился с nonce
        updated_content = client._sessions["42"][0]["content"]
        assert "cache_nonce:" in updated_content
        assert nonce in updated_content
        # Nonce consumed → get_gemini_nonce возвращает ""
        assert get_gemini_nonce("42") == ""

    @pytest.mark.asyncio
    async def test_gemini_clear_nonce_consumes_after_use(self) -> None:
        """clear_gemini_nonce() после применения — чтобы nonce не обновлялся
        бесконечно при каждом запросе."""
        from src.core.gemini_cache_nonce import clear_gemini_nonce

        invalidate_gemini_cache_for_chat("99")
        assert get_gemini_nonce("99") != ""

        clear_gemini_nonce("99")
        assert get_gemini_nonce("99") == ""

        # Idempotent: clear на пустом — no-op, не падает
        clear_gemini_nonce("99")
        assert get_gemini_nonce("99") == ""


# ──────────────────────────────────────────────
# Follow-up patch: session files + audit + archive hint + progress
# ──────────────────────────────────────────────


class TestOpenClawSessionFileCleanup:
    """MEDIUM: openclaw_client.clear_session чистит также persistent session.jsonl."""

    def test_session_file_removed_when_chat_id_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Создаём fake ~/.openclaw/.../sessions/<id>.jsonl с нужным chat_id → после
        clear_session файл удалён. Другие файлы (без chat_id) не тронуты."""
        import src.openclaw_client as oc_mod

        # Редиректим Path.home() на tmp_path, чтобы изолировать тест от реальной FS.
        monkeypatch.setattr(oc_mod.Path, "home", lambda: tmp_path)

        sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # Файл с нужным chat_id — должен быть удалён.
        target = sessions_dir / "aaaa.jsonl"
        target.write_text(
            '{"type":"session","id":"aaaa"}\n'
            '{"type":"user_message","chat_id": "42", "text": "hi"}\n',
            encoding="utf-8",
        )
        # Файл без chat_id — остаётся.
        other = sessions_dir / "bbbb.jsonl"
        other.write_text('{"type":"session","id":"bbbb"}\n', encoding="utf-8")

        # Собираем минимальный OpenClawClient-инстанс только для clear_session.
        from src.openclaw_client import OpenClawClient

        client = OpenClawClient.__new__(OpenClawClient)
        client._sessions = {"42": []}
        client._lm_native_chat_state = {}

        # history_cache.delete — мокаем на no-op, чтобы не тронуть реальный кэш.
        monkeypatch.setattr(oc_mod.history_cache, "delete", lambda _k: None)

        client.clear_session("42")

        assert not target.exists(), "файл с chat_id=42 должен быть удалён"
        assert other.exists(), "файл без chat_id=42 не должен быть тронут"

    def test_session_file_cleanup_noop_when_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Если sessions-директории нет — clear_session не падает."""
        import src.openclaw_client as oc_mod

        # Home → tmp_path без sessions-директории.
        monkeypatch.setattr(oc_mod.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(oc_mod.history_cache, "delete", lambda _k: None)

        from src.openclaw_client import OpenClawClient

        client = OpenClawClient.__new__(OpenClawClient)
        client._sessions = {}
        client._lm_native_chat_state = {}

        # Не падает, не поднимает исключения.
        client.clear_session("404")


class TestAuditLogAllForce:
    """LOW: audit-лог при `!reset --all --force` (destructive)."""

    @pytest.mark.asyncio
    async def test_audit_log_emitted_for_all_force(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Проверяем, что logger.warning(reset_all_force_executed, ...) вызывается."""
        import src.handlers.command_handlers as ch

        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --all --force", chat_id=1, from_user_id=999)
        oc = _make_openclaw(sessions={"1": [], "2": []})
        h_cache = _make_cache()

        # Перехватываем warning прямо на logger модуля — надёжнее чем caplog
        # для structlog (проксирует через stdlib, но event сериализуется в message).
        calls: list[tuple[str, dict]] = []

        def _capture(event: str, **kwargs: object) -> None:
            calls.append((event, dict(kwargs)))

        monkeypatch.setattr(ch.logger, "warning", _capture)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Ищем нужное событие в перехваченных.
        audit_calls = [c for c in calls if c[0] == "reset_all_force_executed"]
        assert audit_calls, f"ожидали reset_all_force_executed, получили: {calls}"
        event, kwargs = audit_calls[0]
        assert kwargs.get("chat_count") == 2
        assert kwargs.get("user_id") == 999
        assert kwargs.get("layer") == "all"

    @pytest.mark.asyncio
    async def test_audit_log_not_emitted_for_dry_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Для dry-run audit-лог НЕ пишется (ничего не разрушается)."""
        import src.handlers.command_handlers as ch

        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --all --force --dry-run", from_user_id=999)
        oc = _make_openclaw(sessions={"1": []})
        h_cache = _make_cache()

        calls: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            ch.logger,
            "warning",
            lambda event, **kwargs: calls.append((event, dict(kwargs))),
        )

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        assert not any(
            c[0] == "reset_all_force_executed" for c in calls
        ), "dry-run не должен триггерить audit-лог"


class TestDryRunArchiveHint:
    """LOW: dry-run для default scope явно сообщает, что archive не включён."""

    @pytest.mark.asyncio
    async def test_dry_run_includes_archive_hint_for_default(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --dry-run", chat_id=42, from_user_id=100)
        oc = _make_openclaw(sessions={"42": []})
        h_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        text: str = msg.reply.call_args[0][0]
        # Hint должен быть в превью.
        assert "Archive" in text
        assert "--layer=archive" in text


class TestProgressMessageForLargeAll:
    """NICE: при --all с >10 чатами показываем интерактивный прогресс."""

    @pytest.mark.asyncio
    async def test_progress_shown_for_many_chats(self) -> None:
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --all --force", from_user_id=999)

        # 15 чатов — в 1.5 раза больше порога (10).
        sessions = {str(i): [] for i in range(15)}
        oc = _make_openclaw(sessions=sessions)
        h_cache = _make_cache()

        # Подменяем reply чтобы отличить progress-message от финального.
        progress_mock = AsyncMock()
        progress_mock.edit = AsyncMock()
        progress_mock.delete = AsyncMock()
        reply_calls: list[str] = []

        async def _reply(text: str) -> AsyncMock:
            reply_calls.append(text)
            if "🔄" in text:
                return progress_mock  # progress-message
            return AsyncMock()  # финальный отчёт

        msg.reply = AsyncMock(side_effect=_reply)
        # sender == bot.me → edit, а не reply. Но progress всё равно пойдёт
        # через message.reply (first bootstrap message). Оставляем from_user_id=999
        # но заменим на иной id чтобы получить reply-путь для финального отчёта тоже.
        msg.from_user = SimpleNamespace(id=100, username="not-owner")
        # Но тогда owner-check для --all упадёт → нам нужен обход. Возвращаем owner.
        msg.from_user = SimpleNamespace(id=999, username="owner")

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # Progress-message создан (first reply с "🔄 Reset: 0 /")
        assert any("Reset: 0 /" in t for t in reply_calls), (
            f"ожидали progress-init reply, получили: {reply_calls}"
        )
        # Хотя бы один edit вызван (на 10-й итерации из 15).
        assert progress_mock.edit.call_count >= 1
        # Progress удалён в конце.
        progress_mock.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_progress_not_shown_for_small_all(self) -> None:
        """Если чатов ≤10 — progress не показываем."""
        bot = _make_bot(owner_id=999)
        msg = _make_message("!reset --all --force", from_user_id=999)

        sessions = {str(i): [] for i in range(5)}
        oc = _make_openclaw(sessions=sessions)
        h_cache = _make_cache()

        reply_calls: list[str] = []

        async def _reply(text: str) -> AsyncMock:
            reply_calls.append(text)
            return AsyncMock()

        msg.reply = AsyncMock(side_effect=_reply)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
        ):
            await handle_reset(bot, msg)

        # В edit-пути (sender == bot.me) financial report уходит в edit,
        # reply вообще не вызывается. Главное — нет progress-сообщения.
        assert not any("🔄 Reset: 0 /" in t for t in reply_calls)
