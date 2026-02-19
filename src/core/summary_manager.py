# -*- coding: utf-8 -*-
"""
Summary Manager v1.0
Автоматическое сжатие контекста длинных диалогов.
"""
import structlog
import os
import time
import asyncio
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .model_manager import ModelRouter
    from .context_manager import ContextKeeper

logger = structlog.get_logger(__name__)

class SummaryManager:
    def __init__(
        self,
        router: 'ModelRouter',
        memory: 'ContextKeeper',
        max_tokens: int = 3000,
        min_messages: int | None = None,
    ):
        self.router = router
        self.memory = memory
        self.max_tokens = int(max(200, max_tokens))  # Порог срабатывания в токенах.
        # Legacy-режим обратной совместимости:
        # старые вызовы SummaryManager(..., min_messages=40) должны продолжать работать.
        self.min_messages = int(max(1, min_messages)) if min_messages is not None else None

        env_enabled = str(os.getenv("AUTO_SUMMARY_ENABLED", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        # В legacy-режиме включаем auto summary без дополнительного env-флага.
        self.enabled = True if self.min_messages is not None else env_enabled
        self.min_interval_seconds = int(os.getenv("AUTO_SUMMARY_MIN_INTERVAL_SECONDS", "900"))
        self.max_history_chars = int(os.getenv("AUTO_SUMMARY_MAX_HISTORY_CHARS", "20000"))
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._last_run_ts: dict[int, float] = {}

    async def auto_summarize(self, chat_id: int):
        """Проверяет токен-лимит истории и сжимает её при необходимости."""
        if not self.enabled:
            return False

        chat_lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        if chat_lock.locked():
            return False

        async with chat_lock:
            return await self._auto_summarize_locked(chat_id)

    async def _auto_summarize_locked(self, chat_id: int):
        """Внутренний запуск summarization с защитой от частых повторов."""
        now = time.time()
        last_run = self._last_run_ts.get(chat_id, 0.0)
        if now - last_run < self.min_interval_seconds:
            return False

        history = self.memory.get_recent_context(chat_id, limit=None)
        history_size = len(history or [])

        if self.min_messages is not None and history_size < self.min_messages:
            return False

        history_text = "\n".join([
            f"{msg.get('role', msg.get('user', 'unknown'))}: {msg.get('text', '')}"
            for msg in history
        ])
        if self.max_history_chars > 0 and len(history_text) > self.max_history_chars:
            # Ограничиваем размер тела, чтобы не отправлять мегапромпты в LLM.
            history_text = history_text[-self.max_history_chars:]

        # В token-режиме используем порог токенов;
        # в legacy min_messages-режиме summary уже разрешён количеством сообщений.
        current_tokens = self._estimate_tokens(history_text)
        if self.min_messages is None and current_tokens < self.max_tokens:
            return False

        if self.min_messages is not None:
            logger.info(
                "🔄 Summarizing chat (legacy min_messages mode)",
                chat_id=chat_id,
                messages=history_size,
                min_messages=self.min_messages,
            )
        else:
            logger.info(
                "🔄 Summarizing chat (token mode)",
                chat_id=chat_id,
                tokens=current_tokens,
                max_tokens=self.max_tokens,
            )

        summary_prompt = (
            "### ИНСТРУКЦИЯ: Сократи этот диалог до краткого, но информативного саммари.\n"
            "Выдели основные факты, принятые решения и текущий контекст.\n"
            "Саммари должно быть на русском языке.\n\n"
            f"### ДИАЛОГ:\n{history_text}"
        )

        try:
            summary = await self.router.route_query(
                prompt=summary_prompt,
                task_type="chat",
                use_rag=False # Чтобы не было рекурсии
            )
            
            if summary and not summary.startswith("Error:"):
                # Сохраняем новое саммари
                old_summary = self.memory.get_summary(chat_id)
                new_summary = f"{old_summary}\n\n[LATEST SUMMARY]:\n{summary}"
                self.memory.save_summary(chat_id, new_summary)
                
                # Очищаем историю (оставляем только последние 5 сообщений для плавности)
                last_messages = history[-5:]
                self.memory.clear_history(chat_id)
                for m in last_messages:
                    self.memory.save_message(chat_id, m)
                
                logger.info(f"✅ Chat {chat_id} summarized successfully.")
                self._last_run_ts[chat_id] = time.time()
                return True
        except Exception as e:
            logger.error(f"❌ Failed to summarize chat {chat_id}: {e}")
        
        return False

    def _estimate_tokens(self, text: str) -> int:
        """Безопасная оценка токенов с fallback, если memory не даёт estimator."""
        estimator = getattr(self.memory, "_estimate_tokens", None)
        if callable(estimator):
            try:
                value = int(estimator(text))
                if value >= 0:
                    return value
            except Exception:
                pass
        if not text:
            return 0
        return len(text) // 4 + 1
