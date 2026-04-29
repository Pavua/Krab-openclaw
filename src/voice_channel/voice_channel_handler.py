# -*- coding: utf-8 -*-
"""
VoiceChannelHandler — core bridge for Voice Assistant Mode (VA Phase 1.4).

Dataflow:
  Voice Gateway (Krab Ear STT output)
      -> POST /v1/voice/message
          -> VoiceChannelHandler.handle_voice_message()
              -> OpenClawClient.send_message_stream()
                  -> SSE token stream back to caller

Design constraints:
- Reuses OpenClawClient.send_message_stream (streaming logic not duplicated).
- Reuses MemoryManager.recall for context.
- Thin wrapper: ~100 LOC, no business logic outside the prompt.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, AsyncIterator, Dict, Optional

from structlog import get_logger

from ..config import config
from .voice_state import VoiceSession

if TYPE_CHECKING:
    from ..mcp_client import MCPClientManager
    from ..memory_engine import MemoryManager
    from ..model_manager import ModelManager
    from ..openclaw_client import OpenClawClient

logger = get_logger(__name__)

# Голосовой системный промпт — хранится здесь, а не в config, т.к. это VA-domain.
_VOICE_SYSTEM_PROMPT_TEMPLATE = (
    "You are Krab, voice assistant. "
    "Respond conversationally в {language}. "
    "Keep responses brief (2 sentences max). "
    "Use tools if needed."
)

# Preferred model: hint to OpenClaw; falls through to current route if not loaded.
_PREFERRED_MODEL = "qwen3-30b-a3b-instruct-2507"


class VoiceChannelHandler:
    """
    Главный обработчик голосового канала.

    Принимает транскрибированный текст (уже в виде строки — STT выполнен
    в Krab Ear), прокидывает через OpenClaw brain, отдаёт токены стримом.
    """

    def __init__(
        self,
        openclaw: "OpenClawClient",
        memory: "MemoryManager",
        mcp: Optional["MCPClientManager"] = None,
        model_manager: Optional["ModelManager"] = None,
    ) -> None:
        self._openclaw = openclaw
        self._memory = memory
        self._mcp = mcp
        self._model_manager = model_manager
        self._sessions: Dict[str, VoiceSession] = {}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def get_or_create_session(self, chat_id: str, language: str = "ru") -> VoiceSession:
        """Возвращает существующую сессию или создаёт новую."""
        if chat_id not in self._sessions:
            self._sessions[chat_id] = VoiceSession(session_id=chat_id, language=language)
            logger.info("voice_session_created", session_id=chat_id, language=language)
        return self._sessions[chat_id]

    def session_count(self) -> int:
        """Количество активных сессий (для статуса)."""
        return len(self._sessions)

    def get_session(self, chat_id: str) -> Optional[VoiceSession]:
        """Возвращает сессию или None."""
        return self._sessions.get(chat_id)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def handle_voice_message(
        self,
        chat_id: str,
        message_text: str,
        language: str = "ru",
    ) -> AsyncIterator[str]:
        """
        Stream response tokens for a voice message.

        Args:
            chat_id:      Идентификатор сессии (например, "session_xyz").
            message_text: Транскрибированный текст от Krab Ear.
            language:     Язык ответа (ru/es/en).

        Yields:
            Строковые токены ответа LLM.
        """
        session = self.get_or_create_session(chat_id, language)
        session.push_transcript(message_text, language)

        # LRU eviction (VA Phase 1.6): если model_manager доступен,
        # записываем использование qwen3-30b и выгружаем idle модели.
        preferred_model = getattr(config, "KRAB_MODEL_QWEN3_30B", "qwen3-30b-a3b-instruct-2507")
        if self._model_manager:
            self._model_manager.record_usage(preferred_model)
            try:
                evicted = await self._model_manager.maybe_evict_idle(
                    keep_model=preferred_model, max_total_models=1
                )
                if evicted:
                    logger.info("voice_lru_evicted_models", evicted=evicted)
            except Exception as exc:  # noqa: BLE001
                logger.warning("voice_lru_eviction_failed", error=str(exc))

        # Опциональный контекст памяти (best-effort, не ронять поток при сбое).
        memory_context = ""
        try:
            memory_context = self._memory.recall(message_text, n_results=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_memory_recall_failed", error=str(exc))

        system_prompt = _VOICE_SYSTEM_PROMPT_TEMPLATE.format(language=language)
        if memory_context:
            system_prompt += f"\n\nRelevant context:\n{memory_context}"

        logger.info(
            "voice_message_received",
            session_id=chat_id,
            language=language,
            text_len=len(message_text),
        )

        started = time.monotonic()
        token_count = 0
        try:
            async for token in self._openclaw.send_message_stream(
                message=message_text,
                chat_id=chat_id,
                system_prompt=system_prompt,
                preferred_model=_PREFERRED_MODEL,
            ):
                token_count += 1
                yield token
        except Exception as exc:  # noqa: BLE001
            logger.error("voice_stream_error", session_id=chat_id, error=str(exc))
            yield f"[Ошибка: {exc}]"
            return

        logger.info(
            "voice_message_completed",
            session_id=chat_id,
            tokens=token_count,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
