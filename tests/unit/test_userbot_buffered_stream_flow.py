# -*- coding: utf-8 -*-
"""
Тесты buffered-text потока userbot_bridge.

Проверяем ключевой регресс:
1) первый soft-timeout не должен мгновенно убивать живой OpenClaw-запрос;
2) userbot обязан дождаться buffered-ответа в пределах hard-timeout;
3) пользователю должно прийти явное notice о долгом ожидании.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.core.access_control import AccessLevel, AccessProfile
from src.userbot_bridge import KraabUserbot


def _make_buffered_bot_stub() -> KraabUserbot:
    """Создаёт минимальный bot stub для проверки buffered-text сценария."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot.voice_reply_speed = "+0%"
    bot.voice_reply_voice = "ru-RU-DmitryNeural"
    bot.voice_reply_delivery = "text+voice"
    bot._known_commands = set()
    bot._chat_background_tasks = {}
    bot._disclosure_sent_for_chat_ids = set()

    bot._message_has_audio = Mock(return_value=False)
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(side_effect=lambda text: text or "")
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kwargs: kwargs["text"])
    bot._split_message = Mock(side_effect=lambda text: [text])
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYSTEM")
    bot._build_runtime_chat_scope_id = Mock(return_value="runtime-chat-123")
    bot._build_effective_user_query = Mock(side_effect=lambda *, query, has_images: query)
    bot._extract_live_stream_text = Mock(side_effect=lambda text, allow_reasoning=False: text)
    bot._strip_transport_markup = Mock(side_effect=lambda text: text)
    bot._apply_deferred_action_guard = Mock(side_effect=lambda text: text)
    bot._remember_hidden_reasoning_trace = Mock()
    bot._should_send_voice_reply = Mock(return_value=False)
    bot._should_force_cloud_for_photo_route = Mock(return_value=False)
    bot._sync_incoming_message_to_inbox = Mock(return_value=None)
    bot._record_incoming_reply_to_inbox = Mock()
    bot._deliver_response_parts = AsyncMock(
        side_effect=lambda **kwargs: {
            "delivery_mode": "edit",
            "text_message_ids": [],
            "parts_count": 1,
            "full_response": kwargs["full_response"],
        }
    )
    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        get_chat_history=None,
    )
    return bot


def test_normalize_user_visible_fallback_text_converts_raw_openclaw_stub() -> None:
    """Сырой англоязычный fallback не должен доходить до Telegram как есть."""
    assert (
        KraabUserbot._normalize_user_visible_fallback_text("No response from OpenClaw.")
        == "❌ OpenClaw не вернул текстовый ответ. Попробуй повторить запрос."
    )


def test_should_send_voice_for_response_skips_error_surfaces() -> None:
    """Голос не должен озвучивать transport/model fallback."""
    bot = _make_buffered_bot_stub()
    bot._should_send_voice_reply = Mock(return_value=True)

    assert bot._should_send_voice_for_response("Всё готово, погнали дальше.")
    assert not bot._should_send_voice_for_response("No response from OpenClaw.")
    assert not bot._should_send_voice_for_response("❌ Модель не вернула ответ.")


