# -*- coding: utf-8 -*-
"""
Wave 16-G: транскрипция аудио из reply_to_message.

Баг: user reply'ает на audio-сообщение с текстом («оцени трек»).
Краб видел только reply_to_text: [media], но НЕ скачивал audio из
reply_to_message. Wave 16-E фиксил direct audio (message.audio),
Wave 16-G фиксит reply audio (message.reply_to_message.audio).

Тесты:
1. reply audio + caption → транскрипт добавляется к query
2. reply audio без caption → транскрипт становится query
3. reply audio + caption, транскрипция упала → query сохраняется с ошибкой
4. reply audio без caption, транскрипция упала → отправляется error-reply
5. direct audio (has_audio_message=True) имеет приоритет над reply audio
6. нет audio, нет reply audio → проваливается в text path
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.userbot.voice_profile import VoiceProfileMixin
from src.userbot_bridge import KraabUserbot

# ---------------------------------------------------------------------------
# Helpers (по образцу test_reply_media_extraction.py)
# ---------------------------------------------------------------------------


def _make_bot(owner_id: int = 777) -> KraabUserbot:
    """Минимальный KraabUserbot без __init__."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=owner_id, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()
    bot._session_messages_processed = 0
    return bot


def _make_bot_methods(bot: KraabUserbot, text: str = "оцени трек") -> KraabUserbot:
    """Добавляет стандартный набор заглушек методов (минимальный набор для _process_message)."""
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value=text)
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, t: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kw: kw["text"])
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYS")
    bot._build_runtime_chat_scope_id = Mock(return_value="123")
    # client нужен для send_chat_action в LLM path
    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(return_value=None),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )
    return bot


def _make_msg(
    *,
    text: str = "",
    caption: str | None = None,
    voice=None,
    audio=None,
    reply_voice=None,
    reply_audio=None,
    chat_type=enums.ChatType.PRIVATE,
    owner_id: int = 777,
) -> SimpleNamespace:
    """Конструирует входящее сообщение."""
    reply_to_message = None
    if reply_voice is not None or reply_audio is not None:
        reply_to_message = SimpleNamespace(
            id=101,
            voice=reply_voice,
            audio=reply_audio,
            text=None,
            caption=None,
            # from_user нужен для is_reply_to_me проверок в _process_message
            from_user=SimpleNamespace(id=99, username="sender", is_bot=False),
        )

    return SimpleNamespace(
        id=200,
        from_user=SimpleNamespace(id=owner_id, username="owner", is_bot=False),
        text=text,
        caption=caption,
        photo=None,
        voice=voice,
        audio=audio,
        document=None,
        chat=SimpleNamespace(id=42, type=chat_type),
        reply_to_message=reply_to_message,
        outgoing=False,
        reply=AsyncMock(return_value=SimpleNamespace(id=999, text="", caption="")),
    )


# ---------------------------------------------------------------------------
# Тест 1: reply audio + caption → транскрипт добавляется к query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_audio_with_caption_transcribes_and_appends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    User пишет «оцени трек» в reply на audio-сообщение.
    Ожидаем: transcript добавляется к query, LLM получает оба компонента.
    """
    fake_voice = object()  # voice в reply
    msg = _make_msg(text="оцени трек", reply_voice=fake_voice)

    bot = _make_bot()
    _make_bot_methods(bot, text="оцени трек")

    # Мок транскрипции — успешная, target_message должен быть reply_to_message
    async def _fake_transcribe(message, *, target_message=None):
        assert target_message is msg.reply_to_message
        return "привет из трека", ""

    bot._transcribe_audio_message = _fake_transcribe
    bot._safe_reply_or_send_new = AsyncMock()

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["message"] = kwargs.get("message", "")
        yield "Ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(msg)

    sent = captured.get("message", "")
    assert "оцени трек" in sent, f"Исходный query должен сохраниться, получено: {sent!r}"
    assert "привет из трека" in sent, (
        f"Транскрипт должен присутствовать в query, получено: {sent!r}"
    )
    assert "[Транскрипция reply-аудио]" in sent, (
        f"Маркер транскрипции должен быть в query, получено: {sent!r}"
    )


# ---------------------------------------------------------------------------
# Тест 2: reply audio без caption → транскрипт становится query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_audio_without_caption_uses_transcript_as_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    User reply'ает на audio-сообщение без текста (пустой reply).
    Ожидаем: транскрипт используется как весь query.
    """
    fake_audio = object()  # audio в reply
    msg = _make_msg(text="", reply_audio=fake_audio)

    bot = _make_bot()
    _make_bot_methods(bot, text="")

    async def _fake_transcribe(message, *, target_message=None):
        return "содержание аудиофайла", ""

    bot._transcribe_audio_message = _fake_transcribe
    bot._safe_reply_or_send_new = AsyncMock()

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["message"] = kwargs.get("message", "")
        yield "Ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(msg)

    sent = captured.get("message", "")
    assert "содержание аудиофайла" in sent, f"Транскрипт должен стать query, получено: {sent!r}"


