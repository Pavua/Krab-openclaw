# -*- coding: utf-8 -*-
"""
Тесты для расширенного `telegram_send_message` MCP tool (Session 25).

Покрывает userbot capabilities — reply_to_message_id, quote_text,
parse_mode, disable_web_page_preview — должны передаваться в Pyrogram
client.send_message без потерь.
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


@pytest.mark.asyncio
async def test_send_message_passes_reply_to_message_id():
    """reply_to_message_id передаётся в client.send_message."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake_client = _FakeClient()
    bridge._client = fake_client  # type: ignore[assignment]

    await bridge.send_message(123, "hi", reply_to_message_id=456)

    assert fake_client.last_call["args"] == (123, "hi")
    assert fake_client.last_call["kwargs"]["reply_to_message_id"] == 456


@pytest.mark.asyncio
async def test_send_message_passes_quote_text():
    """quote_text передаётся в client.send_message."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake_client = _FakeClient()
    bridge._client = fake_client  # type: ignore[assignment]

    await bridge.send_message(
        123, "hi", reply_to_message_id=10, quote_text="фрагмент цитаты"
    )
    assert fake_client.last_call["kwargs"]["quote_text"] == "фрагмент цитаты"


@pytest.mark.asyncio
async def test_send_message_parse_mode_markdown():
    """parse_mode='markdown' резолвится в pyrogram.enums.ParseMode.MARKDOWN."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake_client = _FakeClient()
    bridge._client = fake_client  # type: ignore[assignment]

    await bridge.send_message(123, "**bold**", parse_mode="markdown")

    pm = fake_client.last_call["kwargs"].get("parse_mode")
    assert pm is not None
    # ParseMode enum members имеют name атрибут
    assert pm.name == "MARKDOWN"


@pytest.mark.asyncio
async def test_send_message_disable_web_page_preview():
    """disable_web_page_preview=True передаётся."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake_client = _FakeClient()
    bridge._client = fake_client  # type: ignore[assignment]

    await bridge.send_message(123, "https://example.com", disable_web_page_preview=True)

    assert fake_client.last_call["kwargs"]["disable_web_page_preview"] is True


@pytest.mark.asyncio
async def test_send_message_none_params_omitted():
    """None/falsy params НЕ передаются в kwargs (Pyrogram default)."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake_client = _FakeClient()
    bridge._client = fake_client  # type: ignore[assignment]

    await bridge.send_message(123, "plain")

    kwargs = fake_client.last_call["kwargs"]
    assert "reply_to_message_id" not in kwargs
    assert "quote_text" not in kwargs
    assert "parse_mode" not in kwargs
    assert "disable_web_page_preview" not in kwargs


