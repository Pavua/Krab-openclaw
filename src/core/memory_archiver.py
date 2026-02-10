# -*- coding: utf-8 -*-
"""
Memory Archiver v1.0 (Phase 10).
Модуль для реализации "Infinite Memory". 
Автоматически сжимает старые диалоги и сохраняет их в вечную память (RAG) 
с тегами 'archive' и 'history', освобождая контекстное окно.
"""

import json
import structlog
import asyncio
from datetime import datetime, timedelta

logger = structlog.get_logger("MemoryArchiver")

class MemoryArchiver:
    def __init__(self, rag_engine, context_keeper):
        self.rag = rag_engine
        self.memory = context_keeper
        self.archival_threshold_days = 2  # Архивировать диалоги старше 2 дней

    async def archive_old_chats(self):
        """
        Проходит по всем чатам и архивирует устаревшие сообщения.
        """
        logger.info("📚 Starting Memory Archival Process...")
        
        if not self.memory.base_path.exists():
            return

        for chat_dir in self.memory.base_path.iterdir():
            if not chat_dir.is_dir():
                continue
                
            try:
                chat_id = int(chat_dir.name)
                history_file = chat_dir / "history.jsonl"
                
                if not history_file.exists():
                    continue

                # Считываем все сообщения
                with open(history_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                if len(lines) < 20: # Не архивируем слишком короткие диалоги
                    continue

                messages = [json.loads(line) for line in lines]
                
                # Архивируем и очищаем файл
                success = await self.summarize_and_store(chat_id, messages)
                if success:
                    # Очищаем историю (оставляем последние 5 для связки)
                    with open(history_file, "w", encoding="utf-8") as f:
                        for line in lines[-5:]:
                            f.write(line)
                    logger.info(f"🧹 History cleaned for chat {chat_id}")

            except ValueError:
                continue # Не числовая папка
            except Exception as e:
                logger.error(f"Error archiving chat {chat_dir.name}: {e}")

    async def summarize_and_store(self, chat_id: int, messages: list):
        """
        Сжимает список сообщений в саммари и сохраняет в RAG.
        """
        if not messages:
            return False

        # Формируем текстовый блок для RAG
        text_block = f"--- ARCHIVED CHAT LOG {chat_id} ({datetime.now().date()}) ---\n"
        text_block += "\n".join([f"[{m.get('role', 'user')}]: {m.get('text', '')}" for m in messages])
        
        try:
            doc_id = f"archive_{chat_id}_{int(datetime.now().timestamp())}"
            self.rag.add_document(
                text=text_block,
                metadata={
                    "source": "archive",
                    "chat_id": chat_id,
                    "archived_at": datetime.now().isoformat(),
                    "msg_count": len(messages)
                },
                doc_id=doc_id,
                category="history",
                ttl_days=36500 # 100 лет (Infinite Memory)
            )
            logger.info(f"✅ Context archived for chat {chat_id} (DocID: {doc_id})")
            return True
        except Exception as e:
            logger.error(f"❌ Archival failed for {chat_id}: {e}")
            return False
