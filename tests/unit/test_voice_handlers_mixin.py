# -*- coding: utf-8 -*-
"""Wave 31-J tests: VoiceHandlersMixin extraction."""

from __future__ import annotations

import inspect

import pytest


def test_voice_handlers_mixin_importable():
    from src.userbot.voice_handlers import VoiceHandlersMixin

    assert VoiceHandlersMixin.__name__ == "VoiceHandlersMixin"


def test_kraab_userbot_inherits_voice_handlers_mixin():
    from src.userbot.voice_handlers import VoiceHandlersMixin
    from src.userbot_bridge import KraabUserbot

    assert VoiceHandlersMixin in KraabUserbot.__mro__


@pytest.mark.parametrize(
    "method_name",
    ["_apply_voice_dispatcher", "_handle_translator_voice"],
)
def test_voice_methods_resolve_via_mixin(method_name):
    from src.userbot.voice_handlers import VoiceHandlersMixin
    from src.userbot_bridge import KraabUserbot

    assert method_name in VoiceHandlersMixin.__dict__
    assert method_name not in KraabUserbot.__dict__
    assert inspect.iscoroutinefunction(getattr(KraabUserbot, method_name))


@pytest.mark.asyncio
async def test_apply_voice_dispatcher_disabled_returns_transcript_unchanged(monkeypatch):
    """KRAB_VOICE_DISPATCHER_ENABLED=0 → bypass, transcript неизменный."""
    from src.userbot.voice_handlers import VoiceHandlersMixin

    monkeypatch.setenv("KRAB_VOICE_DISPATCHER_ENABLED", "0")
    bot = VoiceHandlersMixin.__new__(VoiceHandlersMixin)

    result = await bot._apply_voice_dispatcher(message=None, transcript="hello world")
    assert result == "hello world"


@pytest.mark.asyncio
async def test_apply_voice_dispatcher_empty_transcript_passthrough(monkeypatch):
    """Empty transcript → fast-return без обращения к dispatcher."""
    from src.userbot.voice_handlers import VoiceHandlersMixin

    monkeypatch.setenv("KRAB_VOICE_DISPATCHER_ENABLED", "1")
    bot = VoiceHandlersMixin.__new__(VoiceHandlersMixin)

    result = await bot._apply_voice_dispatcher(message=None, transcript="   ")
    assert result == "   "


def test_full_mixin_set_after_wave_31_j():
    """12 mixins (Waves 31-A..J) подключены."""
    from src.userbot_bridge import KraabUserbot

    mro_names = [c.__name__ for c in KraabUserbot.__mro__ if c.__name__.endswith("Mixin")]
    assert "VoiceHandlersMixin" in mro_names
    # Total = 19 (включая базовые до 31-A) + 1 = 20 mixins
    assert len(mro_names) >= 20
