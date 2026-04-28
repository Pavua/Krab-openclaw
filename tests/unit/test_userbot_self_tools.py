# -*- coding: utf-8 -*-
"""
Unit-тесты для src.core.userbot_self_tools (Feature M).

Покрывают:
- 6 native tools с mock pyrogram client
- rate-limit срабатывает на 11-м вызове
- client=None graceful (без exception)
- USERBOT_SELF_TOOL_SCHEMAS — все entries валидны
- dispatcher маршрутизирует tool_name → handler
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.userbot_self_tools import (
    USERBOT_SELF_TOOL_SCHEMAS,
    UserbotSelfTools,
    dispatch_userbot_self_tool,
    is_userbot_self_tool,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_message(
    msg_id: int = 1,
    chat_id: int = -1001,
    text: str = "hello",
    has_photo: bool = False,
) -> SimpleNamespace:
    chat = SimpleNamespace(id=chat_id, type=SimpleNamespace(name="GROUP"), title="Test")
    user = SimpleNamespace(
        id=42, username="alice", first_name="Alice", is_bot=False, is_self=False
    )
    return SimpleNamespace(
        id=msg_id,
        chat=chat,
        from_user=user,
        text=text,
        caption=None,
        date=dt.datetime(2026, 4, 28, 12, 0, 0, tzinfo=dt.timezone.utc),
        reply_to_message_id=None,
        photo=SimpleNamespace() if has_photo else None,
        video=None,
        voice=None,
        audio=None,
        document=None,
        sticker=None,
        animation=None,
    )


class _AsyncIter:
    """Имитирует async-iterator pyrogram (get_chat_history / search_messages)."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> "_AsyncIter":
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _make_client_with_history(messages: list[Any]) -> MagicMock:
    client = MagicMock()
    client.get_chat_history = MagicMock(return_value=_AsyncIter(messages))
    client.search_messages = MagicMock(return_value=_AsyncIter(messages))
    client.search_global = MagicMock(return_value=_AsyncIter(messages))
    client.get_dialogs = MagicMock(return_value=_AsyncIter(messages))
    return client


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_schemas_are_valid() -> None:
    """Все 6 schemas зарегистрированы и валидны."""
    assert len(USERBOT_SELF_TOOL_SCHEMAS) == 6
    names = {s["name"] for s in USERBOT_SELF_TOOL_SCHEMAS}
    assert names == {
        "userbot_read_chat_history",
        "userbot_search_chat",
        "userbot_search_global",
        "userbot_get_message",
        "userbot_get_dialogs",
        "userbot_resolve_user",
    }
    for schema in USERBOT_SELF_TOOL_SCHEMAS:
        assert "description" in schema and isinstance(schema["description"], str)
        assert schema["inputSchema"]["type"] == "object"
        assert "properties" in schema["inputSchema"]


@pytest.mark.asyncio
async def test_read_chat_history_returns_serialized_messages() -> None:
    msgs = [_make_message(msg_id=i, text=f"msg{i}") for i in (10, 9, 8)]
    client = _make_client_with_history(msgs)
    tools = UserbotSelfTools(client=client)

    result = await tools.read_chat_history(chat_id=-1001, limit=3)

    assert result["ok"] is True
    assert result["count"] == 3
    assert result["messages"][0]["id"] == 10
    assert result["messages"][0]["text"] == "msg10"
    assert result["messages"][0]["chat_id"] == -1001
    assert result["messages"][0]["from_user"]["username"] == "alice"
    assert result["messages"][0]["has_media"] is False
    assert result["messages"][0]["date_iso"].startswith("2026-04-28")


@pytest.mark.asyncio
async def test_search_chat_with_query() -> None:
    msgs = [_make_message(msg_id=5, text="ищу нужное")]
    client = _make_client_with_history(msgs)
    tools = UserbotSelfTools(client=client)

    result = await tools.search_chat(chat_id="@channel", query="нужное", limit=5)

    assert result["ok"] is True
    assert result["query"] == "нужное"
    assert result["count"] == 1
    client.search_messages.assert_called_once()