@pytest.mark.asyncio
async def test_edit_message_parse_mode_html():
    """parse_mode для edit резолвится в HTML."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    fake_client = _FakeClient(method="edit_message_text")
    bridge._client = fake_client  # type: ignore[assignment]

    await bridge.edit_message(123, 5, "<b>new</b>", parse_mode="html")
    pm = fake_client.last_call["kwargs"].get("parse_mode")
    assert pm is not None
    assert pm.name == "HTML"


@pytest.mark.asyncio
async def test_session_info_json_user_session():
    """is_bot=False → возвращает userbot capabilities (без warning)."""
    import json as _json

    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()

    class _Me:
        is_bot = False
        id = 999
        username = "krab"
        first_name = "Krab"

    fake_client = _FakeClient(method="send_message")

    async def _get_me():
        return _Me()

    fake_client.get_me = _get_me  # type: ignore[method-assign]
    bridge._client = fake_client  # type: ignore[assignment]

    raw = await bridge.session_info_json()
    data = _json.loads(raw)
    assert data["ok"] is True
    assert data["is_bot"] is False
    assert data["user_id"] == 999
    assert data["username"] == "krab"
    assert isinstance(data["capabilities"], list)
    capabilities_lower = " ".join(data["capabilities"]).lower()
    # Хотя бы один маркер userbot функциональности должен присутствовать
    assert any(marker in capabilities_lower for marker in ("dm", "reply", "userbot", "search"))
    # Для userbot warning не должен быть set
    assert data.get("warning") is None


@pytest.mark.asyncio
async def test_send_message_peer_id_invalid_returns_structured_error():
    """PeerIdInvalid → structured error с hint, не raise (Session 25)."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()

    class _PeerIdInvalid(Exception):
        """Mimic Pyrogram PeerIdInvalid (subclass-checked by name)."""

        def __init__(self, msg: str = "Peer id invalid") -> None:
            super().__init__(msg)

    _PeerIdInvalid.__name__ = "PeerIdInvalid"

    fake_client = _FakeClient()

    async def _send_failing(*args, **kwargs):
        raise _PeerIdInvalid()

    async def _get_chat_failing(_):
        raise _PeerIdInvalid()

    fake_client.send_message = _send_failing  # type: ignore[method-assign]
    fake_client.get_chat = _get_chat_failing  # type: ignore[method-assign]
    bridge._client = fake_client  # type: ignore[assignment]

    result = await bridge.send_message(123456, "hi")
    assert result["ok"] is False
    assert result["error_code"] == "peer_id_invalid"
    assert "hint" in result
    assert "username" in result["hint"].lower() or "общ" in result["hint"]
    assert result["chat_id"] == 123456


@pytest.mark.asyncio
async def test_send_message_peer_resolves_after_get_chat():
    """PeerIdInvalid на send → get_chat populates cache → retry success."""
    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    call_count = {"send": 0}

    class _PeerIdInvalid(Exception):
        pass

    _PeerIdInvalid.__name__ = "PeerIdInvalid"

    async def _send_with_retry(*args, **kwargs):
        call_count["send"] += 1
        if call_count["send"] == 1:
            raise _PeerIdInvalid("Peer id invalid")
        return _FakeMessage()

    async def _get_chat_ok(_):
        return type("Chat", (), {"id": 123})()

    fake_client = _FakeClient()
    fake_client.send_message = _send_with_retry  # type: ignore[method-assign]
    fake_client.get_chat = _get_chat_ok  # type: ignore[method-assign]
    bridge._client = fake_client  # type: ignore[assignment]

    result = await bridge.send_message(123, "hi")
    # После retry — успешный msg dict (без ok=False)
    assert call_count["send"] == 2
    assert "id" in result
    assert result.get("ok") is not False


@pytest.mark.asyncio
async def test_session_info_json_bot_warns():
    """is_bot=True → возвращает warning с инструкцией для re-auth."""
    import json as _json

    from telegram_bridge import TelegramBridge

    bridge = TelegramBridge()

    class _Me:
        is_bot = True
        id = 100
        username = "krab_bot"
        first_name = "Krab Bot"

    fake_client = _FakeClient(method="send_message")

    async def _get_me():
        return _Me()

    fake_client.get_me = _get_me  # type: ignore[method-assign]
    bridge._client = fake_client  # type: ignore[assignment]

    raw = await bridge.session_info_json()
    data = _json.loads(raw)
    assert data["is_bot"] is True
    assert data.get("warning") is not None
    assert "auth_setup" in data["warning"]


# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeClient:
    """Минимальный fake Pyrogram Client для assert на передаваемые kwargs."""

    def __init__(self, method: str = "send_message") -> None:
        self.last_call: dict = {}
        self._method = method

        async def _send(*args, **kwargs):
            self.last_call = {"args": args, "kwargs": kwargs}
            return _FakeMessage()

        # support send_message + edit_message_text
        self.send_message = _send  # type: ignore[method-assign]
        self.edit_message_text = _send  # type: ignore[method-assign]

    async def get_me(self):
        # Можно override через assignment в тестах
        raise NotImplementedError


class _FakeMessage:
    """Pyrogram Message stub — _msg_to_dict вызывается на нём."""

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
