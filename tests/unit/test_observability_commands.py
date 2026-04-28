# -*- coding: utf-8 -*-
"""
Тесты Wave 17 (Phase 2): observability_commands.

Проверяем:
1) handlers корректно импортируются как из нового модуля, так и через
   re-export `src.handlers.command_handlers` (back-compat для тестов);
2) dual-namespace lookup работает: monkeypatch `command_handlers.inbox_service`
   подхватывается `observability_commands.handle_inbox`;
3) `handle_watch status` использует `proactive_watch` и форматирует ответ;
4) `handle_context clear` сбрасывает сессию OpenClaw.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.handlers.commands import observability_commands
from src.handlers.commands.observability_commands import (
    _estimate_session_tokens,
    _format_time_ago,
    handle_context,
    handle_inbox,
    handle_watch,
)


def _make_message(text: str, chat_id: int = -100123) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(),
    )


def test_reexports_match_module_symbols() -> None:
    """handle_X в command_handlers re-export должен указывать на функцию из observability_commands."""
    assert command_handlers_module.handle_watch is observability_commands.handle_watch
    assert command_handlers_module.handle_inbox is observability_commands.handle_inbox
    assert command_handlers_module.handle_context is observability_commands.handle_context
    assert command_handlers_module.handle_memo is observability_commands.handle_memo
    assert command_handlers_module.handle_bookmark is observability_commands.handle_bookmark
    assert command_handlers_module.handle_note is observability_commands.handle_note
    assert command_handlers_module._CHECKPOINTS_DIR is observability_commands._CHECKPOINTS_DIR


def test_estimate_session_tokens_basic() -> None:
    msgs = [
        {"role": "user", "content": "hello world"},  # 11 chars
        {"role": "assistant", "content": [{"text": "abcd"}, {"text": "efgh"}]},  # 8 chars
    ]
    # Total ~19 chars => (19 + 3) // 4 = 5
    assert _estimate_session_tokens(msgs) == (19 + 3) // 4


def test_format_time_ago_branches() -> None:
    assert _format_time_ago(5) == "5 сек назад"
    assert _format_time_ago(125) == "2 мин назад"
    assert _format_time_ago(7200) == "2 ч назад"


@pytest.mark.asyncio
async def test_handle_watch_status_uses_command_handlers_namespace_patch(monkeypatch) -> None:
    """Dual-namespace lookup: патч command_handlers.proactive_watch
    должен подхватываться handle_watch через _ch_attr."""
    fake_watch = MagicMock()
    fake_watch.get_status.return_value = {
        "enabled": True,
        "interval_sec": 60,
        "alert_cooldown_sec": 120,
        "last_reason": None,
        "last_digest_ts": None,
        "last_alert_ts": None,
        "last_snapshot": {"route_model": "google/gemini-3-pro-preview"},
    }
    monkeypatch.setattr(command_handlers_module, "proactive_watch", fake_watch)
    message = _make_message("!watch status")
    bot = SimpleNamespace()
    await handle_watch(bot, message)
    fake_watch.get_status.assert_called_once()
    message.reply.assert_awaited_once()
    sent = message.reply.await_args.args[0]
    assert "Proactive Watch" in sent
    assert "google/gemini-3-pro-preview" in sent


@pytest.mark.asyncio
async def test_handle_inbox_dual_namespace_patch(monkeypatch) -> None:
    """`command_handlers.inbox_service = fake` должен использоваться через handle_inbox."""
    fake_inbox = MagicMock()
    fake_inbox.list_items.return_value = []
    monkeypatch.setattr(command_handlers_module, "inbox_service", fake_inbox)
    message = _make_message("!inbox list")
    bot = SimpleNamespace()
    await handle_inbox(bot, message)
    fake_inbox.list_items.assert_called_once_with(status="open", limit=8)


@pytest.mark.asyncio
async def test_handle_context_clear_resets_openclaw_session(monkeypatch) -> None:
    """`!context clear` должен вызвать openclaw_client.clear_session(chat_id)."""
    fake_client = MagicMock()
    fake_client._sessions = {}
    monkeypatch.setattr(command_handlers_module, "openclaw_client", fake_client)
    message = _make_message("!context clear", chat_id=-99999)
    bot = SimpleNamespace()
    await handle_context(bot, message)
    fake_client.clear_session.assert_called_once_with("-99999")
    message.reply.assert_awaited_once()
