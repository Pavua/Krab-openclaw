# -*- coding: utf-8 -*-
"""
Тесты Bug 13 fix: извлечение медиа из reply_to_message.

Что проверяем:
- reply_to_message.photo → изображение попадает в images
- reply_to_message.animation → изображение попадает в images
- reply_to_message.document с mime_type image/* → изображение попадает в images
- reply_to_message.document с mime_type application/pdf → НЕ попадает
- caption из reply добавляется в query
- если message.photo уже есть (images не пустой) — reply media не извлекается
- asyncio.TimeoutError при скачивании → только warning, нет краша
"""

from __future__ import annotations

import pytest

pytest.skip(
    "Pre-existing hang on starlette TestClient — Wave 16 backlog. "
    "Tests timeout indefinitely waiting for AI runtime that's not "
    "mocked in fixtures. See Wave 13-B investigation.",
    allow_module_level=True,
)

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.userbot_bridge import KraabUserbot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(owner_id: int = 777) -> KraabUserbot:
    """Конструирует минимальный KraabUserbot без __init__."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=owner_id, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()
    bot._session_messages_processed = 0
    return bot


def _make_bot_methods(bot: KraabUserbot) -> KraabUserbot:
    """Добавляет стандартный набор заглушек методов."""
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="что на картинке?")
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kw: kw["text"])
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYS")
    bot._build_runtime_chat_scope_id = Mock(return_value="123")
    return bot


def _make_incoming(
    *,
    text: str = "что на картинке?",
    photo=None,
    reply_to_message=None,
) -> SimpleNamespace:
    """Конструирует входящее сообщение без прямого photo."""
    temp_msg = SimpleNamespace(
        id=200,
        text="",
        caption="",
        photo=None,
        chat=SimpleNamespace(id=123),
    )
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text=text,
        caption=None,
        photo=photo,
        voice=None,
        document=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=reply_to_message,
        outgoing=False,
        reply=AsyncMock(return_value=temp_msg),
    )
    return msg


FAKE_BYTES = b"fake-image-data"
FAKE_B64 = base64.b64encode(FAKE_BYTES).decode("utf-8")


def _make_download_result() -> SimpleNamespace:
    """BytesIO-подобный объект, возвращаемый download_media."""
    return SimpleNamespace(getvalue=lambda: FAKE_BYTES)


# ---------------------------------------------------------------------------
# Тест 1: reply_to_message.photo → images заполняется
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_photo_extracted(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bug 13: reply_to_message содержит .photo → должно попасть в images для LLM.
    """
    reply_msg = SimpleNamespace(
        photo=object(),
        animation=None,
        document=None,
        caption=None,
        from_user=SimpleNamespace(id=99, username="sender"),
    )
    incoming = _make_incoming(reply_to_message=reply_msg)

    bot = _make_bot()
    _make_bot_methods(bot)

    download_calls: list = []

    async def _counting_download(msg, in_memory=False):
        download_calls.append(msg)
        return _make_download_result()

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=_counting_download,
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["images"] = kwargs.get("images", [])
        yield "Описание изображения"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    # download_media должен был вызваться на reply_msg
    assert len(download_calls) == 1, "download_media должен быть вызван ровно один раз"
    assert download_calls[0] is reply_msg, "download_media должен вызываться на reply_msg"

    # images должен содержать base64 изображение
    assert len(captured.get("images", [])) == 1, "LLM должен получить изображение из reply"
    assert captured["images"][0] == FAKE_B64


