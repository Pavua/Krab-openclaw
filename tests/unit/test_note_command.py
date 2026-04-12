# -*- coding: utf-8 -*-
"""
Тесты команды !note — голосовая заметка в Obsidian.

Покрываем:
1) handle_note без reply → UserInputError
2) handle_note с reply на не-аудио сообщение → UserInputError
3) handle_note с reply на голосовое → транскрибация + сохранение
4) handle_note с тегом → тег попадает в frontmatter
5) handle_note при ошибке транскрибации → сообщение об ошибке
6) memo_service.save() с tags и source_type → корректный frontmatter
7) memo_service.save_async() с tags и source_type → работает
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.core.memo_service import MemoService, MemoResult
from src.core.exceptions import UserInputError


# ─── вспомогательные mock-функции ─────────────────────────────────────────────

def _make_message(
    text: str = "!note",
    reply_voice: bool = False,
    reply_audio: bool = False,
    reply_video_note: bool = False,
    has_reply: bool = True,
) -> MagicMock:
    """Создаёт мок Telegram Message для тестов."""
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.title = "Test Chat"
    msg.chat.id = 12345

    if has_reply:
        reply = MagicMock()
        reply.voice = MagicMock() if reply_voice else None
        reply.audio = MagicMock() if reply_audio else None
        reply.video_note = MagicMock() if reply_video_note else None
        msg.reply_to_message = reply
    else:
        msg.reply_to_message = None

    # reply() возвращает статусное сообщение с .edit()
    status_mock = AsyncMock()
    status_mock.edit = AsyncMock()
    msg.reply = AsyncMock(return_value=status_mock)

    return msg


def _make_bot(transcribe_result: tuple[str, str] = ("распознанный текст", "")) -> MagicMock:
    """Создаёт мок KraabUserbot."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    bot._transcribe_audio_message = AsyncMock(return_value=transcribe_result)
    return bot


# ─── тесты UserInputError ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_no_reply_raises_user_input_error():
    """!note без reply → UserInputError с инструкцией."""
    from src.handlers.command_handlers import handle_note

    bot = _make_bot()
    msg = _make_message(has_reply=False)

    with pytest.raises(UserInputError) as exc_info:
        await handle_note(bot, msg)

    assert "голосовое" in exc_info.value.user_message.lower()


@pytest.mark.asyncio
async def test_note_reply_not_audio_raises_user_input_error():
    """!note в ответ на текстовое сообщение → UserInputError."""
    from src.handlers.command_handlers import handle_note

    bot = _make_bot()
    # reply есть, но нет audio/voice/video_note
    msg = _make_message(has_reply=True, reply_voice=False, reply_audio=False)

    with pytest.raises(UserInputError) as exc_info:
        await handle_note(bot, msg)

    assert "голосовое" in exc_info.value.user_message.lower()


# ─── тесты успешного сохранения ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_voice_reply_saves_to_obsidian(tmp_path: Path):
    """!note с reply на голосовое → транскрибирует и сохраняет заметку."""
    from src.handlers.command_handlers import handle_note

    inbox = tmp_path / "00_Inbox"
    inbox.mkdir()

    bot = _make_bot(transcribe_result=("привет мир", ""))
    bot._get_command_args = MagicMock(return_value="")
    msg = _make_message(has_reply=True, reply_voice=True)

    with patch("src.core.memo_service.memo_service") as mock_memo:
        saved_path = inbox / "2026-01-01_12-00_memo.md"
        mock_memo.save_async = AsyncMock(return_value=MemoResult(
            success=True,
            message="Заметка сохранена: `2026-01-01_12-00_memo.md`",
            file_path=saved_path,
        ))
        await handle_note(bot, msg)

    # Проверяем что save_async вызван с правильными аргументами
    mock_memo.save_async.assert_awaited_once()
    call_kwargs = mock_memo.save_async.call_args

    # Текст должен содержать [voice] и транскрипт
    text_arg = call_kwargs[1].get("text") or call_kwargs[0][0]
    assert "[voice]" in text_arg
    assert "привет мир" in text_arg

    # source_type должен быть krab-voice
    source_type = call_kwargs[1].get("source_type", "")
    assert source_type == "krab-voice"

    # Теги должны содержать voice
    tags = call_kwargs[1].get("tags", [])
    assert "voice" in tags


@pytest.mark.asyncio
async def test_note_with_tag(tmp_path: Path):
    """!note идея → тег 'идея' добавляется к заметке."""
    from src.handlers.command_handlers import handle_note

    inbox = tmp_path / "00_Inbox"
    inbox.mkdir()

    bot = _make_bot(transcribe_result=("отличная мысль", ""))
    bot._get_command_args = MagicMock(return_value="идея")
    msg = _make_message(has_reply=True, reply_voice=True)

    with patch("src.core.memo_service.memo_service") as mock_memo:
        saved_path = inbox / "2026-01-01_12-00_memo.md"
        mock_memo.save_async = AsyncMock(return_value=MemoResult(
            success=True,
            message="Заметка сохранена",
            file_path=saved_path,
        ))
        await handle_note(bot, msg)

    call_kwargs = mock_memo.save_async.call_args
    tags = call_kwargs[1].get("tags", [])
    assert "идея" in tags
    assert "voice" in tags