@pytest.mark.asyncio
async def test_text_route_waits_past_first_chunk_soft_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Buffered-text запрос не должен падать после первого soft-timeout,
    если ответ приходит в пределах расширенного hard-timeout окна.
    """
    bot = _make_buffered_bot_stub()
    incoming = SimpleNamespace(
        id=10,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Привет, Краб",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(
            return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")
        ),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )

    async def _fake_stream(**kwargs):
        _ = kwargs
        await asyncio.sleep(0.03)
        yield "Готовый buffered-ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {"model": "openai-codex/gpt-5.4", "channel": "planning", "status": "ok"},
    )
    import src.userbot.llm_flow as llm_flow_module

    monkeypatch.setattr(
        llm_flow_module,
        "_resolve_openclaw_stream_timeouts",
        lambda **kwargs: (0.01, 0.05),
    )
    monkeypatch.setattr(
        llm_flow_module,
        "_resolve_openclaw_buffered_response_timeout",
        lambda **kwargs: 0.08,
    )
    monkeypatch.setattr(
        llm_flow_module,
        "_resolve_openclaw_progress_notice_schedule",
        lambda **kwargs: (0.01, 0.05),
    )
    monkeypatch.setattr(
        llm_flow_module,
        "_build_openclaw_slow_wait_notice",
        lambda **kwargs: "SLOW_NOTICE",
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        False,
        raising=False,
    )

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    delivered_text = bot._deliver_response_parts.await_args.kwargs["full_response"]
    assert delivered_text == "Готовый buffered-ответ"
    edited_texts = [call.args[1] for call in bot._safe_edit.await_args_list]
    assert any("SLOW_NOTICE" in text for text in edited_texts)


@pytest.mark.asyncio
async def test_text_route_emits_tool_progress_notice_before_regular_progress_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool-progress notice должен появиться раньше длинного generic progress schedule."""
    bot = _make_buffered_bot_stub()
    incoming = SimpleNamespace(
        id=11,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Открой страницу и проверь цену",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=124, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(
            return_value=SimpleNamespace(chat=SimpleNamespace(id=124), text="", caption="")
        ),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )

    async def _fake_stream(**kwargs):
        _ = kwargs
        await asyncio.sleep(0.03)
        yield "Готово"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {"model": "openai-codex/gpt-5.4", "channel": "planning", "status": "ok"},
    )
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_active_tool_calls_summary",
        lambda: "🔧 Выполняется: browser\nИнструментов: 0/1",
    )
    import src.userbot.llm_flow as llm_flow_module

    monkeypatch.setattr(
        llm_flow_module,
        "_resolve_openclaw_stream_timeouts",
        lambda **kwargs: (0.05, 0.05),
    )
    monkeypatch.setattr(
        llm_flow_module,
        "_resolve_openclaw_buffered_response_timeout",
        lambda **kwargs: 0.20,
    )
    monkeypatch.setattr(
        llm_flow_module,
        "_resolve_openclaw_progress_notice_schedule",
        lambda **kwargs: (10.0, 30.0),
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "OPENCLAW_TOOL_PROGRESS_POLL_SEC",
        0.01,
        raising=False,
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        False,
        raising=False,
    )

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    edited_texts = [call.args[1] for call in bot._safe_edit.await_args_list]
    assert any("Выполняется: browser" in text for text in edited_texts)


@pytest.mark.asyncio
async def test_voice_route_uses_typing_during_processing_and_upload_audio_on_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice route должен показывать typing во время обработки и upload_audio перед send_voice."""
    bot = _make_buffered_bot_stub()
    bot.voice_mode = True
    bot._should_send_voice_reply = Mock(return_value=True)
    incoming = SimpleNamespace(
        id=12,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Ответь голосом",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=125, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(
            return_value=SimpleNamespace(chat=SimpleNamespace(id=125), text="", caption="")
        ),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )

    async def _fake_stream(**kwargs):
        _ = kwargs
        yield "Голосовой ответ готов."

    fd, voice_path_raw = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    Path(voice_path_raw).write_bytes(b"voice")

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {"model": "openai-codex/gpt-5.4", "channel": "planning", "status": "ok"},
    )
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        False,
        raising=False,
    )
    import src.voice_engine as voice_engine_module

    monkeypatch.setattr(
        voice_engine_module,
        "text_to_speech",
        AsyncMock(return_value=voice_path_raw),
    )

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    sent_actions = [call.args[1] for call in bot.client.send_chat_action.await_args_list]
    assert sent_actions
    assert sent_actions[0] == enums.ChatAction.TYPING
    assert enums.ChatAction.UPLOAD_AUDIO in sent_actions
    bot.client.send_voice.assert_awaited()


@pytest.mark.asyncio
async def test_mark_incoming_item_background_started_updates_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Background handoff должен переводить persisted owner item в `acked`."""
    bot = _make_buffered_bot_stub()
    captured: dict[str, object] = {}

    def _fake_set_status_by_dedupe(dedupe_key: str, **kwargs):
        captured["dedupe_key"] = dedupe_key
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(
        userbot_bridge_module.inbox_service, "set_status_by_dedupe", _fake_set_status_by_dedupe
    )

    result = bot._mark_incoming_item_background_started(
        incoming_item_result={
            "ok": True,
            "item": {
                "metadata": {
                    "chat_id": "123",
                    "message_id": "456",
                }
            },
        }
    )

    assert result["ok"] is True
    assert captured["dedupe_key"] == "incoming:123:456"
    assert captured["kwargs"]["status"] == "acked"


