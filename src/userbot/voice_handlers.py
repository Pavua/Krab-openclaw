# -*- coding: utf-8 -*-
"""Wave 31-J: VoiceHandlersMixin — voice/translator pipeline для voice notes.

Зачем:
- bridge до 31-J содержал ~4967 LOC, voice-блок (2 метода) — cohesive ~150 LOC
  обработки голосовых сообщений: dispatcher (summary/preview) + translator-overlay.
- Mixin использует: ``self._safe_reply_or_send_new`` (TelegramSendUtilsMixin),
  ``self.get_translator_runtime_profile`` (TranslatorProfileMixin),
  ``self.get_translator_session_state``, ``self.update_translator_session_state``.

Контракт:
- ``_apply_voice_dispatcher`` — Idea 1: подмешивает summary/preview к транскрипту.
  Решение через VoiceMessageDispatcher.decide_format (audio/duration-aware).
  Fail-open: при любой ошибке возвращает исходный transcript.
- ``_handle_translator_voice`` — переводит транскрипт voice note и шлёт результат
  в чат. Возвращает True если перевод выполнен (LLM-flow skip), False иначе.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from ..core.translator_session_state import append_translator_history_entry
from ..openclaw_client import openclaw_client

logger = structlog.get_logger("Krab.userbot.voice_handlers")


class VoiceHandlersMixin:
    """Mixin: voice transcript dispatcher + translator overlay для voice notes."""

    # Зависимости через MRO (на host KraabUserbot):
    # - _safe_reply_or_send_new → TelegramSendUtilsMixin
    # - get_translator_runtime_profile → TranslatorProfileMixin
    # - get_translator_session_state → TranslatorProfileMixin
    # - update_translator_session_state → TranslatorProfileMixin

    async def _apply_voice_dispatcher(self, message: Any, transcript: str) -> str:
        """Idea 1: подмешать summary/preview к голосовому transcript.

        Принимает решение через VoiceMessageDispatcher.decide_format().
        Если decision=summary|both — вызывает AudioSummarizer и собирает
        форматированный prompt-блок. Fail-open: при любой ошибке возвращает
        исходный transcript.
        """
        # Gate: env-флаг для отключения
        if os.getenv("KRAB_VOICE_DISPATCHER_ENABLED", "1").strip().lower() not in (
            "1",
            "true",
            "yes",
        ):
            return transcript
        text = (transcript or "").strip()
        if not text:
            return transcript
        try:
            from ..core.audio_summarizer import get_summarizer  # noqa: PLC0415
            from ..core.voice_message_dispatcher import get_dispatcher  # noqa: PLC0415

            duration: float | None = None
            voice_obj = getattr(message, "voice", None)
            audio_obj = getattr(message, "audio", None)
            for src_obj in (voice_obj, audio_obj):
                if src_obj is not None:
                    dur_attr = getattr(src_obj, "duration", None)
                    if dur_attr is not None:
                        duration = float(dur_attr)
                        break

            dispatcher = get_dispatcher()
            decision = dispatcher.decide_format(text, duration_sec=duration)
            summary = None
            if decision.kind in ("summary", "both"):
                try:
                    summary = await get_summarizer().summarize(text)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "voice_dispatcher_summary_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    summary = None
            formatted = dispatcher.format_response(
                text,
                summary=summary,
                format_kind=decision.kind,
                duration_sec=duration,
            )
            logger.info(
                "voice_dispatcher_applied",
                kind=decision.kind,
                reason=decision.reason,
                duration_sec=duration,
                transcript_chars=len(text),
                has_summary=bool(summary),
            )
            return formatted or transcript
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "voice_dispatcher_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return transcript

    async def _handle_translator_voice(
        self,
        message: Any,
        transcript: str,
        chat_id: int | str,
    ) -> bool:
        """
        Переводит транскрипт voice note и отправляет результат.

        Возвращает True если перевод выполнен, False если нужно идти в обычный LLM.
        """
        from ..core.language_detect import (  # noqa: PLC0415
            detect_language,
            resolve_translation_pair,
        )
        from ..core.translator_engine import translate_text  # noqa: PLC0415

        profile = self.get_translator_runtime_profile()
        detected = detect_language(transcript)
        if not detected:
            return False  # не удалось определить язык → обычный LLM

        language_pair = str(profile.get("language_pair") or "es-ru")
        src_lang, tgt_lang = resolve_translation_pair(detected, language_pair)
        if src_lang == tgt_lang:
            return False  # язык совпадает, переводить нечего

        try:
            result = await translate_text(
                transcript,
                src_lang,
                tgt_lang,
                openclaw_client=openclaw_client,
                chat_id=f"translator_{chat_id}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "translator_voice_failed",
                chat_id=str(chat_id),
                error=str(exc),
            )
            return False  # fallback к обычному LLM

        if not result.translated:
            return False

        # Формируем ответ
        reply_text = f"🔄 {src_lang}→{tgt_lang}\n**{result.original}**\n_{result.translated}_"
        await self._safe_reply_or_send_new(message, reply_text)

        # Обновляем session stats и добавляем запись в history
        try:
            state = self.get_translator_session_state()
            stats = state.get("stats") or {"total_translations": 0, "total_latency_ms": 0}
            # Добавляем запись в историю переводов
            updated_state = append_translator_history_entry(
                state,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                original=transcript[:300],
                translation=result.translated[:300],
                latency_ms=result.latency_ms,
            )
            self.update_translator_session_state(
                last_language_pair=f"{src_lang}-{tgt_lang}",
                last_translated_original=transcript[:200],
                last_translated_translation=result.translated[:200],
                last_event="translation_completed",
                history=updated_state["history"],
                stats={
                    "total_translations": stats.get("total_translations", 0) + 1,
                    "total_latency_ms": stats.get("total_latency_ms", 0) + result.latency_ms,
                },
            )
        except Exception:  # noqa: BLE001
            pass  # stats update не должен ломать pipeline

        logger.info(
            "translator_voice_completed",
            chat_id=str(chat_id),
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            latency_ms=result.latency_ms,
            model=result.model_id,
        )
        return True
