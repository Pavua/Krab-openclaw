# -*- coding: utf-8 -*-
"""
Unit тесты для новых Telegram MCP tools:
  telegram_send_photo, telegram_send_reaction, telegram_forward_message,
  telegram_delete_message, telegram_pin_message, telegram_get_message,
  telegram_send_voice

Тестируем через прямой вызов TelegramBridge-методов (telegram_bridge.py),
мокая Pyrogram Client. Для tool-хендлеров тестируем обработку ошибок через
мокирование _bridge в server.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers для загрузки модулей
# ---------------------------------------------------------------------------

_TELEGRAM_DIR = Path(__file__).resolve().parents[2] / "mcp-servers" / "telegram"


def _ensure_sys_path():
    server_dir = str(_TELEGRAM_DIR)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)


def _load_bridge_module():
    _ensure_sys_path()
    if "telegram_bridge_under_test" in sys.modules:
        return sys.modules["telegram_bridge_under_test"]
    module_path = _TELEGRAM_DIR / "telegram_bridge.py"
    spec = importlib.util.spec_from_file_location("telegram_bridge_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["telegram_bridge_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _make_mock_message(
    msg_id: int = 42,
    chat_id: int = -1001234567890,
    text: str = "hello",
) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id
    msg.chat.id = chat_id
    msg.chat.title = "Test Chat"
    msg.chat.first_name = None
    msg.from_user = MagicMock()
    msg.from_user.first_name = "TestUser"
    msg.text = text
    msg.caption = None
    msg.date = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    msg.media = None
    msg.reply_to_message_id = None
    msg.entities = []
    return msg


@pytest.fixture(scope="module")
def bridge_mod():
    return _load_bridge_module()


@pytest.fixture
def bridge(bridge_mod):
    return bridge_mod.TelegramBridge()


# ---------------------------------------------------------------------------
# Helper: mock _run_client_call
# ---------------------------------------------------------------------------

def _patch_run(bridge, return_value):
    """Патчит _run_client_call чтобы вернуть заданное значение."""
    async def _fake_run(callback):
        return return_value

    bridge._run_client_call = _fake_run


# ---------------------------------------------------------------------------
# 1. telegram_send_photo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_photo_local_path(bridge, bridge_mod):
    """send_photo с локальным путём возвращает метаданные сообщения."""
    msg = _make_mock_message(msg_id=100, text="")
    msg.caption = "Nice photo"

    async def _fake_run(callback):
        client = MagicMock()
        client.send_photo = AsyncMock(return_value=msg)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.send_photo(-100123, "/tmp/test.jpg", caption="Nice photo")
    assert result["id"] == 100


@pytest.mark.asyncio
async def test_send_photo_url(bridge, bridge_mod):
    """send_photo с URL-строкой вызывает client.send_photo с этим URL."""
    msg = _make_mock_message(msg_id=101)
    url = "https://example.com/photo.jpg"
    captured = {}

    async def _fake_run(callback):
        client = MagicMock()
        async def _send(chat_id, photo, **kwargs):
            captured["photo"] = photo
            return msg
        client.send_photo = _send
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.send_photo(-100123, url)
    assert captured["photo"] == url
    assert result["id"] == 101


# ---------------------------------------------------------------------------
# 2. telegram_send_reaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reaction_single_emoji(bridge, bridge_mod):
    """send_reaction с одним эмодзи возвращает ok=True."""
    async def _fake_run(callback):
        client = MagicMock()
        client.send_reaction = AsyncMock(return_value=None)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.send_reaction(-100123, 42, "👍")
    assert result["ok"] is True
    assert result["emoji"] == ["👍"]
    assert result["message_id"] == 42


@pytest.mark.asyncio
async def test_send_reaction_list_emoji(bridge, bridge_mod):
    """send_reaction принимает список эмодзи."""
    async def _fake_run(callback):
        client = MagicMock()
        client.send_reaction = AsyncMock(return_value=None)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.send_reaction(-100123, 42, ["👍", "❤️"])
    assert result["ok"] is True
    assert result["emoji"] == ["👍", "❤️"]


# ---------------------------------------------------------------------------
# 3. telegram_forward_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_message(bridge, bridge_mod):
    """forward_message пересылает и возвращает метаданные нового сообщения."""
    forwarded = _make_mock_message(msg_id=200)

    async def _fake_run(callback):
        client = MagicMock()
        client.forward_messages = AsyncMock(return_value=[forwarded])
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.forward_message(-100111, 42, -100222)
    assert result["id"] == 200


# ---------------------------------------------------------------------------
# 4. telegram_delete_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_message_single(bridge, bridge_mod):
    """delete_messages с одним ID возвращает ok=True и список удалённых."""
    async def _fake_run(callback):
        client = MagicMock()
        client.delete_messages = AsyncMock(return_value=True)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.delete_messages(-100123, 42)
    assert result["ok"] is True
    assert 42 in result["deleted"]


@pytest.mark.asyncio
async def test_delete_message_multiple(bridge, bridge_mod):
    """delete_messages принимает список ID."""
    async def _fake_run(callback):
        client = MagicMock()
        client.delete_messages = AsyncMock(return_value=True)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.delete_messages(-100123, [1, 2, 3])
    assert result["ok"] is True
    assert result["deleted"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 5. telegram_pin_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_message(bridge, bridge_mod):
    """pin_message закрепляет сообщение."""
    async def _fake_run(callback):
        client = MagicMock()
        client.pin_chat_message = AsyncMock(return_value=True)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.pin_message(-100123, 42)
    assert result["ok"] is True
    assert result["action"] == "pinned"


@pytest.mark.asyncio
async def test_unpin_message(bridge, bridge_mod):
    """pin_message с unpin=True открепляет сообщение."""
    async def _fake_run(callback):
        client = MagicMock()
        client.unpin_chat_message = AsyncMock(return_value=True)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.pin_message(-100123, 42, unpin=True)
    assert result["ok"] is True
    assert result["action"] == "unpinned"


# ---------------------------------------------------------------------------
# 6. telegram_get_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_message_returns_full_dict(bridge, bridge_mod):
    """get_message возвращает полный словарь с entities."""
    msg = _make_mock_message(msg_id=55, text="Sample text")
    msg.entities = []

    async def _fake_run(callback):
        client = MagicMock()
        client.get_messages = AsyncMock(return_value=msg)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.get_message(-100123, 55)
    assert result["id"] == 55
    assert result["text"] == "Sample text"
    assert "entities" in result
    assert isinstance(result["entities"], list)


@pytest.mark.asyncio
async def test_get_message_list_response(bridge, bridge_mod):
    """get_message обрабатывает случай когда get_messages возвращает список."""
    msg = _make_mock_message(msg_id=77)
    msg.entities = []

    async def _fake_run(callback):
        client = MagicMock()
        client.get_messages = AsyncMock(return_value=[msg])
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.get_message(-100123, 77)
    assert result["id"] == 77


# ---------------------------------------------------------------------------
# 7. telegram_send_voice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_voice(bridge, bridge_mod):
    """send_voice отправляет .ogg файл и возвращает метаданные."""
    msg = _make_mock_message(msg_id=300)

    async def _fake_run(callback):
        client = MagicMock()
        client.send_voice = AsyncMock(return_value=msg)
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.send_voice(-100123, "/tmp/voice.ogg")
    assert result["id"] == 300


@pytest.mark.asyncio
async def test_send_voice_with_duration(bridge, bridge_mod):
    """send_voice передаёт duration если задан."""
    msg = _make_mock_message(msg_id=301)
    captured = {}

    async def _fake_run(callback):
        client = MagicMock()
        async def _send_voice(chat_id, voice_path, **kwargs):
            captured.update(kwargs)
            return msg
        client.send_voice = _send_voice
        return await callback(client)

    bridge._run_client_call = _fake_run

    result = await bridge.send_voice(-100123, "/tmp/voice.ogg", duration=15)
    assert result["id"] == 301
    assert captured.get("duration") == 15


# ---------------------------------------------------------------------------
# Error handling: missing photo source → ok=False
# ---------------------------------------------------------------------------


def _load_server_module():
    _ensure_sys_path()
    if "mcp_tg_new_server_under_test" in sys.modules:
        return sys.modules["mcp_tg_new_server_under_test"]
    module_path = _TELEGRAM_DIR / "server.py"
    spec = importlib.util.spec_from_file_location("mcp_tg_new_server_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["mcp_tg_new_server_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server_mod():
    return _load_server_module()


@pytest.mark.asyncio
async def test_send_photo_no_source_returns_error(server_mod):
    """telegram_send_photo без photo_path и photo_url возвращает ok=False."""
    handler = server_mod.telegram_send_photo
    params_cls = server_mod._SendPhotoInput
    params = params_cls(chat_id="-100123", photo_path="", photo_url="", caption="")
    result_json = await handler(params)
    result = json.loads(result_json)
    assert result["ok"] is False
    assert "photo_path" in result["error"] or "photo_url" in result["error"]
