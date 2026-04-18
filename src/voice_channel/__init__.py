# -*- coding: utf-8 -*-
"""
voice_channel — Voice Assistant Mode (VA Phase 1.4)

Принимает транскрибированный текст от Krab Ear / Voice Gateway,
пропускает через OpenClaw brain, стримит ответ обратно.
"""

from .voice_channel_handler import VoiceChannelHandler
from .voice_state import VoiceSession

__all__ = ["VoiceChannelHandler", "VoiceSession"]