@pytest.mark.asyncio
async def test_deliver_response_parts_prefers_send_message_for_background() -> None:
    """Deferred-path должен отправлять финальный ответ отдельным сообщением."""
    bot = _make_buffered_bot_stub()
    bot._deliver_response_parts = KraabUserbot._deliver_response_parts.__get__(bot, KraabUserbot)
    sent_message = SimpleNamespace(id=999)
    bot.client.send_message = AsyncMock(return_value=sent_message)
    source_message = SimpleNamespace(chat=SimpleNamespace(id=555), reply=AsyncMock())
    temp_message = SimpleNamespace(id=444, delete=AsyncMock())

    result = await bot._deliver_response_parts(
        source_message=source_message,
        temp_message=temp_message,
        is_self=False,
        query="Привет",
        full_response="Готовый ответ",
        prefer_send_message_for_background=True,
    )

    assert result["delivery_mode"] == "send_message"
    bot.client.send_message.assert_awaited_once_with(555, "Готовый ответ")
    temp_message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_serialized_defers_long_text_route_to_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Длинный текстовый путь должен быстро уходить в background-task и не держать caller await-ом."""
    bot = _make_buffered_bot_stub()
    incoming = SimpleNamespace(
        id=12,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Сделай долгую задачу",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=125, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(
            return_value=SimpleNamespace(
                chat=SimpleNamespace(id=125), id=901, text="", caption="", delete=AsyncMock()
            )
        ),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )
    background_started = asyncio.Event()
    background_release = asyncio.Event()

    async def _fake_run_llm_request_flow(**kwargs):
        _ = kwargs
        background_started.set()
        await background_release.wait()

    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        True,
        raising=False,
    )
    monkeypatch.setattr(bot, "_run_llm_request_flow", _fake_run_llm_request_flow)
    monkeypatch.setattr(
        bot, "_mark_incoming_item_background_started", Mock(return_value={"ok": True})
    )

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    task = bot._get_active_chat_background_task(str(incoming.chat.id))
    assert task is not None
    await asyncio.wait_for(background_started.wait(), timeout=0.2)
    bot._mark_incoming_item_background_started.assert_called_once()
    edited_texts = [call.args[1] for call in bot._safe_edit.await_args_list]
    assert any("в фоне" in text.lower() for text in edited_texts)
    assert any("отдельным сообщением" in text.lower() for text in edited_texts)

    background_release.set()
    await asyncio.wait_for(task, timeout=0.2)


@pytest.mark.asyncio
async def test_process_message_serialized_falls_back_to_send_message_when_initial_reply_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Стартовый ack не должен ломать background-path, если Telegram валит reply."""
    bot = _make_buffered_bot_stub()
    placeholder = SimpleNamespace(
        chat=SimpleNamespace(id=126), id=902, text="", caption="", delete=AsyncMock()
    )
    bot.client.send_message = AsyncMock(return_value=placeholder)
    incoming = SimpleNamespace(
        id=13,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Сделай ещё одну долгую задачу",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=126, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(side_effect=RuntimeError("MESSAGE_ID_INVALID")),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )
    background_started = asyncio.Event()
    background_release = asyncio.Event()

    async def _fake_run_llm_request_flow(**kwargs):
        _ = kwargs
        background_started.set()
        await background_release.wait()

    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        True,
        raising=False,
    )
    monkeypatch.setattr(bot, "_run_llm_request_flow", _fake_run_llm_request_flow)
    monkeypatch.setattr(
        bot, "_mark_incoming_item_background_started", Mock(return_value={"ok": True})
    )

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    bot.client.send_message.assert_awaited()
    await asyncio.wait_for(background_started.wait(), timeout=0.2)
    task = bot._get_active_chat_background_task(str(incoming.chat.id))
    assert task is not None

    background_release.set()
    await asyncio.wait_for(task, timeout=0.2)


