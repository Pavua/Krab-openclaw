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
async def test_photo_download_timeout_returns_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client, "send_message_stream", send_stream_mock
    )

    await bot._process_message(incoming)

    send_stream_mock.assert_not_called()
    assert bot._safe_edit.await_count >= 2
    last_edit_text = bot._safe_edit.await_args_list[-1].args[1]
    assert "Таймаут загрузки фото" in last_edit_text


@pytest.mark.asyncio
async def test_photo_without_caption_uses_russian_effective_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Фото без подписи должно идти в модель с русским текстом-запросом,
    а не с английским `(Image sent)`.
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

    photo_bytes = SimpleNamespace(getvalue=lambda: b"fake-image")
    temp_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123),
        text="",
        caption="",
    )
    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Краб",
        caption=None,
        photo=object(),
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=temp_msg),
    )

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(return_value=photo_bytes),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict[str, str] = {}

    async def _fake_stream(**kwargs):
        captured["message"] = kwargs["message"]
        yield "Готово"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    assert captured["message"] == "Опиши присланное изображение на русском языке."


@pytest.mark.asyncio
async def test_photo_auto_vision_forces_cloud_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    В userbot-контуре фото при `LOCAL_PREFERRED_VISION_MODEL=auto` должно
    принудительно идти в cloud, чтобы не выгружать локальный Nemotron.
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
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYSTEM")
    bot._build_effective_user_query = Mock(
        return_value="Опиши присланное изображение на русском языке."
    )
    bot._deliver_response_parts = AsyncMock()
    bot._build_runtime_chat_scope_id = Mock(return_value="123")

    photo_bytes = SimpleNamespace(getvalue=lambda: b"fake-image")
    temp_msg = SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")
    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Проверь фото",
        caption=None,
        photo=object(),
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=temp_msg),
    )

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(return_value=photo_bytes),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict[str, object] = {}

    async def _fake_stream(**kwargs):
        captured["force_cloud"] = kwargs["force_cloud"]
        captured["images"] = kwargs["images"]
        yield "Готово"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(userbot_bridge_module.config, "FORCE_CLOUD", False, raising=False)
    monkeypatch.setattr(
        userbot_bridge_module.config, "LOCAL_PREFERRED_VISION_MODEL", "qwen2-vl", raising=False
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_FORCE_CLOUD_FOR_PHOTO",
        True,
        raising=False,
    )

    await bot._process_message(incoming)

    assert captured["force_cloud"] is True
    assert len(captured["images"]) == 1
