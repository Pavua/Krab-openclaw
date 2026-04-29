# -*- coding: utf-8 -*-
"""
Wire-up tests для Idea 1: VoiceMessageDispatcher в userbot_bridge.

Проверяем _apply_voice_dispatcher:
  1) короткий voice → kind=full → вернёт raw transcript-like (без bullets)
  2) длинный voice → kind=summary → вернёт собранный bullet-блок (summarizer mock)
  3) summarizer кидает исключение → fail-open: вернётся исходный transcript
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _stub_bot():
    from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

    return KraabUserbot.__new__(KraabUserbot)


def _make_voice_msg(duration: float):
    msg = MagicMock()
    msg.voice = SimpleNamespace(duration=duration)
    msg.audio = None
    return msg


def test_short_voice_returns_full_format(monkeypatch):
    """Короткое voice → kind='full', формат содержит исходный текст без summary-bullets."""
    monkeypatch.setenv("KRAB_VOICE_DISPATCHER_ENABLED", "1")
    from src.core import voice_message_dispatcher as vmd

    vmd.reset_dispatcher()

    bot = _stub_bot()
    msg = _make_voice_msg(duration=10.0)
    transcript = "Привет, это короткое сообщение."

    # summarizer не должен вызываться при kind=full
    fake_summarizer = MagicMock()
    fake_summarizer.summarize = AsyncMock(return_value=None)
    with patch("src.core.audio_summarizer.get_summarizer", return_value=fake_summarizer):
        result = asyncio.run(bot._apply_voice_dispatcher(msg, transcript))

    assert transcript in result
    fake_summarizer.summarize.assert_not_called()


def test_long_voice_invokes_summarizer(monkeypatch):
    """Длинный voice → kind='summary', вызывается summarizer, результат содержит bullets."""
    monkeypatch.setenv("KRAB_VOICE_DISPATCHER_ENABLED", "1")
    from src.core import voice_message_dispatcher as vmd
    from src.core.audio_summarizer import AudioSummary

    vmd.reset_dispatcher()

    bot = _stub_bot()
    msg = _make_voice_msg(duration=180.0)  # > LONG_DURATION_SEC=120
    transcript = "Это длинное сообщение. " * 100  # > 1500 chars

    fake_summary = AudioSummary(
        bullets=["пункт 1", "пункт 2"],
        topic="Тема",
        sentiment="neutral",
        length_chars=200,
    )
    fake_summarizer = MagicMock()
    fake_summarizer.summarize = AsyncMock(return_value=fake_summary)
    with patch("src.core.audio_summarizer.get_summarizer", return_value=fake_summarizer):
        result = asyncio.run(bot._apply_voice_dispatcher(msg, transcript))

    fake_summarizer.summarize.assert_called_once()
    assert "пункт 1" in result
    assert "пункт 2" in result
    assert "Тема" in result


def test_summarizer_failure_fails_open(monkeypatch):
    """Если summarizer кидает исключение — вернуть исходный transcript (fail-open)."""
    monkeypatch.setenv("KRAB_VOICE_DISPATCHER_ENABLED", "1")
    from src.core import voice_message_dispatcher as vmd

    vmd.reset_dispatcher()

    bot = _stub_bot()
    msg = _make_voice_msg(duration=180.0)
    transcript = "Длинное сообщение. " * 100

    fake_summarizer = MagicMock()
    fake_summarizer.summarize = AsyncMock(side_effect=RuntimeError("LM down"))
    with patch("src.core.audio_summarizer.get_summarizer", return_value=fake_summarizer):
        result = asyncio.run(bot._apply_voice_dispatcher(msg, transcript))

    # Fail-open: format_response с summary=None для kind='summary' возвращает full
    assert transcript[:200] in result or "Длинное сообщение" in result


def test_dispatcher_disabled_returns_transcript(monkeypatch):
    """KRAB_VOICE_DISPATCHER_ENABLED=0 → bypass, вернётся исходный transcript."""
    monkeypatch.setenv("KRAB_VOICE_DISPATCHER_ENABLED", "0")

    bot = _stub_bot()
    msg = _make_voice_msg(duration=180.0)
    transcript = "Длинный текст " * 100

    result = asyncio.run(bot._apply_voice_dispatcher(msg, transcript))
    assert result == transcript
