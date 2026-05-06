# -*- coding: utf-8 -*-
"""
Wire-up tests: video / video_note / animation → process_video_message.

Проверяем, что bridge вызывает perceptor.process_video_message для всех
3 видео-типов и НЕ вызывает для sticker (skip-policy).

Дополнительно — _describe_video_frame корректно собирает stream openclaw_client
в одну строку и graceful fallback'ит на пустую при timeout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _stub_bot():
    """Stub KraabUserbot для прямых вызовов _process_video_message / _describe_video_frame."""
    from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

    bot = KraabUserbot.__new__(KraabUserbot)
    bot.client = MagicMock()
    bot.client.download_media = AsyncMock(return_value="/tmp/krab_videos/dummy.bin")
    bot._safe_edit = AsyncMock()
    return bot


def _make_video_message(*, kind: str = "video", caption: str = "") -> MagicMock:
    msg = MagicMock()
    msg.id = 555
    msg.caption = caption
    msg.video = None
    msg.video_note = None
    msg.animation = None
    msg.sticker = None
    media = SimpleNamespace(file_size=1024)
    setattr(msg, kind, media)
    return msg


@pytest.mark.parametrize("kind", ["video", "video_note", "animation"])
def test_process_video_message_invokes_perceptor_for_all_video_kinds(tmp_path, kind):
    """video / video_note / animation → process_video_message вызывается."""
    bot = _stub_bot()
    msg = _make_video_message(kind=kind, caption="привет")

    captured: dict = {}

    async def fake_perceptor(path, *, caption, max_frames, sample_strategy, frame_describer):
        captured["path"] = path
        captured["caption"] = caption
        captured["max_frames"] = max_frames
        return "Подпись к видео: привет\nСодержимое видео по кадрам:\n  1. кошка"

    with (
        patch("src.modules.perceptor.process_video_message", side_effect=fake_perceptor),
        # Wave 31-H: media-методы переехали в src.userbot.media_processors
        patch(
            "src.userbot.media_processors.config",
            SimpleNamespace(
                VIDEO_DOWNLOAD_DIR=str(tmp_path),
                VIDEO_DOWNLOAD_TIMEOUT_SEC=5.0,
                VIDEO_DOWNLOAD_MAX_BYTES=10 * 1024 * 1024,
                VIDEO_MAX_FRAMES=3,
            ),
        ),
    ):
        result = asyncio.run(
            bot._process_video_message(
                message=msg,
                query="что тут?",
                temp_msg=None,
                is_self=True,
                chat_id="123",
            )
        )

    assert "Подпись к видео: привет" in result
    assert "что тут?" in result
    assert captured["caption"] == "привет"
    assert captured["max_frames"] == 3


def test_process_video_message_skips_when_no_media(tmp_path):
    """Без video/video_note/animation — query возвращается без изменений."""
    bot = _stub_bot()
    msg = MagicMock()
    msg.video = None
    msg.video_note = None
    msg.animation = None

    with patch(
        "src.modules.perceptor.process_video_message",
        side_effect=AssertionError("perceptor не должен вызываться"),
    ):
        result = asyncio.run(
            bot._process_video_message(
                message=msg,
                query="оригинал",
                temp_msg=None,
                is_self=True,
                chat_id="123",
            )
        )

    assert result == "оригинал"


def test_process_video_message_too_large_skips(tmp_path):
    """file_size > VIDEO_DOWNLOAD_MAX_BYTES — perceptor не вызывается."""
    bot = _stub_bot()
    msg = MagicMock()
    msg.id = 1
    msg.caption = ""
    msg.video = SimpleNamespace(file_size=999_999_999)
    msg.video_note = None
    msg.animation = None

    with (
        patch(
            "src.modules.perceptor.process_video_message",
            side_effect=AssertionError("perceptor не должен вызываться"),
        ),
        patch(
            "src.userbot.media_processors.config",
            SimpleNamespace(
                VIDEO_DOWNLOAD_DIR=str(tmp_path),
                VIDEO_DOWNLOAD_MAX_BYTES=1024,
            ),
        ),
    ):
        result = asyncio.run(
            bot._process_video_message(
                message=msg,
                query="orig",
                temp_msg=None,
                is_self=True,
                chat_id="55",
            )
        )

    assert result == "orig"


def test_describe_video_frame_aggregates_stream_chunks():
    """_describe_video_frame собирает stream chunks в одну строку."""
    bot = _stub_bot()

    async def fake_stream(*args, **kwargs):
        for ch in ["кош", "ка ", "сидит"]:
            yield ch

    with (
        patch("src.userbot.media_processors.openclaw_client") as mock_client,
        patch(
            "src.userbot.media_processors.config",
            SimpleNamespace(VIDEO_FRAME_DESCRIBE_TIMEOUT_SEC=10.0),
        ),
    ):
        mock_client.send_message_stream = fake_stream
        result = asyncio.run(bot._describe_video_frame(b"\x89PNG", 0, chat_id="42"))

    assert result == "кошка сидит"


def test_describe_video_frame_returns_empty_on_error():
    """При исключении из stream — возвращаем пустую строку (perceptor пропустит кадр)."""
    bot = _stub_bot()

    async def boom(*args, **kwargs):
        raise RuntimeError("vision down")
        yield  # pragma: no cover — make это async generator

    with (
        patch("src.userbot.media_processors.openclaw_client") as mock_client,
        patch(
            "src.userbot.media_processors.config",
            SimpleNamespace(VIDEO_FRAME_DESCRIBE_TIMEOUT_SEC=10.0),
        ),
    ):
        mock_client.send_message_stream = boom
        result = asyncio.run(bot._describe_video_frame(b"x", 1, chat_id="42"))

    assert result == ""


def test_describe_video_frame_empty_bytes_short_circuits():
    """Пустой frame_bytes → пустая строка без обращения к openclaw."""
    bot = _stub_bot()
    with patch("src.userbot.media_processors.openclaw_client") as mock_client:
        mock_client.send_message_stream = MagicMock(
            side_effect=AssertionError("не должно вызываться")
        )
        result = asyncio.run(bot._describe_video_frame(b"", 0, chat_id="42"))
    assert result == ""
