# -*- coding: utf-8 -*-
"""
Тесты извлечения memory_commands (Phase 2 Wave 5, Session 27).

Покрываем:
1)  !remember без аргумента → UserInputError
2)  !remember <текст> safe → save через memory_validator + workspace + vector
3)  !remember <текст> unsafe → блокирующий warn
4)  !recall без аргумента → UserInputError
5)  !recall <запрос> → агрегация workspace + vector + memory_layer
6)  !mem help / без аргументов → справка
7)  !mem <запрос> → диспатч в _mem_search
8)  !quote без аргументов → встроенная цитата
9)  !quote save без reply → подсказка
10) !tag list (пусто) → информативный reply
11) !tag <тег> без reply → UserInputError
12) _make_msg_link для супергрупп
13) _format_memory_layer_section пусто/непусто
14) _mem_truncate усекает с эллипсисом
15) TestReExports — сверяем re-exports через command_handlers
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import UserInputError
from src.handlers.commands import memory_commands as mc


def _make_message(text: str, *, reply_to=None, chat_id: int = -1001000000001) -> SimpleNamespace:
    """Минимальный stub Pyrogram Message с текстом и async reply."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(),
        reply_to_message=reply_to,
        from_user=SimpleNamespace(id=42, username="tester"),
    )


def _make_bot(args: str = "") -> MagicMock:
    bot = MagicMock()
    bot._get_command_args.return_value = args
    return bot


# ---------------------------------------------------------------------------
# !remember
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_without_args_raises():
    bot = _make_bot(args="")
    msg = _make_message("!remember")
    with pytest.raises(UserInputError):
        await mc.handle_remember(bot, msg)


@pytest.mark.asyncio
async def test_remember_saves_when_safe(monkeypatch):
    bot = _make_bot(args="hello world")
    msg = _make_message("!remember hello world")

    monkeypatch.setattr(mc.memory_validator, "stage", lambda *a, **kw: (True, "", None))
    monkeypatch.setattr(mc, "append_workspace_memory_entry", lambda *a, **kw: True)
    monkeypatch.setattr(mc.memory_manager, "save_fact", lambda txt: True)

    await mc.handle_remember(bot, msg)
    msg.reply.assert_awaited_once()
    reply_text = msg.reply.await_args.args[0]
    assert "Запомнил" in reply_text


@pytest.mark.asyncio
async def test_remember_blocked_when_unsafe(monkeypatch):
    bot = _make_bot(args="dangerous instr")
    msg = _make_message("!remember dangerous instr")

    monkeypatch.setattr(
        mc.memory_validator, "stage", lambda *a, **kw: (False, "⚠ заблокировано", None)
    )

    await mc.handle_remember(bot, msg)
    msg.reply.assert_awaited_once_with("⚠ заблокировано")


# ---------------------------------------------------------------------------
# !recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_without_args_raises():
    bot = _make_bot(args="")
    msg = _make_message("!recall")
    with pytest.raises(UserInputError):
        await mc.handle_recall(bot, msg)


@pytest.mark.asyncio
async def test_recall_aggregates_sources(monkeypatch):
    bot = _make_bot(args="krab")
    msg = _make_message("!recall krab")

    monkeypatch.setattr(mc, "recall_workspace_memory", lambda q: "ws-fact")
    monkeypatch.setattr(mc.memory_manager, "recall", lambda q: "vec-fact")

    async def fake_layer(q, limit=5):
        return [{"text": "layer-fact", "score": 0.5, "mode": "hybrid"}]

    monkeypatch.setattr(mc, "_recall_memory_layer", fake_layer)

    await mc.handle_recall(bot, msg)
    reply_text = msg.reply.await_args.args[0]
    assert "Вспомнил" in reply_text
    assert "ws-fact" in reply_text
    assert "vec-fact" in reply_text
    assert "layer-fact" in reply_text


# ---------------------------------------------------------------------------
# !mem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mem_help_default():
    bot = _make_bot()
    msg = _make_message("!mem")

    with patch("src.core.command_registry.bump_command"):
        await mc.handle_mem(bot, msg)
    msg.reply.assert_awaited_once()
    assert "!mem" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_mem_search_dispatches(monkeypatch):
    bot = _make_bot()
    msg = _make_message("!mem hello world")

    called = {}

    async def fake_search(message, query):
        called["query"] = query

    monkeypatch.setattr(mc, "_mem_search", fake_search)
    with patch("src.core.command_registry.bump_command"):
        await mc.handle_mem(bot, msg)

    assert called.get("query") == "hello world"


