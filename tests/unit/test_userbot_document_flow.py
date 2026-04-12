# -*- coding: utf-8 -*-
"""
Тесты обработки document-сообщений в userbot_bridge.

Покрываем:
1) текстовый файл <= _DOC_INLINE_BYTES → содержимое встраивается в query;
2) бинарный файл → путь добавляется в query без чтения;
3) файл > _DOC_MAX_BYTES → явная ошибка, AI не вызывается;
4) таймаут download_media → явная ошибка, AI не вызывается.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

from src.userbot_bridge import KraabUserbot


def _make_doc_bot() -> KraabUserbot:
    """Минимальный stub KraabUserbot для document-flow тестов."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()
    bot._batched_followup_message_ids = {}
    bot._chat_processing_locks = {}

    bot._message_has_audio = Mock(return_value=False)
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="")
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
    bot._build_runtime_chat_scope_id = Mock(return_value="runtime-chat-doc")
    bot._build_effective_user_query = Mock(side_effect=lambda *, query, has_images: query)
    bot._extract_live_stream_text = Mock(side_effect=lambda text, allow_reasoning=False: text)
    bot._strip_transport_markup = Mock(side_effect=lambda text: text)
    bot._apply_deferred_action_guard = Mock(side_effect=lambda text: text)
    bot._remember_hidden_reasoning_trace = Mock()
    bot._should_send_voice_reply = Mock(return_value=False)
    bot._should_send_full_text_reply = Mock(return_value=True)
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
    return bot


def _make_doc_message(
    *,
    file_name: str = "test.txt",
    mime_type: str = "text/plain",
    file_size: int = 100,
    caption: str = "",
    sender_id: int = 42,
) -> SimpleNamespace:
    doc = SimpleNamespace(
        file_name=file_name,
        mime_type=mime_type,
        file_size=file_size,
    )
    return SimpleNamespace(
        id=500,
        text=caption,
        caption=caption,
        photo=None,
        voice=None,
        audio=None,
        document=doc,
        date=None,
        from_user=SimpleNamespace(id=sender_id, username="tester", is_bot=False),
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(
            return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="", id=9001)
        ),
    )


