"""
Тесты фото-path в userbot_bridge.

Цель:
- исключить зависание на статусе «Разглядываю фото...»;
- гарантировать явный user-visible отказ при timeout загрузки изображения.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.userbot_bridge import KraabUserbot


@pytest.mark.asyncio
async def test_photo_download_timeout_returns_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Если download_media по фото истекает по timeout, бот:
    1) не запускает AI stream;
    2) отдает явную ошибку пользователю.
    """
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()

    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="")
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kwargs: kwargs["text"])
    bot._split_message = Mock(side_effect=lambda text: [text])
    bot._looks_like_model_status_question = Mock(return_value=False)

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(side_effect=asyncio.TimeoutError()),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    temp_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123),
        text="",
        caption="",
    )
    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Краб, что на фото?",
        caption=None,
        photo=object(),
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=temp_msg),
    )

    send_stream_mock = Mock()
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", send_stream_mock)

    await bot._process_message(incoming)

    send_stream_mock.assert_not_called()
    assert bot._safe_edit.await_count >= 2
    last_edit_text = bot._safe_edit.await_args_list[-1].args[1]
    assert "Таймаут загрузки фото" in last_edit_text