# ---------------------------------------------------------------------------
# !quote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quote_no_args_returns_builtin():
    bot = _make_bot(args="")
    msg = _make_message("!quote")
    await mc.handle_quote(bot, msg)
    msg.reply.assert_awaited_once()
    assert msg.reply.await_args.args[0].startswith("💬")


@pytest.mark.asyncio
async def test_quote_save_without_reply():
    bot = _make_bot(args="save")
    msg = _make_message("!quote save", reply_to=None)
    await mc.handle_quote(bot, msg)
    msg.reply.assert_awaited_once()
    assert "Ответь" in msg.reply.await_args.args[0]


# ---------------------------------------------------------------------------
# !tag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_list_empty(monkeypatch):
    bot = _make_bot(args="list")
    msg = _make_message("!tag list")
    monkeypatch.setattr(mc, "_load_tags", lambda: {})
    await mc.handle_tag(bot, msg)
    msg.reply.assert_awaited_once()
    assert "Тегов нет" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_tag_add_without_reply_raises(monkeypatch):
    bot = _make_bot(args="todo")
    msg = _make_message("!tag todo", reply_to=None)
    monkeypatch.setattr(mc, "_load_tags", lambda: {})
    with pytest.raises(UserInputError):
        await mc.handle_tag(bot, msg)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_make_msg_link_supergroup():
    link = mc._make_msg_link(-1001234567890, 42)
    assert link == "https://t.me/c/1234567890/42"


def test_format_memory_layer_section_empty():
    assert mc._format_memory_layer_section([]) == ""


def test_format_memory_layer_section_basic():
    out = mc._format_memory_layer_section([{"text": "hello", "mode": "hybrid", "score": 0.85}])
    assert "hello" in out
    assert "hybrid" in out
    assert "0.85" in out


def test_mem_truncate():
    short = "x" * 50
    assert mc._mem_truncate(short, 100) == short
    long_ = "y" * 300
    truncated = mc._mem_truncate(long_, 50)
    assert truncated.endswith("…")
    assert len(truncated) <= 51  # 50 + 1 for ellipsis


# ---------------------------------------------------------------------------
# Re-exports — preserve API через src.handlers.command_handlers
# ---------------------------------------------------------------------------


class TestReExports:
    """Re-exports через src.handlers.command_handlers — preserve API."""

    def test_handlers_re_exported(self):
        from src.handlers import command_handlers as ch

        assert ch.handle_remember is mc.handle_remember
        assert ch.handle_recall is mc.handle_recall
        assert ch.handle_mem is mc.handle_mem
        assert ch.handle_quote is mc.handle_quote
        assert ch.handle_tag is mc.handle_tag

    def test_helpers_re_exported(self):
        from src.handlers import command_handlers as ch

        assert ch._recall_memory_layer is mc._recall_memory_layer
        assert ch._format_memory_layer_section is mc._format_memory_layer_section
        assert ch._mem_truncate is mc._mem_truncate
        assert ch._mem_search is mc._mem_search
        assert ch._mem_stats is mc._mem_stats
        assert ch._mem_count is mc._mem_count
        assert ch._mem_summary is mc._mem_summary
        assert ch._load_saved_quotes is mc._load_saved_quotes
        assert ch._save_quotes is mc._save_quotes
        assert ch._load_tags is mc._load_tags
        assert ch._save_tags is mc._save_tags
        assert ch._make_msg_link is mc._make_msg_link

    def test_state_re_exported(self):
        from src.handlers import command_handlers as ch

        assert ch._BUILTIN_QUOTES is mc._BUILTIN_QUOTES
        assert ch._SAVED_QUOTES_PATH is mc._SAVED_QUOTES_PATH
        assert ch._TAGS_FILE is mc._TAGS_FILE
        assert ch._MEM_HELP_TEXT is mc._MEM_HELP_TEXT
        assert ch._MEM_SNIPPET_LEN == mc._MEM_SNIPPET_LEN
        assert ch.MEMORY_SEARCH_URL == mc.MEMORY_SEARCH_URL
