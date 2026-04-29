# -*- coding: utf-8 -*-
"""
VoiceMessageDispatcher — выбор формата ответа для голосовых сообщений.

Idea 1 (Session 28). Обёртка вокруг audio_summarizer (Idea 35): для коротких
voice messages показываем full transcript, для длинных — bullet-сводку, для
средних — оба (preview + bullets). Pure-модуль, без I/O: caller сам берёт
transcript и (по решению dispatcher'а) вызывает AudioSummarizer.

Решение принимается по двум осям — duration_sec (если известно) и
длине transcript. Если хотя бы одна ось пересекает порог «длинного»,
формат повышается; «короткий» требует обоих признаков.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.core.audio_summarizer import AudioSummary

FormatKind = Literal["full", "summary", "both", "auto"]

# --- Пороги по умолчанию ---------------------------------------------------

# < SHORT — full transcript
SHORT_DURATION_SEC = 30
SHORT_TRANSCRIPT_CHARS = 300

# > LONG — только summary
LONG_DURATION_SEC = 120  # 2 минуты
LONG_TRANSCRIPT_CHARS = 1500

# Лимит на full-фрагмент в составном ответе ("both"): чтобы не дублировать
# простыню рядом с буллетами.
DEFAULT_MAX_FULL_CHARS = 600


@dataclass(frozen=True)
class DispatcherDecision:
    """Решение о формате ответа на голосовое сообщение."""

    kind: Literal["full", "summary", "both"]
    reason: str  # человекочитаемая причина для логов


class VoiceMessageDispatcher:
    """Решает, как отдать voice transcript: full / summary / both."""

    def __init__(
        self,
        *,
        short_duration_sec: int = SHORT_DURATION_SEC,
        short_transcript_chars: int = SHORT_TRANSCRIPT_CHARS,
        long_duration_sec: int = LONG_DURATION_SEC,
        long_transcript_chars: int = LONG_TRANSCRIPT_CHARS,
    ) -> None:
        self._short_duration = short_duration_sec
        self._short_chars = short_transcript_chars
        self._long_duration = long_duration_sec
        self._long_chars = long_transcript_chars

    def decide_format(
        self,
        transcript: str,
        *,
        duration_sec: float | None = None,
    ) -> DispatcherDecision:
        """Вернуть решение о формате ответа.

        Логика:
          - long по любой оси → 'summary'
          - short по обеим осям (или одной при отсутствии duration) → 'full'
          - иначе → 'both'
        """
        text = (transcript or "").strip()
        chars = len(text)
        # Пустой transcript — нечего форматировать; считаем "full" (caller
        # сам решит, отправлять ли).
        if not chars:
            return DispatcherDecision(kind="full", reason="empty_transcript")

        is_long_by_chars = chars > self._long_chars
        is_long_by_dur = duration_sec is not None and duration_sec > self._long_duration
        if is_long_by_chars or is_long_by_dur:
            return DispatcherDecision(
                kind="summary",
                reason="long_by_duration" if is_long_by_dur else "long_by_chars",
            )

        is_short_by_chars = chars < self._short_chars
        if duration_sec is None:
            is_short = is_short_by_chars
        else:
            is_short_by_dur = duration_sec < self._short_duration
            is_short = is_short_by_chars and is_short_by_dur
        if is_short:
            return DispatcherDecision(kind="full", reason="short")

        return DispatcherDecision(kind="both", reason="medium")

    def format_response(
        self,
        transcript: str,
        *,
        summary: AudioSummary | None = None,
        format_kind: FormatKind = "auto",
        duration_sec: float | None = None,
        max_full_chars: int = DEFAULT_MAX_FULL_CHARS,
    ) -> str:
        """Собрать текстовый ответ согласно kind.

        Если kind='auto' — берём решение из decide_format(). Если требуется
        summary, но он не передан — fail-open до full transcript.
        """
        text = (transcript or "").strip()
        if format_kind == "auto":
            kind = self.decide_format(text, duration_sec=duration_sec).kind
        else:
            kind = format_kind  # type: ignore[assignment]

        if kind == "summary":
            if summary is None:
                # fail-open: вернуть full
                return self._render_full(text, max_chars=None)
            return self._render_summary(summary)

        if kind == "both":
            preview = self._render_full(text, max_chars=max_full_chars)
            if summary is None:
                return preview
            return f"{preview}\n\n{self._render_summary(summary)}"

        # kind == 'full'
        return self._render_full(text, max_chars=None)

    # ----- renderers -------------------------------------------------------

    @staticmethod
    def _render_full(text: str, *, max_chars: int | None) -> str:
        text = text.strip()
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return f"🎙 {text}" if text else "🎙 (пусто)"

    @staticmethod
    def _render_summary(summary: AudioSummary) -> str:
        lines: list[str] = []
        if summary.topic and summary.topic != "—":
            lines.append(f"🎙 *{summary.topic}*")
        else:
            lines.append("🎙 Сводка")
        for bullet in summary.bullets:
            lines.append(f"• {bullet}")
        return "\n".join(lines)


# --- Singleton ------------------------------------------------------------

_default_dispatcher: VoiceMessageDispatcher | None = None


def get_dispatcher() -> VoiceMessageDispatcher:
    global _default_dispatcher
    if _default_dispatcher is None:
        _default_dispatcher = VoiceMessageDispatcher()
    return _default_dispatcher


def reset_dispatcher() -> None:
    """Для тестов: сбросить singleton."""
    global _default_dispatcher
    _default_dispatcher = None
