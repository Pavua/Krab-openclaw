"""
MemoryIndexerWorker — Phase 4 Memory Layer (Track E).

Отвечает за real-time индексацию входящих Telegram-сообщений:
  - принимает pyrofork-сообщения через `enqueue()` (producer side, Phase 4);
  - consumer loop (batch insert + chunking + embedding) добавляется в Phase 5;
  - фильтрует по whitelist (privacy-by-default) и пустой строке;
  - накапливает метрики в IndexerStats.

Только enqueue-сторона: `start()` создаёт очередь, `stop()` делает graceful
drain/cancel. Consumer task — TODO Phase 5.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from src.core.memory_archive import ArchivePaths
from src.core.memory_chunking import ChunkBuilder
from src.core.memory_pii_redactor import PIIRedactor
from src.core.memory_whitelist import MemoryWhitelist

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Константы.
# ---------------------------------------------------------------------------

DEFAULT_QUEUE_MAXSIZE: int = 10_000
DEFAULT_BATCH_SIZE: int = 20
DEFAULT_BATCH_TIMEOUT_SEC: float = 30.0
DEFAULT_CHUNK_CLOSE_AFTER: timedelta = timedelta(minutes=5)
DEFAULT_DRAIN_TIMEOUT_SEC: float = 10.0


# ---------------------------------------------------------------------------
# Dataclasses.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueuedMessage:
    """Минимальная проекция pyrofork-сообщения для consumer'а."""

    chat_id: str
    chat_title: str
    chat_type: str
    message_id: str
    sender_id: str | None
    text: str
    timestamp: datetime
    reply_to_message_id: str | None


@dataclass
class IndexerStats:
    """Снимок счётчиков worker'а."""

    queue_size: int = 0
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE
    enqueued_total: int = 0
    processed_total: int = 0
    chunks_committed: int = 0
    embeddings_committed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    dropped_queue_full: int = 0
    failed: dict[str, int] = field(default_factory=dict)
    last_flush_at: datetime | None = None
    last_flush_duration_sec: float = 0.0
    builders_active: int = 0
    started_at: datetime | None = None
    is_running: bool = False
    restarts: int = 0
    embed_disabled: bool = False

    def bump_skipped(self, reason: str) -> None:
        """Инкрементирует счётчик пропусков по причине."""
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def bump_failed(self, reason: str) -> None:
        """Инкрементирует счётчик ошибок по причине."""
        self.failed[reason] = self.failed.get(reason, 0) + 1


# ---------------------------------------------------------------------------
# Worker.
# ---------------------------------------------------------------------------


class MemoryIndexerWorker:
    """
    Worker real-time индексации сообщений.

    Producer side (Phase 4):
      - `start()` — инициализация очереди;
      - `enqueue()` — non-blocking enqueue с фильтрацией;
      - `stop()` — graceful drain + cancel.

    Consumer loop (Phase 5) добавляется отдельно.
    """

    def __init__(
        self,
        archive_paths: ArchivePaths | None = None,
        *,
        whitelist: MemoryWhitelist | None = None,
        redactor: PIIRedactor | None = None,
        embedder: Any | None = None,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_timeout_sec: float = DEFAULT_BATCH_TIMEOUT_SEC,
        chunk_close_after: timedelta = DEFAULT_CHUNK_CLOSE_AFTER,
    ) -> None:
        self._paths = archive_paths or ArchivePaths.default()
        self._whitelist = whitelist or MemoryWhitelist()
        self._redactor = redactor or PIIRedactor()
        self._embedder = embedder
        self._queue_maxsize = queue_maxsize
        self._batch_size = batch_size
        self._batch_timeout_sec = batch_timeout_sec
        self._chunk_close_after = chunk_close_after
        self._queue: asyncio.Queue[QueuedMessage] | None = None
        self._consumer_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._stop_requested: bool = False
        self._stats = IndexerStats(queue_maxsize=queue_maxsize)
        self._builders: dict[str, ChunkBuilder] = {}

    async def start(self) -> None:
        """Запускает worker: создаёт queue."""
        if self._consumer_task is not None and not self._consumer_task.done():
            return
        self._queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._stop_requested = False
        self._stats.started_at = datetime.now(timezone.utc)
        self._stats.is_running = True
        logger.info(
            "memory_indexer_started",
            queue_maxsize=self._queue_maxsize,
            batch_size=self._batch_size,
        )

    async def stop(
        self,
        *,
        drain: bool = True,
        timeout: float = DEFAULT_DRAIN_TIMEOUT_SEC,
    ) -> None:
        """Graceful shutdown."""
        self._stop_requested = True
        self._stats.is_running = False
        if drain and self._queue is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "memory_indexer_drain_timeout",
                    remaining=self._queue.qsize() if self._queue else 0,
                )
        if self._consumer_task is not None and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("memory_indexer_stopped")

    async def enqueue(self, pyrofork_message: Any) -> bool:
        """Non-blocking enqueue. Returns False on deny/overflow/empty."""
        if self._queue is None:
            return False

        # Извлекаем поля из pyrofork Message.
        try:
            chat = pyrofork_message.chat
            chat_id = str(chat.id)
            chat_title = getattr(chat, "title", None) or f"chat_{chat_id}"
            chat_type_raw = getattr(chat, "type", None)
            chat_type = (
                getattr(chat_type_raw, "value", str(chat_type_raw))
                if chat_type_raw is not None
                else "unknown"
            )
            text = pyrofork_message.text or ""
            message_id = str(pyrofork_message.id)
        except AttributeError as exc:
            self._stats.bump_failed("build")
            logger.warning("memory_indexer_enqueue_invalid_msg", error=str(exc))
            return False

        # Whitelist gate.
        decision = self._whitelist.is_allowed(chat_id, chat_title)
        if not decision.allowed:
            self._stats.bump_skipped("whitelist")
            return False

        # Empty text gate.
        if not text.strip():
            self._stats.bump_skipped("empty_text")
            return False

        # Строим QueuedMessage.
        from_user = getattr(pyrofork_message, "from_user", None)
        sender_id = str(from_user.id) if from_user and from_user.id else None
        date = pyrofork_message.date
        if isinstance(date, datetime) and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        reply_to_raw = getattr(pyrofork_message, "reply_to_message_id", None)
        reply_to = str(reply_to_raw) if reply_to_raw else None

        queued = QueuedMessage(
            chat_id=chat_id,
            chat_title=chat_title,
            chat_type=str(chat_type),
            message_id=message_id,
            sender_id=sender_id,
            text=text,
            timestamp=date,
            reply_to_message_id=reply_to,
        )

        try:
            self._queue.put_nowait(queued)
        except asyncio.QueueFull:
            self._stats.dropped_queue_full += 1
            return False

        self._stats.enqueued_total += 1
        return True

    def get_stats(self) -> IndexerStats:
        """Снимок текущих счётчиков."""
        if self._queue is not None:
            self._stats.queue_size = self._queue.qsize()
        self._stats.builders_active = len(self._builders)
        return self._stats