@pytest.mark.asyncio
async def test_document_text_file_inlined_into_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Текстовый файл <= _DOC_INLINE_BYTES встраивается в запрос к модели."""
    import src.userbot_bridge as bridge_module

    bot = _make_doc_bot()
    msg = _make_doc_message(file_name="hello.txt", mime_type="text/plain", file_size=50)

    # Имитируем download_media: пишем файл вручную
    async def _fake_download(message, *, file_name: str):
        path = Path(file_name)
        path.write_text("Hello from the document!", encoding="utf-8")
        return str(path)

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        download_media=_fake_download,
        get_chat_history=AsyncMock(),
    )

    sent_queries: list[str] = []

    async def _fake_stream(**kwargs):
        sent_queries.append(kwargs["message"])
        yield "Ответ"

    monkeypatch.setattr(bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(bridge_module.config, "DOCUMENT_DOWNLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False
    )

    from src.core.access_control import AccessLevel, AccessProfile

    access_profile = AccessProfile(
        level=AccessLevel.FULL, source="unit-test", matched_subject="tester"
    )
    await bot._process_message_serialized(
        message=msg,
        user=msg.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(msg.chat.id),
    )

    assert len(sent_queries) == 1
    assert "Hello from the document!" in sent_queries[0]
    assert "hello.txt" in sent_queries[0]


@pytest.mark.asyncio
async def test_document_oversized_aborts_with_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Файл > _DOC_MAX_BYTES: явная ошибка, AI не вызывается."""
    import src.userbot_bridge as bridge_module

    bot = _make_doc_bot()
    big_size = KraabUserbot._DOC_MAX_BYTES + 1
    msg = _make_doc_message(
        file_name="huge.bin", mime_type="application/octet-stream", file_size=big_size
    )

    download_called = False

    async def _fake_download(message, *, file_name: str):
        nonlocal download_called
        download_called = True
        return str(file_name)

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        download_media=_fake_download,
        get_chat_history=AsyncMock(),
    )

    sent_queries: list[str] = []

    async def _fake_stream(**kwargs):
        sent_queries.append(kwargs["message"])
        yield "Ответ"

    monkeypatch.setattr(bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(bridge_module.config, "DOCUMENT_DOWNLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False
    )

    from src.core.access_control import AccessLevel, AccessProfile

    access_profile = AccessProfile(
        level=AccessLevel.FULL, source="unit-test", matched_subject="tester"
    )
    await bot._process_message_serialized(
        message=msg,
        user=msg.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(msg.chat.id),
    )

    assert not download_called, "download_media не должен вызываться для oversized файла"
    assert sent_queries == [], "AI не должен вызываться при ошибке"
    bot._safe_edit.assert_awaited()
    last_call_text = bot._safe_edit.await_args_list[-1].args[-1]
    assert "слишком большой" in last_call_text or "KB" in last_call_text


@pytest.mark.asyncio
async def test_document_download_timeout_aborts_with_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Таймаут download_media → явная ошибка, AI не вызывается."""
    import src.userbot_bridge as bridge_module

    bot = _make_doc_bot()
    msg = _make_doc_message(file_name="slow.pdf", mime_type="application/pdf", file_size=1024)

    async def _timeout_download(message, *, file_name: str):
        raise asyncio.TimeoutError()

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        download_media=_timeout_download,
        get_chat_history=AsyncMock(),
    )

    sent_queries: list[str] = []

    async def _fake_stream(**kwargs):
        sent_queries.append(kwargs["message"])
        yield "Ответ"

    monkeypatch.setattr(bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(bridge_module.config, "DOCUMENT_DOWNLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False
    )

    from src.core.access_control import AccessLevel, AccessProfile

    access_profile = AccessProfile(
        level=AccessLevel.FULL, source="unit-test", matched_subject="tester"
    )
    await bot._process_message_serialized(
        message=msg,
        user=msg.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(msg.chat.id),
    )

    assert sent_queries == [], "AI не должен вызываться при таймауте"
    bot._safe_edit.assert_awaited()
    last_call_text = bot._safe_edit.await_args_list[-1].args[-1]
    assert "Таймаут" in last_call_text or "таймаут" in last_call_text


@pytest.mark.asyncio
async def test_document_binary_passes_path_in_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Бинарный файл (не текст) передаётся как путь, не встраивается inline."""
    import src.userbot_bridge as bridge_module

    bot = _make_doc_bot()
    msg = _make_doc_message(file_name="report.pdf", mime_type="application/pdf", file_size=2048)

    async def _fake_download(message, *, file_name: str):
        path = Path(file_name)
        path.write_bytes(b"%PDF-1.4 fake content")
        return str(path)

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_message=AsyncMock(),
        send_voice=AsyncMock(),
        download_media=_fake_download,
        get_chat_history=AsyncMock(),
    )

    sent_queries: list[str] = []

    async def _fake_stream(**kwargs):
        sent_queries.append(kwargs["message"])
        yield "Ответ"

    monkeypatch.setattr(bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(bridge_module.config, "DOCUMENT_DOWNLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        bridge_module.config, "USERBOT_BACKGROUND_LLM_HANDOFF", False, raising=False
    )

    from src.core.access_control import AccessLevel, AccessProfile

    access_profile = AccessProfile(
        level=AccessLevel.FULL, source="unit-test", matched_subject="tester"
    )
    await bot._process_message_serialized(
        message=msg,
        user=msg.from_user,
        access_profile=access_profile,
        is_allowed_sender=True,
        chat_id=str(msg.chat.id),
    )

    assert len(sent_queries) == 1
    assert "report.pdf" in sent_queries[0]
    # Путь к файлу (не содержимое PDF) должен быть в запросе
    assert (
        "Файл сохранён" in sent_queries[0]
        or "krab_docs" in sent_queries[0]
        or tmp_path.name in sent_queries[0]
    )
    # Содержимое PDF (raw bytes) не должно быть вставлено
    assert "%PDF" not in sent_queries[0]
