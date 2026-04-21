"""
Per-chat Message Batcher — буферизует incoming msgs пока предыдущий LLM-call ещё идёт.

Когда LLM busy для chat_id → new msgs queued. При release → batched в один prompt.

TODO Session 13.X: integrate в _process_message когда ChatWindow integration landed.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from structlog import get_logger

logger = get_logger(__name__)

MAX_BATCH_SIZE = int(os.environ.get("BATCH_MAX_SIZE", "5"))
MAX_BATCH_AGE_SEC = float(os.environ.get("BATCH_MAX_AGE_SEC", "30"))
BATCH_FORMAT = os.environ.get(
    "BATCH_FORMAT",
    "Пользователь прислал {count} сообщений подряд:\n\n{messages}\n\nОтветь coherently объединяя контекст.",
)


@dataclass
class PendingMessage:
    text: str
    sender_id: str
    ts: float = field(default_factory=time.time)
    message_id: Optional[int] = None


@dataclass
class ChatBatch:
    """Buffer pending messages для одного чата пока LLM busy."""

    chat_id: str
    pending: list[PendingMessage] = field(default_factory=list)
    busy: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def age_sec(self) -> float:
        if not self.pending:
            return 0.0
        return time.time() - self.pending[0].ts

    def size(self) -> int:
        return len(self.pending)

    def should_flush(self) -> bool:
        """Есть ли причина flush прямо сейчас?"""
        if not self.pending:
            return False
        if self.size() >= MAX_BATCH_SIZE:
            return True
        if self.age_sec() >= MAX_BATCH_AGE_SEC:
            return True
        return False

    def drain(self) -> list[PendingMessage]:
        """Take all pending, reset buffer."""
        msgs = self.pending
        self.pending = []
        return msgs

    def format_batched_prompt(self) -> str:
        """Объединяет pending messages в single prompt."""
        if not self.pending:
            return ""
        lines = []
        for i, m in enumerate(self.pending, 1):
            lines.append(f"[{i}] {m.text}")
        return BATCH_FORMAT.format(count=len(self.pending), messages="\n".join(lines))


class MessageBatcher:
    """Singleton per-chat batcher."""

    def __init__(self) -> None:
        self._batches: dict[str, ChatBatch] = {}

    def _get_batch(self, chat_id: str) -> ChatBatch:
        chat_id = str(chat_id)
        if chat_id not in self._batches:
            self._batches[chat_id] = ChatBatch(chat_id=chat_id)
        return self._batches[chat_id]

    async def try_add_or_flush(
        self,
        chat_id: str,
        msg: PendingMessage,
        processor: Callable,  # async processor(chat_id, combined_prompt) -> response
    ) -> tuple[str, Optional[str]]:
        """
        Add message:
        - If LLM busy for chat → buffer, return ("buffered", None)
        - If not busy → mark busy, process immediately, return ("immediate", response)
        """
        batch = self._get_batch(chat_id)
        async with batch.lock:
            if batch.busy:
                batch.pending.append(msg)
                logger.info(
                    "message_buffered",
                    chat_id=chat_id,
                    buffer_size=batch.size(),
                )
                return ("buffered", None)

            batch.busy = True
            batch.pending.append(msg)

        try:
            # Обрабатываем первое сообщение (или накопленный batch если уже были)
            combined = batch.format_batched_prompt()
            response = await processor(chat_id, combined)
            batch.pending = []
            return ("immediate", response)
        finally:
            # После обработки проверяем накопленные в очереди → запускаем flush
            async with batch.lock:
                batch.busy = False
                has_pending = batch.size() > 0

            if has_pending:
                # Не блокируем текущую task — flush идёт фоново
                asyncio.create_task(self._flush_batch(chat_id, processor))

    async def _flush_batch(self, chat_id: str, processor: Callable) -> None:
        """Process accumulated batch."""
        batch = self._get_batch(chat_id)
        async with batch.lock:
            if batch.busy or not batch.pending:
                return
            batch.busy = True

        try:
            combined = batch.format_batched_prompt()
            batch.pending = []
            logger.info("batch_flushing", chat_id=chat_id, combined_length=len(combined))
            await processor(chat_id, combined)
        except Exception as e:
            logger.error("batch_flush_failed", chat_id=chat_id, error=str(e))
        finally:
            async with batch.lock:
                batch.busy = False
                # Рекурсивный flush если ещё накопилось во время обработки
                has_more = batch.size() > 0
            if has_more:
                asyncio.create_task(self._flush_batch(chat_id, processor))

    def stats(self) -> dict:
        return {
            "total_batches": len(self._batches),
            "active_batches": sum(1 for b in self._batches.values() if b.busy),
            "total_pending": sum(b.size() for b in self._batches.values()),
        }


# Singleton
message_batcher = MessageBatcher()
