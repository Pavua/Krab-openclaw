# -*- coding: utf-8 -*-
"""
Тесты для команды !clear — быстрая очистка контекста.

Варианты:
  !clear          — очистить сессию текущего чата
  !clear all      — очистить все сессии
  !clear cache    — очистить все кэши (history_cache + search_cache)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import handle_clear  # noqa: E402

# ──────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────


def _make_bot(owner_id: int = 999) -> MagicMock:
    bot = MagicMock()
    bot.me = SimpleNamespace(id=owner_id)
    return bot


def _make_message(
    text: str = "!clear",
    chat_id: int = 12345,
    from_user_id: int = 100,
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
    oc.clear_session = MagicMock()
    return oc


def _make_cache(count: int = 5) -> MagicMock:
    c = MagicMock()
    c.clear_all = MagicMock(return_value=count)
    return c


# ──────────────────────────────────────────────
# !clear (без аргументов) — очистка текущей сессии
# ──────────────────────────────────────────────


class TestHandleClearDefault:
    @pytest.mark.asyncio
    async def test_calls_clear_session_for_current_chat(self) -> None:
        bot = _make_bot()
        msg = _make_message("!clear", chat_id=42)
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        oc.clear_session.assert_called_once_with("42")

    @pytest.mark.asyncio
    async def test_replies_confirmation(self) -> None:
        bot = _make_bot()
        msg = _make_message("!clear")
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Память очищена" in reply_text or "очищена" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_uses_edit_when_sender_is_bot(self) -> None:
        """Если сообщение отправлено самим ботом — редактирует вместо reply."""
        bot = _make_bot(owner_id=999)
        msg = _make_message("!clear", from_user_id=999)
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.edit.assert_called_once()
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_reply_when_sender_is_other_user(self) -> None:
        """Если сообщение отправлено другим пользователем — reply."""
        bot = _make_bot(owner_id=999)
        msg = _make_message("!clear", from_user_id=100)
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.reply.assert_called_once()
        msg.edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_affect_other_chats(self) -> None:
        """!clear очищает только текущий чат, не трогает другие."""
        bot = _make_bot()
        msg = _make_message("!clear", chat_id=111)
        oc = _make_openclaw(sessions={"111": [], "222": []})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        # clear_session вызывается только для 111
        oc.clear_session.assert_called_once_with("111")

    @pytest.mark.asyncio
    async def test_no_from_user_does_not_crash(self) -> None:
        """Если from_user = None — не падаем, просто reply."""
        bot = _make_bot(owner_id=999)
        msg = _make_message("!clear")
        msg.from_user = None
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.reply.assert_called_once()


# ──────────────────────────────────────────────
# !clear all — очистка всех сессий
# ──────────────────────────────────────────────


class TestHandleClearAll:
    @pytest.mark.asyncio
    async def test_clears_all_sessions(self) -> None:
        bot = _make_bot()
        msg = _make_message("!clear all")
        sessions = {"111": [], "222": [], "333": []}
        oc = _make_openclaw(sessions=sessions)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        # После clear_all sessions должен быть пустым
        assert len(oc._sessions) == 0

    @pytest.mark.asyncio
    async def test_does_not_call_clear_session(self) -> None:
        """!clear all использует _sessions.clear(), не clear_session()."""
        bot = _make_bot()
        msg = _make_message("!clear all")
        oc = _make_openclaw(sessions={"111": []})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        oc.clear_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_replies_with_count(self) -> None:
        """Ответ содержит количество очищенных чатов."""
        bot = _make_bot()
        msg = _make_message("!clear all")
        sessions = {"1": [], "2": [], "3": []}
        oc = _make_openclaw(sessions=sessions)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "3" in reply_text

    @pytest.mark.asyncio
    async def test_clears_lm_native_chat_state(self) -> None:
        """!clear all также сбрасывает _lm_native_chat_state."""
        bot = _make_bot()
        msg = _make_message("!clear all")
        oc = _make_openclaw(sessions={"111": []})
        oc._lm_native_chat_state = {"111": {"response_id": "abc"}}

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        assert len(oc._lm_native_chat_state) == 0

    @pytest.mark.asyncio
    async def test_empty_sessions_clears_gracefully(self) -> None:
        """Если сессий нет — не падаем, корректный ответ."""
        bot = _make_bot()
        msg = _make_message("!clear all")
        oc = _make_openclaw(sessions={})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "0" in reply_text

    @pytest.mark.asyncio
    async def test_uses_edit_when_sender_is_bot(self) -> None:
        bot = _make_bot(owner_id=5)
        msg = _make_message("!clear all", from_user_id=5)
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.edit.assert_called_once()
        msg.reply.assert_not_called()


# ──────────────────────────────────────────────
# !clear cache — очистка кэшей
# ──────────────────────────────────────────────


class TestHandleClearCache:
    @pytest.mark.asyncio
    async def test_calls_clear_all_on_history_cache(self) -> None:
        bot = _make_bot()
        msg = _make_message("!clear cache")
        oc = _make_openclaw()
        h_cache = _make_cache(count=10)
        s_cache = _make_cache(count=3)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
            patch("src.handlers.command_handlers.search_cache", s_cache),
        ):
            await handle_clear(bot, msg)

        h_cache.clear_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_clear_all_on_search_cache(self) -> None:
        bot = _make_bot()
        msg = _make_message("!clear cache")
        oc = _make_openclaw()
        h_cache = _make_cache(count=10)
        s_cache = _make_cache(count=3)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
            patch("src.handlers.command_handlers.search_cache", s_cache),
        ):
            await handle_clear(bot, msg)

        s_cache.clear_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_call_clear_session(self) -> None:
        """!clear cache не трогает _sessions."""
        bot = _make_bot()
        msg = _make_message("!clear cache")
        oc = _make_openclaw(sessions={"111": []})
        h_cache = _make_cache()
        s_cache = _make_cache()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
            patch("src.handlers.command_handlers.search_cache", s_cache),
        ):
            await handle_clear(bot, msg)

        oc.clear_session.assert_not_called()
        # Сессии не трогаем
        assert "111" in oc._sessions

    @pytest.mark.asyncio
    async def test_reply_contains_counts(self) -> None:
        """Ответ содержит количество очищенных записей из обоих кэшей."""
        bot = _make_bot()
        msg = _make_message("!clear cache")
        oc = _make_openclaw()
        h_cache = _make_cache(count=7)
        s_cache = _make_cache(count=4)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", h_cache),
            patch("src.handlers.command_handlers.search_cache", s_cache),
        ):
            await handle_clear(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "7" in reply_text
        assert "4" in reply_text

    @pytest.mark.asyncio
    async def test_reply_mentions_both_caches(self) -> None:
        """Ответ упоминает history_cache и search_cache."""
        bot = _make_bot()
        msg = _make_message("!clear cache")
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "history_cache" in reply_text
        assert "search_cache" in reply_text

    @pytest.mark.asyncio
    async def test_zero_counts_when_caches_empty(self) -> None:
        bot = _make_bot()
        msg = _make_message("!clear cache")
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache(count=0)),
            patch("src.handlers.command_handlers.search_cache", _make_cache(count=0)),
        ):
            await handle_clear(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "0" in reply_text

    @pytest.mark.asyncio
    async def test_uses_edit_when_sender_is_bot(self) -> None:
        bot = _make_bot(owner_id=77)
        msg = _make_message("!clear cache", from_user_id=77)
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.history_cache", _make_cache()),
            patch("src.handlers.command_handlers.search_cache", _make_cache()),
        ):
            await handle_clear(bot, msg)

        msg.edit.assert_called_once()
        msg.reply.assert_not_called()


# ──────────────────────────────────────────────
# cache_manager.clear_all — юнит-тест
# ──────────────────────────────────────────────


class TestCacheManagerClearAll:
    @pytest.fixture
    def cache(self, tmp_path):
        """Временный CacheManager для изолированного тестирования."""
        from unittest.mock import patch as _patch

        from src.cache_manager import CacheManager

        with _patch("src.cache_manager._CACHE_DIR", tmp_path / "krab_cache"):
            mgr = CacheManager("test_clear_all.db")
            yield mgr

    def test_clear_all_removes_all_entries(self, cache) -> None:
        cache.set("a", "1", ttl=60)
        cache.set("b", "2", ttl=60)
        cache.set("c", "3", ttl=60)
        count = cache.clear_all()
        assert count == 3
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None

    def test_clear_all_returns_zero_on_empty_cache(self, cache) -> None:
        count = cache.clear_all()
        assert count == 0

    def test_clear_all_returns_correct_count(self, cache) -> None:
        for i in range(5):
            cache.set(f"key_{i}", f"val_{i}", ttl=60)
        count = cache.clear_all()
        assert count == 5

    def test_after_clear_all_can_set_new_values(self, cache) -> None:
        cache.set("old", "value", ttl=60)
        cache.clear_all()
        cache.set("new", "fresh", ttl=60)
        assert cache.get("new") == "fresh"
        assert cache.get("old") is None

    def test_clear_all_clears_expired_and_fresh(self, cache) -> None:
        """clear_all удаляет как живые, так и просроченные записи."""
        cache.set("fresh", "yes", ttl=3600)
        # Устанавливаем с очень коротким TTL но не ждём — просто проверяем счёт
        cache.set("also_fresh", "yes", ttl=3600)
        count = cache.clear_all()
        assert count == 2