@pytest.mark.asyncio
async def test_search_global_empty_query_rejected() -> None:
    client = _make_client_with_history([])
    tools = UserbotSelfTools(client=client)
    result = await tools.search_global(query="   ", limit=10)
    assert result["ok"] is False
    assert result["error"] == "empty_query"


@pytest.mark.asyncio
async def test_get_message_serialization_with_media() -> None:
    msg = _make_message(msg_id=77, has_photo=True)
    client = MagicMock()
    client.get_messages = AsyncMock(return_value=msg)
    tools = UserbotSelfTools(client=client)

    result = await tools.get_message(chat_id=-1001, message_id=77)

    assert result["ok"] is True
    assert result["message"]["id"] == 77
    assert result["message"]["has_media"] is True
    assert result["message"]["media_type"] == "photo"


@pytest.mark.asyncio
async def test_get_dialogs_returns_list() -> None:
    chat = SimpleNamespace(
        id=-1002,
        type=SimpleNamespace(name="SUPERGROUP"),
        title="Krab Swarm",
        username="krab_swarm",
        first_name=None,
    )
    dlg = SimpleNamespace(
        chat=chat,
        unread_messages_count=3,
        top_message=SimpleNamespace(id=999),
    )
    client = _make_client_with_history([dlg])
    tools = UserbotSelfTools(client=client)

    result = await tools.get_dialogs(limit=10)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["dialogs"][0]["chat_id"] == -1002
    assert result["dialogs"][0]["title"] == "Krab Swarm"
    assert result["dialogs"][0]["unread_count"] == 3


@pytest.mark.asyncio
async def test_resolve_user_returns_user_info() -> None:
    user = SimpleNamespace(
        id=42,
        username="alice",
        first_name="Alice",
        last_name="Smith",
        is_bot=False,
        is_self=False,
        is_verified=False,
        is_premium=True,
    )
    client = MagicMock()
    client.get_users = AsyncMock(return_value=user)
    tools = UserbotSelfTools(client=client)

    result = await tools.resolve_user("@alice")

    assert result["ok"] is True
    assert result["user"]["id"] == 42
    assert result["user"]["username"] == "alice"
    assert result["user"]["is_premium"] is True


@pytest.mark.asyncio
async def test_rate_limit_kicks_in_after_10_calls() -> None:
    client = _make_client_with_history([])  # пустой iter каждый вызов
    tools = UserbotSelfTools(client=client)

    # Фабрика возвращает свежий итератор каждый вызов.
    client.get_chat_history = MagicMock(side_effect=lambda **_kw: _AsyncIter([]))

    for _ in range(10):
        out = await tools.read_chat_history(chat_id=-1001, limit=1)
        assert out["ok"] is True

    blocked = await tools.read_chat_history(chat_id=-1001, limit=1)
    assert blocked["ok"] is False
    assert blocked["error"] == "rate_limited"


@pytest.mark.asyncio
async def test_no_client_returns_graceful_error() -> None:
    """client=None → все tools возвращают ok=False, не падают."""
    tools = UserbotSelfTools(client=None)

    # Заблокировать ленивый импорт _userbot_client (которого нет).
    out1 = await tools.read_chat_history(chat_id=-1001, limit=5)
    out2 = await tools.search_chat(chat_id=-1001, query="x")
    out3 = await tools.search_global(query="x")
    out4 = await tools.get_message(chat_id=-1001, message_id=1)
    out5 = await tools.get_dialogs()
    out6 = await tools.resolve_user("@alice")

    for out in (out1, out2, out3, out4, out5, out6):
        assert out["ok"] is False
        assert out["error"] == "client_unavailable"


@pytest.mark.asyncio
async def test_dispatcher_routes_to_correct_tool() -> None:
    """dispatch_userbot_self_tool маршрутизирует и обрабатывает unknown tool."""
    assert is_userbot_self_tool("userbot_read_chat_history") is True
    assert is_userbot_self_tool("voice:get_recent_dictations") is False

    # Unknown tool → graceful.
    out = await dispatch_userbot_self_tool("userbot_unknown", {})
    assert out["ok"] is False
    assert "unknown_tool" in out["error"]
