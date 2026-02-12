# -*- coding: utf-8 -*-
"""
Memory Archiver v2.0 Premium (Phase 10).
Реализует "Infinite Memory" через семантическое сжатие.
1. Генерирует краткое саммари диалога через LLM.
2. Сохраняет саммари и лог в RAG.
3. Очищает локальный JSONL контекст.
"""

import json
import structlog
import asyncio
from datetime import datetime
from pathlib import Path

logger = structlog.get_logger("MemoryArchiver")

class MemoryArchiver:
    def __init__(self, router, context_keeper):
        self.router = router
        self.memory = context_keeper
        self.rag = router.rag  # Используем RAG из роутера
        self.archival_threshold = 30  # Архивировать если сообщений > 30

    async def archive_old_chats(self):
        """
        Проходит по всем чатам и архивирует если контекст переполнен.
        """
        logger.info("📚 Checking Memory for Archival...")
        
        if not self.memory.base_path or not self.memory.base_path.exists():
            return

        for chat_dir in self.memory.base_path.iterdir():
            if not chat_dir.is_dir():
                continue
                
            try:
                chat_id = int(chat_dir.name)
                history_file = chat_dir / "history.jsonl"
                
                if not history_file.exists():
                    continue

                with open(history_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                if len(lines) < self.archival_threshold:
                    continue

                messages = [json.loads(line) for line in lines]
                
                # Архивируем
                success = await self.summarize_and_store(chat_id, messages)
                if success:
                    # Оставляем последние 10 сообщений для плавности
                    with open(history_file, "w", encoding="utf-8") as f:
                        for line in lines[-10:]:
                            f.write(line)
                    logger.info("清理历史成功", chat_id=chat_id)

            except Exception as e:
                logger.error("Archival loop error", chat=chat_dir.name, error=str(e))

    async def summarize_and_store(self, chat_id: int, messages: list) -> bool:
        """
        Использует LLM для создания семантического саммари.
        """
        if not messages:
            return False

        # 1. Подготовка текста для LLM
        history_text = "\n".join([f"{m.get('role', 'user')}: {m.get('text', '')}" for m in messages])
        
        summary_prompt = (
            "Сделай очень краткое, но информативное саммари этого диалога на РУССКОМ языке.\n"
            "Выдели ключевые темы, факты и договоренности.\n"
            "Это саммари будет сохранено в вечную память бота.\n\n"
            f"ДИАЛОГ:\n{history_text}\n\n"
            "САММАРИ:"
        )

        try:
            summary = await self.router.route_query(summary_prompt, task_type='chat')
            logger.info("📝 Summary generated", chat_id=chat_id)

            # 2. Сохранение в RAG
            doc_text = f"САММАРИ ДИАЛОГА {chat_id} ({datetime.now().date()}):\n{summary}\n\nЛОГ:\n{history_text}"
            
            self.rag.add_document(
                text=doc_text,
                metadata={
                    "source": "archive",
                    "chat_id": chat_id,
                    "archived_at": datetime.now().isoformat(),
                    "msg_count": len(messages),
                    "summary": summary[:200]
                },
                category="history"
            )
            
            # 3. Дополнительно сохраняем саммари в кэш ContextKeeper для быстрого доступа
            self.memory.save_summary(chat_id, summary)
            
            return True
        except Exception as e:
            logger.error("Summarization/Storage error", chat_id=chat_id, error=str(e))
            return False
