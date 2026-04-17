# -*- coding: utf-8 -*-
"""
VoiceSession — per-session state for the voice channel.

Хранит буфер транскрипций и языковой контекст на время голосовой сессии.
Изолирован от Telegram-чатов: chat_id здесь — это session_id от Voice Gateway.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class TranscriptEntry:
    """Одна запись в буфере транскрипций сессии."""

    text: str
    language: str
    ts: float = field(default_factory=time.time)


class VoiceSession:
    """
    Мини-стейт для одной голосовой сессии (chat_id = session_xyz).

    Намеренно лёгкий: храним последние N транскрипций и языковой контекст.
    Не используем StateStore/NDJSON — голосовые сессии эфемерны.
    """

    MAX_BUFFER = 20

    def __init__(self, session_id: str, language: str = "ru") -> None:
        self.session_id = session_id
        self.language = language
        self.created_at = time.time()
        self.last_active = time.time()
        self._buffer: List[TranscriptEntry] = []

    # ------------------------------------------------------------------
    # Buffer management
    # ------------------------------------------------------------------

    def push_transcript(self, text: str, language: str | None = None) -> None:
        """Добавить транскрипцию в буфер (с rolling window)."""
        lang = language or self.language
        self._buffer.append(TranscriptEntry(text=text, language=lang))
        if len(self._buffer) > self.MAX_BUFFER:
            self._buffer.pop(0)
        self.last_active = time.time()
        if language:
            self.language = language  # обновляем текущий язык сессии

    def recent_transcripts(self, n: int = 5) -> List[TranscriptEntry]:
        """Последние N транскрипций."""
        return self._buffer[-n:]

    def recent_text(self, n: int = 5) -> str:
        """Последние N транскрипций одной строкой для контекста."""
        return "\n".join(e.text for e in self.recent_transcripts(n))

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"<VoiceSession id={self.session_id!r} lang={self.language!r}"
            f" buf={len(self._buffer)}>"
        )