@pytest.mark.asyncio
async def test_process_message_serialized_queues_after_active_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Новый запрос не должен падать в inline-path, если в чате уже есть background-task."""
    bot = _make_buffered_bot_stub()
    incoming = SimpleNamespace(
        id=13,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Сделай ещё одну долгую задачу",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=126, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(
            return_value=SimpleNamespace(
                chat=SimpleNamespace(id=126), id=902, text="", caption="", delete=AsyncMock()
            )
        ),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )
    previous_release = asyncio.Event()
    queued_started = asyncio.Event()

    async def _previous_task() -> None:
        await previous_release.wait()

    async def _fake_finish_after_previous(*, previous_task: asyncio.Task, **kwargs) -> None:
        _ = kwargs
        await previous_task
        queued_started.set()

    previous_task = asyncio.create_task(_previous_task())
    bot._register_chat_background_task(str(incoming.chat.id), previous_task)

    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        bot, "_mark_incoming_item_background_started", Mock(return_value={"ok": True})
    )
    monkeypatch.setattr(
        bot, "_finish_ai_request_background_after_previous", _fake_finish_after_previous
    )
    bot._run_llm_request_flow = AsyncMock()

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    bot._run_llm_request_flow.assert_not_awaited()
    bot._mark_incoming_item_background_started.assert_called_once()
    edited_texts = [call.args[1] for call in bot._safe_edit.await_args_list]
    assert any("поставлен сразу за ней" in text.lower() for text in edited_texts)

    previous_release.set()
    await asyncio.wait_for(queued_started.wait(), timeout=0.2)


@pytest.mark.asyncio
async def test_get_active_chat_background_task_cancels_stale_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale background-task должен отменяться и не блокировать новый запрос."""
    bot = _make_buffered_bot_stub()
    release = asyncio.Event()

    async def _stuck_task() -> None:
        await release.wait()

    task = asyncio.create_task(_stuck_task())
    bot._register_chat_background_task("777", task)
    bot._chat_background_task_started_at["777"] = 10.0

    monkeypatch.setattr(userbot_bridge_module.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_TASK_STALE_TIMEOUT_SEC",
        60.0,
        raising=False,
    )

    active_task = bot._get_active_chat_background_task("777")

    assert active_task is None
    assert "777" not in bot._chat_background_tasks
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_process_message_serialized_survives_initial_ack_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой на первом Telegram-ack не должен оставлять owner request без background handoff."""
    bot = _make_buffered_bot_stub()
    incoming = SimpleNamespace(
        id=14,
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Короткий текст",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        chat=SimpleNamespace(id=127, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(side_effect=RuntimeError("telegram reply failed")),
    )
    access_profile = AccessProfile(
        level=AccessLevel.FULL,
        source="unit-test",
        matched_subject="tester",
    )
    background_started = asyncio.Event()

    async def _fake_finish_background(**kwargs) -> None:
        _ = kwargs
        background_started.set()

    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        bot, "_mark_incoming_item_background_started", Mock(return_value={"ok": True})
    )
    monkeypatch.setattr(bot, "_finish_ai_request_background", _fake_finish_background)

    await bot._process_message_serialized(
        message=incoming,
        user=incoming.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(incoming.chat.id),
    )

    await asyncio.wait_for(background_started.wait(), timeout=0.2)
    bot._mark_incoming_item_background_started.assert_called_once()
