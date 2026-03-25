# -*- coding: utf-8 -*-
"""Проверки voice_engine: сигнатура TTS и проброс выбранного голоса."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src import voice_engine


class _FakeProcess:
    """Минимальный ffmpeg-процесс для unit smoke без реального запуска ffmpeg."""

    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_text_to_speech_accepts_custom_voice_and_returns_ogg_path(tmp_path: Path) -> None:
    """TTS не должен падать, когда caller передаёт `voice=...`."""
    temp_mp3_written: dict[str, str] = {}

    class _FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str) -> None:
            temp_mp3_written["text"] = text
            temp_mp3_written["voice"] = voice
            temp_mp3_written["rate"] = rate

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"mp3")

    async def _fake_exec(*args, **kwargs):  # noqa: ANN002, ANN003
        output_ogg = Path(args[-1])
        output_ogg.write_bytes(b"ogg")
        return _FakeProcess()

    with patch.object(voice_engine, "VOICE_OUTPUT_DIR", str(tmp_path)):
        with patch("src.voice_engine.edge_tts.Communicate", _FakeCommunicate):
            with patch("src.voice_engine.asyncio.create_subprocess_exec", new=_fake_exec):
                result = await voice_engine.text_to_speech(
                    "Привет",
                    filename="reply.ogg",
                    speed=1.25,
                    voice="ru-RU-SvetlanaNeural",
                )

    assert result == str(tmp_path / "reply.ogg")
    assert temp_mp3_written["voice"] == "ru-RU-SvetlanaNeural"
    assert temp_mp3_written["rate"] == "+25%"
    assert (tmp_path / "reply.ogg").exists()
