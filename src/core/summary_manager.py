# -*- coding: utf-8 -*-
"""
Summary Manager v1.0
Автоматическое сжатие контекста длинных диалогов.
"""
import structlog
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .model_manager import ModelRouter
    from .context_manager import ContextKeeper

logger = structlog.get_logger(__name__)

class SummaryManager:
    def __init__(self, router: 'ModelRouter', memory: 'ContextKeeper', min_messages: int = 40):
        self.router = router
        self.memory = memory
        self.min_messages = min_messages # Порог срабатывания

    async def auto_summarize(self, chat_id: int):
        """Проверяет длину истории и сжимает её при необходимости."""
        history = self.memory.get_recent_context(chat_id, limit=None)
        
        if len(history) < self.min_messages:
            return False

        logger.info(f"🔄 Summarizing chat {chat_id} (History length: {len(history)})")
        
        # Формируем текст для суммаризации
        history_text = "\n".join([
            f"{msg.get('role', msg.get('user', 'unknown'))}: {msg.get('text', '')}"
            for msg in history
        ])

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
                return True
        except Exception as e:
            logger.error(f"❌ Failed to summarize chat {chat_id}: {e}")
        
        return False
