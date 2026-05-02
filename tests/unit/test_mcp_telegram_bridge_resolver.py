# -*- coding: utf-8 -*-
"""Resolver wire-up tests for MCP Telegram bridge write tools (Session 32 Wave 3-D).

Verifies that send_photo / send_voice / send_reaction route string chat_id
через `src.core.telegram_resolver.resolve_peer` (тот же pattern что send_message).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# MCP server модули НЕ в src/, они в mcp-servers/telegram/
_MCP_DIR = Path(__file__).resolve().parents[2] / "mcp-servers" / "telegram"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


class _FakeMessage:
    id = 1
    text = "hi"
    caption = None
    media = None

    class _Chat:
        id = 123
        title = None
        first_name = "Test"

    chat = _Chat()
    from_user = None
    date = None
    reply_to_message_id = None


class _FakeClient:
    def __init__(self) -> None:
        self.send_photo = AsyncMock(return_value=_FakeMessage())
        self.send_voice = AsyncMock(return_value=_FakeMessage())
        self.send_reaction = AsyncMock(return_value=None)


# ── send_photo ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_photo_resolver_int_peer(monkeypatch):
    """String chat_id → resolver returns int peer_id → forwarded to client.send_photo."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    async def _resolver(_client, _target):
        return {"ok": True, "peer_id": 42}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_photo("@someuser", "/tmp/x.jpg", caption="cap")

    args, _ = fake.send_photo.call_args
    assert args[0] == 42  # int peer_id passed, NOT raw string
    assert args[1] == "/tmp/x.jpg"


@pytest.mark.asyncio
async def test_send_photo_resolver_me_preserved(monkeypatch):
    """peer_id='me' preserved as 'me' (Saved Messages)."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    async def _resolver(_client, _target):
        return {"ok": True, "peer_id": "me"}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_photo("me", "/tmp/x.jpg")

    args, _ = fake.send_photo.call_args
    assert args[0] == "me"


@pytest.mark.asyncio
async def test_send_photo_numeric_chat_id_skips_resolver(monkeypatch):
    """Numeric chat_id → resolver NOT called."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    resolver_called = {"n": 0}

    async def _resolver(_client, _target):
        resolver_called["n"] += 1
        return {"ok": True, "peer_id": 999}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_photo(123456, "/tmp/x.jpg")

    args, _ = fake.send_photo.call_args
    assert args[0] == 123456
    assert resolver_called["n"] == 0


# ── send_voice ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_voice_resolver_int_peer(monkeypatch):
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    async def _resolver(_client, _target):
        return {"ok": True, "peer_id": 77}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_voice("+34666555444", "/tmp/v.ogg", duration=5)

    args, kwargs = fake.send_voice.call_args
    assert args[0] == 77
    assert args[1] == "/tmp/v.ogg"
    assert kwargs.get("duration") == 5


@pytest.mark.asyncio
async def test_send_voice_resolver_me_preserved(monkeypatch):
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    async def _resolver(_client, _target):
        return {"ok": True, "peer_id": "me"}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_voice("me", "/tmp/v.ogg")

    args, _ = fake.send_voice.call_args
    assert args[0] == "me"


@pytest.mark.asyncio
async def test_send_voice_numeric_chat_id_skips_resolver(monkeypatch):
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    resolver_called = {"n": 0}

    async def _resolver(_client, _target):
        resolver_called["n"] += 1
        return {"ok": True, "peer_id": 0}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_voice(-1001234567890, "/tmp/v.ogg")

    args, _ = fake.send_voice.call_args
    assert args[0] == -1001234567890
    assert resolver_called["n"] == 0


# ── send_reaction ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_reaction_resolver_int_peer(monkeypatch):
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    async def _resolver(_client, _target):
        return {"ok": True, "peer_id": 99}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_reaction("@somechat", 555, emoji="🔥")

    args, kwargs = fake.send_reaction.call_args
    assert args[0] == 99
    assert args[1] == 555
    assert kwargs.get("emoji") == ["🔥"]


@pytest.mark.asyncio
async def test_send_reaction_resolver_me_preserved(monkeypatch):
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    async def _resolver(_client, _target):
        return {"ok": True, "peer_id": "me"}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_reaction("me", 1, emoji="👍")

    args, _ = fake.send_reaction.call_args
    assert args[0] == "me"


@pytest.mark.asyncio
async def test_send_reaction_numeric_chat_id_skips_resolver(monkeypatch):
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeClient()
    bridge._client = fake  # type: ignore[assignment]

    resolver_called = {"n": 0}

    async def _resolver(_client, _target):
        resolver_called["n"] += 1
        return {"ok": True, "peer_id": 1}

    monkeypatch.setattr("telegram_bridge._full_resolve_peer", _resolver)

    await bridge.send_reaction(42, 100, emoji="❤️")

    args, _ = fake.send_reaction.call_args
    assert args[0] == 42
    assert resolver_called["n"] == 0
