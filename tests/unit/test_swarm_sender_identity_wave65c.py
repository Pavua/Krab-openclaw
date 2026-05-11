# -*- coding: utf-8 -*-
"""
Wave 65-C / AGE-16: swarm team DM bots должны распознавать owner sender.

Задача: при ответе в DM swarm team bot должен видеть identity отправителя:
- Owner (Pavel, @p0lrd, user_id=312322764) — без security disclaimers
- Не-owner — обращение по имени, но guarded

Тесты:
- test_owner_identity_injected: sender с owner user_id → prompt содержит "Pavel" + "владелец"
- test_unknown_sender_identity: random user_id → prompt содержит "Sender context" + имя
- test_no_sender_no_changes: sender=None → prompt без identity block (backwards compat)
- test_sender_in_listener_passed_to_prompt: _stream_reply передаёт sender из message.from_user
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.swarm_team_listener as stl
import src.core.swarm_team_prompts as stp

# ---------------------------------------------------------------------------
# Тесты get_team_system_prompt(sender=...) — Wave 65-C
# ---------------------------------------------------------------------------


def _mk_sender(user_id: int, first_name: str = "", username: str = "") -> SimpleNamespace:
    """Создаёт mock sender (pyrogram User-like)."""
    return SimpleNamespace(
        id=user_id,
        first_name=first_name,
        username=username,
    )


def test_owner_identity_injected_in_prompt(tmp_path) -> None:
    """Wave 65-C: owner user_id → prompt содержит блок «Owner context» с Pavel + p0lrd."""
    # Подготовим ACL с owner id 312322764
    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"owner": ["312322764", "p0lrd"]}', encoding="utf-8")

    # Чистим overlay-кэш и патчим путь ACL
    stp._overlay_cache.clear()
    with patch("src.core.access_control._runtime_acl_path", return_value=acl_file):
        sender = _mk_sender(user_id=312322764, first_name="Pavel", username="p0lrd")
        prompt = stp.get_team_system_prompt("coders", sender=sender)

    assert "Owner context" in prompt
    assert "Pavel" in prompt
    assert "p0lrd" in prompt
    assert "312322764" in prompt
    assert "владелец" in prompt.lower() or "owner" in prompt.lower()
    # baseline coders должен остаться (это extension, не replacement)
    assert "Python" in prompt or "Coders" in prompt


def test_unknown_sender_identity_in_prompt(tmp_path) -> None:
    """Wave 65-C: незнакомый user_id → prompt содержит блок «Sender context» с именем."""
    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"owner": ["312322764"]}', encoding="utf-8")

    stp._overlay_cache.clear()
    with patch("src.core.access_control._runtime_acl_path", return_value=acl_file):
        sender = _mk_sender(user_id=99999, first_name="Alice", username="alice42")
        prompt = stp.get_team_system_prompt("traders", sender=sender)

    assert "Sender context" in prompt
    assert "Alice" in prompt
    assert "99999" in prompt
    # Не должно содержать "Owner context" или Pavel
    assert "Owner context" not in prompt
    assert "Pavel" not in prompt


def test_no_sender_no_identity_block(tmp_path) -> None:
    """Wave 65-C: backwards compat — sender=None → prompt без identity block."""
    stp._overlay_cache.clear()
    prompt_no_sender = stp.get_team_system_prompt("analysts")
    # baseline без identity
    assert "Sender context" not in prompt_no_sender
    assert "Owner context" not in prompt_no_sender
    # baseline содержит специализацию аналитиков
    assert "аналитика" in prompt_no_sender.lower() or "Analysts" in prompt_no_sender


def test_sender_without_first_name_fallback_to_username(tmp_path) -> None:
    """Wave 65-C: если first_name пустое, используем username."""
    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"owner": ["312322764"]}', encoding="utf-8")

    stp._overlay_cache.clear()
    with patch("src.core.access_control._runtime_acl_path", return_value=acl_file):
        sender = _mk_sender(user_id=88888, first_name="", username="anon42")
        prompt = stp.get_team_system_prompt("creative", sender=sender)

    assert "Sender context" in prompt
    assert "anon42" in prompt


def test_sender_without_name_fallback_to_unknown(tmp_path) -> None:
    """Wave 65-C: если ни first_name, ни username, используем «Unknown»."""
    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"owner": ["312322764"]}', encoding="utf-8")

    stp._overlay_cache.clear()
    with patch("src.core.access_control._runtime_acl_path", return_value=acl_file):
        sender = _mk_sender(user_id=77777, first_name="", username="")
        prompt = stp.get_team_system_prompt("coders", sender=sender)

    assert "Sender context" in prompt
    assert "77777" in prompt


# ---------------------------------------------------------------------------
# Тесты integration в swarm_team_listener
# ---------------------------------------------------------------------------


def _make_message(
    text: str = "привет",
    from_user_id: int = 312322764,
    from_first_name: str = "Pavel",
    from_username: str = "p0lrd",
    chat_type: str = "private",
    chat_id: int = 100,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    msg.from_user = SimpleNamespace(
        id=from_user_id,
        first_name=from_first_name,
        username=from_username,
    )
    msg.chat = SimpleNamespace(type=chat_type, id=chat_id)
    msg.reply_to_message = None
    msg.reply = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    return msg


def _make_client(me_id: int = 999, me_username: str = "teambot") -> MagicMock:
    client = MagicMock()
    client.me = SimpleNamespace(id=me_id, username=me_username)
    client.get_me = AsyncMock(return_value=client.me)
    client.send_chat_action = AsyncMock()
    client.on_message = MagicMock(return_value=lambda f: f)
    return client


async def _chunk_stream(*chunks: str):
    """Генератор с заданными чанками."""
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_stream_reply_passes_sender_to_prompt(tmp_path) -> None:
    """Wave 65-C: _stream_reply передаёт sender из message.from_user в get_team_system_prompt."""
    client = _make_client()
    msg = _make_message(
        from_user_id=312322764,
        from_first_name="Pavel",
        from_username="p0lrd",
    )
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    captured_system_prompt: dict[str, str] = {}

    async def _capture_stream(message, chat_id, system_prompt, **kwargs):
        captured_system_prompt["value"] = system_prompt
        yield "ok"

    openclaw.send_message_stream = _capture_stream

    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"owner": ["312322764"]}', encoding="utf-8")

    stp._overlay_cache.clear()
    with patch("src.core.access_control._runtime_acl_path", return_value=acl_file):
        await stl._stream_reply("coders", client, msg, openclaw, "привет")

    # System prompt должен содержать Owner context
    assert "value" in captured_system_prompt
    assert "Owner context" in captured_system_prompt["value"]
    assert "Pavel" in captured_system_prompt["value"]


@pytest.mark.asyncio
async def test_stream_reply_non_owner_passes_sender_context(tmp_path) -> None:
    """Wave 65-C: для non-owner sender, prompt содержит Sender context."""
    client = _make_client()
    msg = _make_message(
        from_user_id=55555,
        from_first_name="Bob",
        from_username="bob42",
    )
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    captured: dict[str, str] = {}

    async def _capture_stream(message, chat_id, system_prompt, **kwargs):
        captured["value"] = system_prompt
        yield "ok"

    openclaw.send_message_stream = _capture_stream

    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"owner": ["312322764"]}', encoding="utf-8")

    stp._overlay_cache.clear()
    with patch("src.core.access_control._runtime_acl_path", return_value=acl_file):
        await stl._stream_reply("analysts", client, msg, openclaw, "hi")

    assert "Sender context" in captured["value"]
    assert "Bob" in captured["value"]
    assert "Owner context" not in captured["value"]


@pytest.mark.asyncio
async def test_stream_reply_no_from_user_no_identity_block(tmp_path) -> None:
    """Wave 65-C: message без from_user → baseline prompt без identity block."""
    client = _make_client()
    msg = _make_message()
    msg.from_user = None  # Anonymous sender
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    captured: dict[str, str] = {}

    async def _capture_stream(message, chat_id, system_prompt, **kwargs):
        captured["value"] = system_prompt
        yield "ok"

    openclaw.send_message_stream = _capture_stream

    stp._overlay_cache.clear()
    await stl._stream_reply("traders", client, msg, openclaw, "anonymous")

    assert "value" in captured
    assert "Sender context" not in captured["value"]
    assert "Owner context" not in captured["value"]


# ---------------------------------------------------------------------------
# Тесты overlay + sender combination
# ---------------------------------------------------------------------------


def test_overlay_with_sender_identity(tmp_path, monkeypatch) -> None:
    """Wave 65-C: identity block добавляется поверх overlay-prompt."""
    acl_file = tmp_path / "acl.json"
    acl_file.write_text('{"owner": ["312322764"]}', encoding="utf-8")

    # Симулируем overlay через монки
    stp._overlay_cache.clear()
    stp._overlay_cache["coders"] = (
        float("inf"),  # не expire
        "OVERLAY_PROMPT_CUSTOM",
    )

    with patch("src.core.access_control._runtime_acl_path", return_value=acl_file):
        sender = _mk_sender(user_id=312322764, first_name="Pavel", username="p0lrd")
        prompt = stp.get_team_system_prompt("coders", sender=sender)

    assert "OVERLAY_PROMPT_CUSTOM" in prompt
    assert "Owner context" in prompt
    assert "Pavel" in prompt
    stp._overlay_cache.clear()
