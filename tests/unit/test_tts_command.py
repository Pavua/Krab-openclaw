# -*- coding: utf-8 -*-
"""
Тесты обработчика !tts (handle_tts).

Покрываем:
1) !tts <текст> — русский язык по умолчанию (Milena)
2) !tts en <текст> — английский (Samantha)
3) !tts es <текст> — испанский (Monica)
4) !tts (reply на сообщение) — озвучивает текст ответа
5) !tts без аргументов и без reply — UserInputError с подсказкой
6) !tts en без текста и без reply — UserInputError
7) Ошибка say (returncode != 0) → UserInputError
8) Ошибка ffmpeg (нет файла) → UserInputError
9) Неизвестный язык — fallback на русский (Milena)
10) Reply с caption вместо text
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.command_handlers import UserInputError, handle_tts

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message(
    text: str, reply_text: str | None = None, reply_caption: str | None = None
) -> SimpleNamespace:
    """Stub Pyrogram Message с текстом и опциональным reply."""
    replied = None
    if reply_text is not None or reply_caption is not None:
        replied = SimpleNamespace(
            text=reply_text,
            caption=reply_caption,
        )
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=-1001000000001),
        reply=AsyncMock(),
        reply_to_message=replied,
    )


def _make_bot() -> MagicMock:
    """Stub KraabUserbot с send_voice и _get_command_args."""
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.send_voice = AsyncMock()

    def _get_args(message):
        # Эмулирует bot._get_command_args: возвращает всё после команды
        text = getattr(message, "text", "") or ""
        parts = text.split(None, 1)
        return parts[1] if len(parts) > 1 else ""

    bot._get_command_args = _get_args
    return bot


def _make_successful_subprocess(returncode: int = 0):
    """Mock для asyncio.create_subprocess_exec — имитирует успешный процесс."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=returncode)
    proc.stdout = AsyncMock()
    proc.stderr = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# Хелпер: patching subprocess + tempfile для успешного сценария
# ---------------------------------------------------------------------------


class _FakeOgg:
    """Контекст-менеджер: создаёт временный ogg-файл с байтами во время теста."""

    def __init__(self):
        self._tmpdir = None

    def __enter__(self):
        import tempfile

        self._tmpdir = tempfile.mkdtemp(prefix="test_tts_")
        self.ogg_path = os.path.join(self._tmpdir, "speech.ogg")
        # Пишем минимальный контент чтобы os.path.exists + getsize прошли
        with open(self.ogg_path, "wb") as f:
            f.write(b"\x00" * 128)
        return self

    def __exit__(self, *args):
        import shutil

        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)


def _patch_tts_success(monkeypatch):
    """
    Патчит subprocess и tempfile так, чтобы handle_tts думал, что say и ffmpeg
    отработали успешно и ogg-файл существует.
    """
    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    call_order = [say_proc, ffmpeg_proc]

    async def fake_exec(*args, **kwargs):
        return call_order.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    # Подменяем os.path.exists и os.path.getsize чтобы ogg "существовал"
    real_exists = os.path.exists
    real_getsize = os.path.getsize

    def fake_exists(path):
        if path.endswith("speech.ogg"):
            return True
        return real_exists(path)

    def fake_getsize(path):
        if path.endswith("speech.ogg"):
            return 128
        return real_getsize(path)

    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(os.path, "getsize", fake_getsize)


# ---------------------------------------------------------------------------
# Тесты: язык по умолчанию (русский)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_default_russian(monkeypatch) -> None:
    """!tts <текст> — вызывает say с голосом Milena, отправляет voice."""
    _patch_tts_success(monkeypatch)
    bot = _make_bot()
    msg = _make_message("!tts Привет, мир!")

    captured_args = []

    async def fake_exec(*args, **kwargs):
        captured_args.append(args)
        proc = _make_successful_subprocess(0)
        if "ffmpeg" in str(args):
            # ffmpeg вызывается вторым
            pass
        return proc

    # Нужен свежий call_order для каждого вызова
    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        captured_args.append(args)
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    monkeypatch.setattr(
        os.path,
        "exists",
        lambda p: (
            p.endswith("speech.ogg") or os.path.exists.__wrapped__(p)
            if hasattr(os.path.exists, "__wrapped__")
            else True
        ),
    )
    monkeypatch.setattr(os.path, "getsize", lambda p: 128)

    await handle_tts(bot, msg)

    # Первый вызов — say, должен содержать "Milena"
    assert any("Milena" in str(a) for a in captured_args[0]), "say должен использовать голос Milena"
    bot.client.send_voice.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_english(monkeypatch) -> None:
    """!tts en <текст> — голос Samantha."""
    bot = _make_bot()
    msg = _make_message("!tts en Hello world")

    captured = []
    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        captured.append(args)
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 128)

    await handle_tts(bot, msg)

    assert any("Samantha" in str(a) for a in captured[0]), "say должен использовать Samantha"
    bot.client.send_voice.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_spanish(monkeypatch) -> None:
    """!tts es <текст> — голос Monica."""
    bot = _make_bot()
    msg = _make_message("!tts es Hola mundo")

    captured = []
    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        captured.append(args)
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 128)

    await handle_tts(bot, msg)

    assert any("Monica" in str(a) for a in captured[0]), "say должен использовать Monica"
    bot.client.send_voice.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: reply на сообщение
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_reply_uses_replied_text(monkeypatch) -> None:
    """!tts в reply → озвучивает текст replied сообщения."""
    bot = _make_bot()
    msg = _make_message("!tts", reply_text="Текст ответного сообщения")

    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 128)

    await handle_tts(bot, msg)
    bot.client.send_voice.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_reply_uses_caption(monkeypatch) -> None:
    """!tts в reply на медиа с caption → озвучивает caption."""
    bot = _make_bot()
    msg = _make_message("!tts", reply_text=None, reply_caption="Подпись к фото")

    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 128)

    await handle_tts(bot, msg)
    bot.client.send_voice.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: отсутствие текста → UserInputError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_no_args_no_reply_raises() -> None:
    """!tts без аргументов и без reply → UserInputError с подсказкой."""
    bot = _make_bot()
    msg = _make_message("!tts")

    with pytest.raises(UserInputError) as exc_info:
        await handle_tts(bot, msg)

    assert (
        "tts" in exc_info.value.user_message.lower() or "say" in exc_info.value.user_message.lower()
    )