@pytest.mark.asyncio
async def test_note_audio_reply_also_works():
    """!note в ответ на audio (не voice) тоже работает."""
    from src.handlers.command_handlers import handle_note

    bot = _make_bot(transcribe_result=("аудио текст", ""))
    bot._get_command_args = MagicMock(return_value="")
    msg = _make_message(has_reply=True, reply_audio=True)

    with patch("src.core.memo_service.memo_service") as mock_memo:
        mock_memo.save_async = AsyncMock(return_value=MemoResult(
            success=True,
            message="Заметка сохранена",
            file_path=Path("/tmp/test.md"),
        ))
        await handle_note(bot, msg)

    mock_memo.save_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_video_note_reply_also_works():
    """!note в ответ на video_note тоже работает."""
    from src.handlers.command_handlers import handle_note

    bot = _make_bot(transcribe_result=("кружок текст", ""))
    bot._get_command_args = MagicMock(return_value="")
    msg = _make_message(has_reply=True, reply_video_note=True)

    with patch("src.core.memo_service.memo_service") as mock_memo:
        mock_memo.save_async = AsyncMock(return_value=MemoResult(
            success=True,
            message="Заметка сохранена",
            file_path=Path("/tmp/test.md"),
        ))
        await handle_note(bot, msg)

    mock_memo.save_async.assert_awaited_once()


# ─── тест ошибки транскрибации ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_transcription_error_shows_error():
    """!note при ошибке STT → показывает сообщение об ошибке, не сохраняет."""
    from src.handlers.command_handlers import handle_note

    bot = _make_bot(transcribe_result=("", "❌ STT сервис недоступен"))
    bot._get_command_args = MagicMock(return_value="")
    msg = _make_message(has_reply=True, reply_voice=True)

    with patch("src.core.memo_service.memo_service") as mock_memo:
        mock_memo.save_async = AsyncMock()
        await handle_note(bot, msg)

    # save_async НЕ должен быть вызван
    mock_memo.save_async.assert_not_awaited()

    # edit должен содержать ошибку
    status_mock = msg.reply.return_value
    status_mock.edit.assert_awaited_once()
    edited_text = status_mock.edit.call_args[0][0]
    assert "STT" in edited_text or "❌" in edited_text


# ─── тесты memo_service с новыми параметрами ──────────────────────────────────

def test_memo_save_with_tags_in_frontmatter(tmp_path: Path):
    """save() с tags → теги попадают в YAML frontmatter."""
    inbox = tmp_path / "00_Inbox"
    svc = MemoService(inbox_dir=inbox)

    result = svc.save(
        text="тест заметки",
        chat_title="Тест Чат",
        tags=["voice", "идея"],
        source_type="krab-voice",
    )

    assert result.success
    assert result.file_path is not None
    content = result.file_path.read_text(encoding="utf-8")

    # Проверяем frontmatter
    assert 'source: krab-voice' in content
    assert '"voice"' in content
    assert '"идея"' in content
    assert "tags:" in content


def test_memo_save_without_tags_no_tags_in_frontmatter(tmp_path: Path):
    """save() без tags → поле tags не появляется в frontmatter."""
    inbox = tmp_path / "00_Inbox"
    svc = MemoService(inbox_dir=inbox)

    result = svc.save(text="простая заметка", chat_title="Чат")

    assert result.success
    content = result.file_path.read_text(encoding="utf-8")
    assert "tags:" not in content
    assert "source: krab-telegram" in content


def test_memo_save_source_type_custom(tmp_path: Path):
    """save() с кастомным source_type → попадает в frontmatter."""
    inbox = tmp_path / "00_Inbox"
    svc = MemoService(inbox_dir=inbox)

    result = svc.save(
        text="тест",
        chat_title="Чат",
        source_type="krab-voice",
    )

    assert result.success
    content = result.file_path.read_text(encoding="utf-8")
    assert "source: krab-voice" in content


@pytest.mark.asyncio
async def test_memo_save_async_with_tags(tmp_path: Path):
    """save_async() с tags и source_type → корректно проксирует в save()."""
    inbox = tmp_path / "00_Inbox"
    svc = MemoService(inbox_dir=inbox)

    result = await svc.save_async(
        text="async голосовая заметка",
        chat_title="Чат",
        tags=["voice", "тест"],
        source_type="krab-voice",
    )

    assert result.success
    assert result.file_path is not None
    content = result.file_path.read_text(encoding="utf-8")
    assert "[voice]" not in content  # текст передаётся как есть, [voice] добавляет handle_note
    assert '"voice"' in content  # но тег есть
    assert "source: krab-voice" in content


def test_memo_save_tag_with_special_chars_sanitized(tmp_path: Path):
    """save() с тегом содержащим кавычки → кавычки убираются."""
    inbox = tmp_path / "00_Inbox"
    svc = MemoService(inbox_dir=inbox)

    result = svc.save(
        text="тест",
        chat_title="Чат",
        tags=['тег"злой'],
    )

    assert result.success
    content = result.file_path.read_text(encoding="utf-8")
    # Кавычки внутри тега должны быть убраны
    assert 'тегзлой' in content or '"тег' not in content.replace('"тегзлой"', "")