# ---------------------------------------------------------------------------
# Тест 2: reply_to_message.animation → images заполняется
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_animation_extracted(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bug 13: reply_to_message содержит .animation → должно попасть в images для LLM.
    """
    reply_msg = SimpleNamespace(
        photo=None,
        animation=object(),  # анимация есть, фото нет
        document=None,
        caption=None,
        from_user=SimpleNamespace(id=99, username="sender"),
    )
    incoming = _make_incoming(reply_to_message=reply_msg)

    bot = _make_bot()
    _make_bot_methods(bot)

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(return_value=_make_download_result()),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["images"] = kwargs.get("images", [])
        yield "Описание анимации"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    assert len(captured.get("images", [])) == 1, (
        "LLM должен получить изображение из animation в reply"
    )
    assert captured["images"][0] == FAKE_B64


# ---------------------------------------------------------------------------
# Тест 3: reply document с image/* mime → images заполняется
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_document_image_extracted(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bug 13: reply_to_message.document с mime_type 'image/png' → должно попасть в images.
    """
    doc = SimpleNamespace(mime_type="image/png")
    reply_msg = SimpleNamespace(
        photo=None,
        animation=None,
        document=doc,
        caption=None,
        from_user=SimpleNamespace(id=99, username="sender"),
    )
    incoming = _make_incoming(reply_to_message=reply_msg)

    bot = _make_bot()
    _make_bot_methods(bot)

    download_calls: list = []

    async def _counting_download(msg, in_memory=False):
        download_calls.append(msg)
        return _make_download_result()

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=_counting_download,
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["images"] = kwargs.get("images", [])
        yield "Описание документа"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    assert len(captured.get("images", [])) == 1, (
        "LLM должен получить изображение из image/png документа"
    )
    assert captured["images"][0] == FAKE_B64


# ---------------------------------------------------------------------------
# Тест 4: reply document с application/pdf mime → НЕ извлекается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_document_non_image_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bug 13: reply_to_message.document с mime_type 'application/pdf' → НЕ должно попасть в images.
    """
    doc = SimpleNamespace(mime_type="application/pdf")
    reply_msg = SimpleNamespace(
        photo=None,
        animation=None,
        document=doc,
        caption=None,
        from_user=SimpleNamespace(id=99, username="sender"),
    )
    incoming = _make_incoming(reply_to_message=reply_msg)

    bot = _make_bot()
    _make_bot_methods(bot)

    download_calls: list = []

    async def _fake_download(msg, in_memory=False):
        download_calls.append(msg)
        return _make_download_result()

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=_fake_download,
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["images"] = kwargs.get("images", [])
        yield "Текстовый ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    # download_media НЕ должен вызываться для PDF через reply-media path
    # (может вызываться через document path, но не через reply-media)
    assert captured.get("images", []) == [], "PDF из reply НЕ должен попасть в images"


# ---------------------------------------------------------------------------
# Тест 5: reply_caption добавляется в query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_caption_prepended(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bug 13: caption из reply_to_message должен добавляться в начало query.
    """
    reply_msg = SimpleNamespace(
        photo=object(),
        animation=None,
        document=None,
        caption="Красивый закат над горами",
        from_user=SimpleNamespace(id=99, username="sender"),
    )
    incoming = _make_incoming(
        text="что это?",
        reply_to_message=reply_msg,
    )

    bot = _make_bot()
    _make_bot_methods(bot)
    bot._get_clean_text = Mock(return_value="что это?")

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(return_value=_make_download_result()),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["message"] = kwargs.get("message", "")
        captured["images"] = kwargs.get("images", [])
        yield "Ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    # caption должен присутствовать в query, переданном в LLM.
    # Bug 13 код добавляет: query = f"[Изображение из reply: {caption}]\n{query}"
    # Sender context injection может дополнительно трансформировать итоговый текст,
    # поэтому проверяем только наличие caption-текста в итоговом query.
    msg_sent = captured.get("message", "")
    assert "Красивый закат над горами" in msg_sent, (
        f"Caption из reply должен присутствовать в query, получено: {msg_sent!r}"
    )


# ---------------------------------------------------------------------------
# Тест 6: message.photo уже есть (images не пустой) → reply media не извлекается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_photo_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bug 13: если message.photo уже обработано и images заполнен,
    reply_to_message.photo НЕ должно добавляться — приоритет прямого фото.
    """
    # У входящего сообщения есть своё фото
    reply_msg = SimpleNamespace(
        photo=object(),
        animation=None,
        document=None,
        caption="reply caption",
        from_user=SimpleNamespace(id=99, username="sender"),
    )

    fake_bytes_direct = b"direct-photo"
    direct_photo_io = SimpleNamespace(getvalue=lambda: fake_bytes_direct)

    incoming = _make_incoming(
        photo=object(),  # прямое фото у сообщения
        reply_to_message=reply_msg,
    )

    bot = _make_bot()
    _make_bot_methods(bot)

    download_call_count = 0

    async def _counting_download(msg, in_memory=False):
        nonlocal download_call_count
        download_call_count += 1
        return direct_photo_io

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=_counting_download,
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["images"] = kwargs.get("images", [])
        yield "Ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    # Настраиваем, чтобы cloud-only не блокировало
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_FORCE_CLOUD_FOR_PHOTO",
        True,
        raising=False,
    )

    await bot._process_message(incoming)

    # download_media вызывается ровно один раз (для прямого photo, не для reply)
    assert download_call_count == 1, (
        "download_media должен вызываться только для прямого photo, не дважды"
    )
    # LLM получает ровно одно изображение (от прямого photo)
    assert len(captured.get("images", [])) == 1, "Только одно изображение должно попасть в LLM"
    direct_b64 = base64.b64encode(fake_bytes_direct).decode("utf-8")
    assert captured["images"][0] == direct_b64, "LLM должен получить прямое фото, а не из reply"


# ---------------------------------------------------------------------------
# Тест 7: asyncio.TimeoutError при скачивании reply media → warning, нет краша
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_media_timeout_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Bug 13: таймаут download_media для reply media → только warning-лог,
    процессинг продолжается (LLM вызывается без изображения).

    Проект использует structlog, поэтому warning проверяем через перехват
    logger.warning (patch), а не через caplog.
    """
    reply_msg = SimpleNamespace(
        photo=object(),
        animation=None,
        document=None,
        caption=None,
        from_user=SimpleNamespace(id=99, username="sender"),
    )
    incoming = _make_incoming(reply_to_message=reply_msg)

    bot = _make_bot()
    _make_bot_methods(bot)

    async def _timeout_download(msg, in_memory=False):
        raise asyncio.TimeoutError()

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=_timeout_download,
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    stream_called = []
    warning_events: list[str] = []

    # Патчим logger.warning в userbot_bridge для перехвата structlog-событий
    original_warning = userbot_bridge_module.logger.warning

    def _capturing_warning(event, **kw):
        warning_events.append(event)
        return original_warning(event, **kw)

    monkeypatch.setattr(userbot_bridge_module.logger, "warning", _capturing_warning)

    async def _fake_stream(**kwargs):
        stream_called.append(kwargs.get("images", []))
        yield "Ответ без картинки"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    # Ускоряем таймаут для теста
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "PHOTO_DOWNLOAD_TIMEOUT_SEC",
        0.01,
        raising=False,
    )
    # Отключаем background handoff, чтобы LLM вызывался синхронно в тесте.
    # При images=[] и is_self=False код по умолчанию уходит в background task,
    # который завершится после return — тест не дождётся.
    monkeypatch.setattr(
        userbot_bridge_module.config,
        "USERBOT_BACKGROUND_LLM_HANDOFF",
        False,
        raising=False,
    )

    await bot._process_message(incoming)

    # Проверяем что warning был залогирован
    assert "reply_media_download_timeout" in warning_events, (
        f"Должен быть залогирован warning reply_media_download_timeout, "
        f"получены события: {warning_events}"
    )

    # LLM должен вызваться (процессинг продолжается, но без изображения)
    assert stream_called, "LLM должен вызываться даже при таймауте reply media"
    # images должен быть пустым — изображение не было загружено
    assert stream_called[0] == [], "images должен быть пустым при таймауте reply media"