@pytest.mark.asyncio
async def test_tts_lang_only_no_text_no_reply_raises() -> None:
    """!tts en (без текста и без reply) → UserInputError."""
    bot = _make_bot()
    msg = _make_message("!tts en")

    with pytest.raises(UserInputError):
        await handle_tts(bot, msg)


@pytest.mark.asyncio
async def test_tts_reply_empty_text_raises() -> None:
    """!tts в reply на пустое сообщение → UserInputError."""
    bot = _make_bot()
    msg = _make_message("!tts", reply_text="   ", reply_caption=None)

    with pytest.raises(UserInputError):
        await handle_tts(bot, msg)


# ---------------------------------------------------------------------------
# Тесты: ошибки say и ffmpeg
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_say_failure_raises(monkeypatch) -> None:
    """Если say возвращает ненулевой код — UserInputError."""
    bot = _make_bot()
    msg = _make_message("!tts Привет")

    say_proc = _make_successful_subprocess(1)  # returncode=1 — ошибка

    async def fake_exec(*args, **kwargs):
        return say_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(UserInputError) as exc_info:
        await handle_tts(bot, msg)

    assert (
        "say" in exc_info.value.user_message.lower()
        or "ошибкой" in exc_info.value.user_message.lower()
    )
    bot.client.send_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_tts_ffmpeg_no_output_raises(monkeypatch) -> None:
    """Если ffmpeg не создаёт файл (getsize=0 или not exists) — UserInputError."""
    bot = _make_bot()
    msg = _make_message("!tts Привет")

    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    # ogg не существует — ffmpeg "упал"
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(os.path, "getsize", lambda p: 0)

    with pytest.raises(UserInputError) as exc_info:
        await handle_tts(bot, msg)

    assert (
        "ffmpeg" in exc_info.value.user_message.lower()
        or "аудио" in exc_info.value.user_message.lower()
    )
    bot.client.send_voice.assert_not_awaited()


# ---------------------------------------------------------------------------
# Тесты: неизвестный "язык" — трактуется как часть текста
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_unknown_lang_treated_as_text(monkeypatch) -> None:
    """!tts xyz Привет — 'xyz' не является языковым кодом, весь аргумент = текст, голос Milena."""
    bot = _make_bot()
    msg = _make_message("!tts xyz Привет")

    captured = []
    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        captured.append(args)
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 128)

    await handle_tts(bot, msg)

    # Голос должен быть Milena (дефолт для ru)
    assert any("Milena" in str(a) for a in captured[0])
    # Текст должен содержать "xyz Привет" (весь raw)
    full_args_str = " ".join(str(a) for a in captured[0])
    assert "xyz" in full_args_str
    bot.client.send_voice.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: алиасы языков
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lang_code,expected_voice",
    [
        ("ru", "Milena"),
        ("en", "Samantha"),
        ("es", "Monica"),
    ],
)
@pytest.mark.asyncio
async def test_tts_lang_voices(lang_code: str, expected_voice: str, monkeypatch) -> None:
    """Проверяем маппинг lang → voice для всех поддерживаемых языков."""
    bot = _make_bot()
    msg = _make_message(f"!tts {lang_code} Текст")

    captured = []
    say_proc = _make_successful_subprocess(0)
    ffmpeg_proc = _make_successful_subprocess(0)
    _calls = [say_proc, ffmpeg_proc]

    async def ordered_exec(*args, **kwargs):
        captured.append(args)
        return _calls.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", ordered_exec)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 128)

    await handle_tts(bot, msg)

    assert any(expected_voice in str(a) for a in captured[0]), (
        f"Для языка '{lang_code}' ожидался голос '{expected_voice}'"
    )
    bot.client.send_voice.assert_awaited_once()