# ---------------------------------------------------------------------------
# Тест 3: reply audio + caption, транскрипция упала → query сохраняется с ошибкой
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_audio_transcription_fail_with_caption_keeps_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Graceful degradation: транскрипция reply audio не удалась, но у user есть caption.
    Ожидаем: query передаётся в LLM с маркером ошибки транскрипции, не падаем.
    """
    fake_voice = object()
    msg = _make_msg(text="что это за трек?", reply_voice=fake_voice)

    bot = _make_bot()
    _make_bot_methods(bot, text="что это за трек?")

    async def _fake_transcribe(message, *, target_message=None):
        return "", "❌ Таймаут"

    bot._transcribe_audio_message = _fake_transcribe
    bot._safe_reply_or_send_new = AsyncMock()

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["message"] = kwargs.get("message", "")
        yield "Ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(msg)

    sent = captured.get("message", "")
    assert "что это за трек?" in sent, f"Исходный caption должен сохраниться, получено: {sent!r}"
    assert "транскрипция не удалась" in sent.lower() or "❌" in sent, (
        f"Маркер ошибки транскрипции должен быть в query, получено: {sent!r}"
    )
    # LLM должен был вызваться (не прерываем обработку)
    assert "message" in captured, "LLM должен вызваться даже при ошибке транскрипции"


# ---------------------------------------------------------------------------
# Тест 4: reply audio без caption, транскрипция упала → отправляется error-reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_audio_transcription_fail_no_caption_replies_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Reply audio без caption + транскрипция упала → user получает error reply,
    LLM НЕ вызывается.
    """
    fake_audio = object()
    msg = _make_msg(text="", reply_audio=fake_audio)

    bot = _make_bot()
    _make_bot_methods(bot, text="")

    async def _fake_transcribe(message, *, target_message=None):
        return "", "❌ STT не подключён"

    bot._transcribe_audio_message = _fake_transcribe

    safe_reply_calls: list = []

    async def _safe_reply(m, t):
        safe_reply_calls.append(t)

    bot._safe_reply_or_send_new = _safe_reply

    stream_called = []

    async def _fake_stream(**kwargs):
        stream_called.append(True)
        yield "Ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(msg)

    # LLM НЕ должен вызываться
    assert not stream_called, "LLM не должен вызываться при ошибке транскрипции без caption"
    # Error reply должен быть отправлен
    assert safe_reply_calls, "Пользователь должен получить error reply"
    assert any("❌" in r or "аудио" in r.lower() for r in safe_reply_calls), (
        f"Error reply должен содержать маркер ошибки, получено: {safe_reply_calls}"
    )


# ---------------------------------------------------------------------------
# Тест 5: direct audio имеет приоритет над reply audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_audio_takes_priority_over_reply_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Если у сообщения есть прямое audio (has_audio_message=True),
    reply audio path (Wave 16-G) НЕ должен активироваться.
    """
    direct_voice = object()
    reply_audio_obj = object()
    msg = _make_msg(voice=direct_voice, reply_audio=reply_audio_obj)

    bot = _make_bot()
    _make_bot_methods(bot, text="")

    transcribe_targets: list = []

    async def _fake_transcribe(message, *, target_message=None):
        transcribe_targets.append(target_message)
        return "транскрипт прямого аудио", ""

    bot._transcribe_audio_message = _fake_transcribe
    bot._safe_reply_or_send_new = AsyncMock()
    bot._is_translator_active_for_chat = Mock(return_value=False)
    bot._apply_voice_dispatcher = AsyncMock(side_effect=lambda m, q: q)

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["message"] = kwargs.get("message", "")
        yield "Ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(msg)

    # Транскрипция должна вызываться ровно один раз
    assert transcribe_targets, "Транскрипция должна вызываться"
    assert len(transcribe_targets) == 1, "Транскрипция должна вызываться ровно один раз"
    # Direct audio path: target_message=None (не reply)
    assert transcribe_targets[0] is None, (
        "Direct audio path должен вызвать transcribe без target_message, "
        f"получено: target_message={transcribe_targets[0]!r}"
    )


# ---------------------------------------------------------------------------
# Тест 6: нет audio, нет reply audio → проваливается в text path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_audio_no_reply_audio_falls_through_to_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Обычное текстовое сообщение без audio и без reply audio
    → стандартный text path, транскрипция не вызывается.
    """
    msg = _make_msg(text="просто текст")

    bot = _make_bot()
    _make_bot_methods(bot, text="просто текст")

    transcribe_called = []

    async def _fake_transcribe(message, *, target_message=None):
        transcribe_called.append(True)
        return "", ""

    bot._transcribe_audio_message = _fake_transcribe
    bot._safe_reply_or_send_new = AsyncMock()

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["message"] = kwargs.get("message", "")
        yield "Текстовый ответ"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(msg)

    # Транскрипция НЕ должна вызываться для текстового сообщения
    assert not transcribe_called, "Транскрипция не должна вызываться для text-only сообщения"
    # LLM должен получить исходный текст
    sent = captured.get("message", "")
    assert "просто текст" in sent, f"LLM должен получить исходный текст, получено: {sent!r}"


# ---------------------------------------------------------------------------
# Unit-тесты на хелперы VoiceProfileMixin (без _process_message)
# ---------------------------------------------------------------------------


def test_message_has_reply_audio_voice() -> None:
    """_message_has_reply_audio: True если reply_to_message.voice."""
    reply = SimpleNamespace(voice=object(), audio=None)
    msg = SimpleNamespace(reply_to_message=reply)
    assert VoiceProfileMixin._message_has_reply_audio(msg) is True


def test_message_has_reply_audio_audio() -> None:
    """_message_has_reply_audio: True если reply_to_message.audio."""
    reply = SimpleNamespace(voice=None, audio=object())
    msg = SimpleNamespace(reply_to_message=reply)
    assert VoiceProfileMixin._message_has_reply_audio(msg) is True


def test_message_has_reply_audio_no_reply() -> None:
    """_message_has_reply_audio: False если reply_to_message отсутствует."""
    msg = SimpleNamespace(reply_to_message=None)
    assert VoiceProfileMixin._message_has_reply_audio(msg) is False


def test_message_has_reply_audio_no_audio_in_reply() -> None:
    """_message_has_reply_audio: False если reply_to_message есть, но без audio."""
    reply = SimpleNamespace(voice=None, audio=None, photo=object())
    msg = SimpleNamespace(reply_to_message=reply)
    assert VoiceProfileMixin._message_has_reply_audio(msg) is False
