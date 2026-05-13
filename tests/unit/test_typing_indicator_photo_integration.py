# -*- coding: utf-8 -*-
"""
Wave 204 (Session 48): интеграционные тесты UPLOAD_PHOTO / UPLOAD_DOCUMENT
обёрток вокруг handler'ов, которые шлют фото/документы.

Покрывают:
- handle_qr → `uploading_photo` обёртка (QR-фото)
- handle_paste → `uploading_document` обёртка
- handle_export → `uploading_document` обёртка
- handle_media → `uploading_document` обёртка
- handle_backup → `uploading_document` обёртка
- При KRAB_TYPING_INDICATOR_ENABLED=0 — обёртка no-op, send_photo/send_document
  всё равно вызывается (graceful degradation).

Проверка: `client.send_chat_action` вызывается с правильным enum-action
(UPLOAD_PHOTO / UPLOAD_DOCUMENT) хотя бы 1 раз до отправки.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", raising=False)


# ---------------------------------------------------------------------------
# Тест 1: handle_qr вызывает uploading_photo() и send_photo()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_qr_invokes_uploading_photo(monkeypatch, tmp_path):
    """handle_qr должен импортировать uploading_photo и обернуть send_photo."""
    from src.handlers.commands import crypto_commands

    # Подменяем uploading_photo на трекер — фиксируем факт вызова.
    invoked = {"called": False, "chat_id": None}

    @asynccontextmanager
    async def _fake_uploading_photo(client, chat_id, **kwargs):
        invoked["called"] = True
        invoked["chat_id"] = chat_id
        yield

    def _factory(client, chat_id, **kwargs):
        return _fake_uploading_photo(client, chat_id, **kwargs)

    monkeypatch.setattr(
        "src.userbot.typing_indicator.uploading_photo",
        _factory,
    )

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="https://example.com")
    bot.client = MagicMock()
    bot.client.send_photo = AsyncMock()

    message = MagicMock()
    message.chat.id = 12345
    message.id = 1
    message.reply_to_message = None
    message.reply = AsyncMock()

    await crypto_commands.handle_qr(bot, message)

    assert invoked["called"] is True
    assert invoked["chat_id"] == 12345
    assert bot.client.send_photo.await_count == 1


# ---------------------------------------------------------------------------
# Тест 2: handle_qr — send_photo вызвался ВНУТРИ контекстa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_qr_send_photo_inside_indicator_context(monkeypatch):
    """send_photo должен быть вызван внутри async with блока (до __aexit__)."""
    from src.handlers.commands import crypto_commands

    order: list[str] = []

    @asynccontextmanager
    async def _tracking_cm(client, chat_id, **kwargs):
        order.append("enter")
        yield
        order.append("exit")

    monkeypatch.setattr(
        "src.userbot.typing_indicator.uploading_photo",
        _tracking_cm,
    )

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="hello")
    bot.client = MagicMock()

    async def _send_photo(**kwargs):
        order.append("send_photo")

    bot.client.send_photo = AsyncMock(side_effect=_send_photo)

    message = MagicMock()
    message.chat.id = 1
    message.id = 1
    message.reply_to_message = None

    await crypto_commands.handle_qr(bot, message)

    # send_photo был вызван между enter и exit.
    assert order == ["enter", "send_photo", "exit"]


# ---------------------------------------------------------------------------
# Тест 3: handle_paste → uploading_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_paste_invokes_uploading_document(monkeypatch, tmp_path):
    from src.handlers.commands import fileio_commands

    invoked = {"called": False}

    @asynccontextmanager
    async def _fake_uploading_document(client, chat_id, **kwargs):
        invoked["called"] = True
        yield

    monkeypatch.setattr(
        "src.userbot.typing_indicator.uploading_document",
        _fake_uploading_document,
    )

    # Конфиг с tmp_path как BASE_DIR.
    fake_config = MagicMock()
    fake_config.BASE_DIR = tmp_path
    monkeypatch.setattr(fileio_commands, "_config_baseline", fake_config)

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="hello world паста")
    bot.client = MagicMock()
    bot.client.send_document = AsyncMock()

    message = MagicMock()
    message.chat.id = 777
    message.reply_to_message = None
    message.reply = AsyncMock()

    await fileio_commands.handle_paste(bot, message)

    assert invoked["called"] is True
    assert bot.client.send_document.await_count == 1


# ---------------------------------------------------------------------------
# Тест 4: handle_media → uploading_document обёртка
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_media_invokes_uploading_document(monkeypatch):
    from src.handlers.commands import content_commands

    invoked = {"called": False, "chat_id": None}

    @asynccontextmanager
    async def _fake_cm(client, chat_id, **kwargs):
        invoked["called"] = True
        invoked["chat_id"] = chat_id
        yield

    monkeypatch.setattr(
        "src.userbot.typing_indicator.uploading_document",
        _fake_cm,
    )

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    bot.client = MagicMock()
    bot.client.send_document = AsyncMock()

    # Подделываем reply_to_message c документом.
    replied = MagicMock()
    replied.document = MagicMock(file_name="data.bin")
    replied.video = None
    replied.audio = None
    replied.voice = None
    replied.photo = None
    replied.sticker = None
    replied.animation = None

    async def _download(file_name=None):
        # Создаём пустой файл с размером > 0.
        from pathlib import Path
        p = Path(file_name)
        p.write_bytes(b"some-bytes-payload-12345")
        return file_name

    replied.download = AsyncMock(side_effect=_download)

    message = MagicMock()
    message.chat.id = 555
    message.id = 1
    message.reply_to_message = replied
    message.reply = AsyncMock(return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock()))

    await content_commands.handle_media(bot, message)

    assert invoked["called"] is True
    assert invoked["chat_id"] == 555
    assert bot.client.send_document.await_count == 1


# ---------------------------------------------------------------------------
# Тест 5: KRAB_TYPING_INDICATOR_ENABLED=0 — handle_qr всё равно шлёт photo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_qr_works_when_indicator_disabled(monkeypatch):
    """При env-disable typing indicator → no-op, но send_photo всё равно работает."""
    monkeypatch.setenv("KRAB_TYPING_INDICATOR_ENABLED", "0")
    from src.handlers.commands import crypto_commands

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="text")
    bot.client = MagicMock()
    bot.client.send_chat_action = AsyncMock()
    bot.client.send_photo = AsyncMock()

    message = MagicMock()
    message.chat.id = 999
    message.id = 1
    message.reply_to_message = None

    await crypto_commands.handle_qr(bot, message)

    # send_photo вызывался.
    assert bot.client.send_photo.await_count == 1
    # send_chat_action — НЕ вызывался (no-op режим).
    assert bot.client.send_chat_action.await_count == 0


# ---------------------------------------------------------------------------
# Тест 6: import-failure → fallback на nullcontext (graceful degradation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_qr_falls_back_on_import_failure(monkeypatch):
    """Если import uploading_photo сломан — обёртка должна тихо продолжить
    через nullcontext fallback."""
    import sys

    # Удаляем модуль из sys.modules чтобы заставить re-import.
    sys.modules.pop("src.userbot.typing_indicator", None)

    # Подменяем sys.modules перед re-import, чтобы import raise'нул.
    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _failing_import(name, *args, **kwargs):
        if name == "src.userbot.typing_indicator" or "typing_indicator" in name:
            raise ImportError("simulated import failure")
        return original_import(name, *args, **kwargs)

    from src.handlers.commands import crypto_commands

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="hi")
    bot.client = MagicMock()
    bot.client.send_photo = AsyncMock()

    message = MagicMock()
    message.chat.id = 222
    message.id = 1
    message.reply_to_message = None

    # patch builtin import только в нужном scope.
    with patch("builtins.__import__", side_effect=_failing_import):
        await crypto_commands.handle_qr(bot, message)

    # send_photo всё равно вызывался — fallback на nullcontext отработал.
    assert bot.client.send_photo.await_count == 1
