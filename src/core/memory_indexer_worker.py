"""
MemoryIndexerWorker — Phase 4 Memory Layer (Track E).

Отвечает за real-time индексацию входящих Telegram-сообщений:
  - принимает pyrofork-сообщения через `enqueue()` (producer side);
  - consumer loop: batch collect → PII redact → ChunkBuilder → SQL → embed;
  - фильтрует по whitelist (privacy-by-default) и пустой строке;
  - накапливает метрики в IndexerStats.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from src.core.memory_archive import ArchivePaths, open_archive
from src.core.memory_chunking import Chunk, ChunkBuilder, Message
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
        """Запускает worker: создаёт queue и consumer task."""
        if self._consumer_task is not None and not self._consumer_task.done():
            return
        self._queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._stop_requested = False
        self._stats.started_at = datetime.now(timezone.utc)
        self._stats.is_running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop())
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
        # Принудительно закрываем все ещё открытые builders.
        await self._force_flush_all_builders()
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

    # ---------------------------------------------------------------------------
    # Consumer loop.
    # ---------------------------------------------------------------------------

    async def _consumer_loop(self) -> None:
        """Основной цикл: собирает batch, делает flush, повторяет до остановки."""
        while not self._stop_requested or not self._queue.empty():  # type: ignore[union-attr]
            batch = await self._collect_batch()
            if not batch:
                continue
            try:
                await self._flush_batch(batch)
            except Exception as exc:
                self._stats.bump_failed("flush")
                logger.error("memory_indexer_flush_error", error=str(exc))
            finally:
                for _ in batch:
                    self._queue.task_done()  # type: ignore[union-attr]

    async def _collect_batch(self) -> list[QueuedMessage]:
        """
        Ждёт первое сообщение (с таймаутом), потом жадно тянет до batch_size.

        Возвращает пустой список при таймауте.
        """
        assert self._queue is not None
        batch: list[QueuedMessage] = []
        try:
            first = await asyncio.wait_for(
                self._queue.get(),
                timeout=self._batch_timeout_sec,
            )
        except asyncio.TimeoutError:
            return batch

        batch.append(first)
        # Жадный pull без ожидания — берём до batch_size.
        while len(batch) < self._batch_size:
            try:
                msg = self._queue.get_nowait()
                batch.append(msg)
            except asyncio.QueueEmpty:
                break

        return batch

    async def _flush_batch(self, batch: list[QueuedMessage]) -> None:
        """
        PII redact → ChunkBuilder.add → harvest_closed → SQL → embed.
        """
        now = datetime.now(timezone.utc)
        # per-chat чанкеры (берём из self._builders, чтобы сохранять state между batch'ами)
        dirty_chat_ids: set[str] = set()
        # Для watermark: per-chat последний message_id
        per_chat_last_msg_id: dict[str, str] = {}

        for qmsg in batch:
            # PII redact
            redaction = self._redactor.redact(qmsg.text)
            redacted_text = redaction.text

            # Строим Message для ChunkBuilder
            msg = Message(
                message_id=qmsg.message_id,
                chat_id=qmsg.chat_id,
                timestamp=qmsg.timestamp,
                text=redacted_text,
                sender_id=qmsg.sender_id,
                reply_to_message_id=qmsg.reply_to_message_id,
            )

            # Получаем или создаём builder для чата
            if qmsg.chat_id not in self._builders:
                self._builders[qmsg.chat_id] = ChunkBuilder(
                    time_gap=self._chunk_close_after,
                )
            self._builders[qmsg.chat_id].add(msg)
            dirty_chat_ids.add(qmsg.chat_id)

            # Обновляем watermark (последний message_id в batch'е)
            per_chat_last_msg_id[qmsg.chat_id] = qmsg.message_id

        # Harvest закрытых chunks из builder'ов
        new_chunks: list[tuple[Chunk, str, str]] = []  # (chunk, chunk_id, chat_title)
        chat_meta: dict[str, tuple[str, str]] = {}  # chat_id → (title, type)
        for qmsg in batch:
            chat_meta[qmsg.chat_id] = (qmsg.chat_title, qmsg.chat_type)

        for chat_id in dirty_chat_ids:
            builder = self._builders[chat_id]
            closed = builder.harvest_closed(now, self._chunk_close_after)
            for chunk in closed:
                if chunk.is_empty():
                    continue
                first_msg_id = chunk.messages[0].message_id
                chunk_id = _chunk_hash(chat_id, first_msg_id)
                new_chunks.append((chunk, chunk_id, chat_id))

        # SQL в отдельном потоке (синхронный sqlite)
        if new_chunks or per_chat_last_msg_id:
            committed_chunk_ids = await asyncio.to_thread(
                self._sync_flush_to_db,
                new_chunks,
                per_chat_last_msg_id,
                batch,
                chat_meta,
            )
        else:
            committed_chunk_ids = []

        # Обновляем статистику
        self._stats.processed_total += len(batch)
        self._stats.chunks_committed += len(committed_chunk_ids)
        self._stats.last_flush_at = now

        # Embed
        await self._maybe_embed_chunks(committed_chunk_ids)

    def _sync_flush_to_db(
        self,
        new_chunks: list[tuple[Chunk, str, str]],
        per_chat_last_msg_id: dict[str, str],
        batch: list[QueuedMessage],
        chat_meta: dict[str, tuple[str, str]],
    ) -> list[str]:
        """
        Синхронный SQL flush (вызывается через asyncio.to_thread).

        Возвращает список chunk_id которые реально вставлены.
        """
        conn = open_archive(self._paths)
        committed: list[str] = []
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("BEGIN;")

            # Upsert чатов
            for chat_id, (title, chat_type) in chat_meta.items():
                conn.execute(
                    "INSERT OR IGNORE INTO chats (chat_id, title, chat_type, message_count) "
                    "VALUES (?, ?, ?, 0);",
                    (chat_id, title, chat_type),
                )

            # Insert сообщений (с PII-redacted текстом)
            for qmsg in batch:
                redacted = self._redactor.redact(qmsg.text).text
                ts_str = (
                    qmsg.timestamp.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO messages "
                    "(message_id, chat_id, sender_id, timestamp, text_redacted, reply_to_id) "
                    "VALUES (?, ?, ?, ?, ?, ?);",
                    (
                        qmsg.message_id,
                        qmsg.chat_id,
                        qmsg.sender_id,
                        ts_str,
                        redacted,
                        qmsg.reply_to_message_id,
                    ),
                )

            # Insert chunks + chunk_messages + FTS
            for chunk, chunk_id, chat_id in new_chunks:
                if chunk.start_timestamp is None or chunk.end_timestamp is None:
                    continue
                start_str = (
                    chunk.start_timestamp.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
                )
                end_str = (
                    chunk.end_timestamp.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
                )
                cur = conn.execute(
                    "INSERT OR IGNORE INTO chunks "
                    "(chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?);",
                    (
                        chunk_id,
                        chat_id,
                        start_str,
                        end_str,
                        len(chunk.messages),
                        chunk.char_len,
                        chunk.text,
                    ),
                )
                rowid = cur.lastrowid
                if cur.rowcount > 0 and rowid is not None:
                    # chunk_messages
                    conn.executemany(
                        "INSERT OR IGNORE INTO chunk_messages (chunk_id, message_id, chat_id) "
                        "VALUES (?, ?, ?);",
                        [(chunk_id, m.message_id, chat_id) for m in chunk.messages],
                    )
                    # FTS5
                    conn.execute(
                        "INSERT INTO messages_fts (rowid, text_redacted) VALUES (?, ?);",
                        (rowid, chunk.text),
                    )
                    committed.append(chunk_id)

            # Watermark indexer_state
            for chat_id, last_msg_id in per_chat_last_msg_id.items():
                ts_now = (
                    datetime.now(timezone.utc)
                    .replace(tzinfo=None)
                    .isoformat(timespec="seconds") + "Z"
                )
                conn.execute(
                    "INSERT INTO indexer_state (chat_id, last_message_id, last_processed_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET "
                    "last_message_id = excluded.last_message_id, "
                    "last_processed_at = excluded.last_processed_at;",
                    (chat_id, last_msg_id, ts_now),
                )

            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            logger.error("memory_indexer_db_error", error=str(exc))
            self._stats.bump_failed("db")
        finally:
            conn.close()

        return committed

    async def _force_flush_all_builders(self) -> None:
        """
        Принудительно закрывает все открытые chunks во всех builders.
        Вызывается из stop() перед отменой consumer task.
        """
        new_chunks: list[tuple[Chunk, str, str]] = []
        chat_meta: dict[str, tuple[str, str]] = {}

        for chat_id, builder in list(self._builders.items()):
            # flush() закрывает абсолютно все — включая "ещё тёплые"
            all_closed = builder.flush()
            for chunk in all_closed:
                if chunk.is_empty():
                    continue
                first_msg_id = chunk.messages[0].message_id
                chunk_id = _chunk_hash(chat_id, first_msg_id)
                new_chunks.append((chunk, chunk_id, chat_id))
            chat_meta[chat_id] = (chat_id, "unknown")

        if not new_chunks:
            return

        committed_ids = await asyncio.to_thread(
            self._sync_flush_to_db,
            new_chunks,
            {},
            [],
            chat_meta,
        )
        self._stats.chunks_committed += len(committed_ids)
        await self._maybe_embed_chunks(committed_ids)

    async def _maybe_embed_chunks(self, chunk_ids: list[str]) -> None:
        """Lazy init embedder + embed_specific. Graceful skip если нет sqlite-vec."""
        if not chunk_ids:
            return
        if self._embedder is None:
            try:
                from src.core.memory_embedder import MemoryEmbedder  # noqa: PLC0415

                self._embedder = MemoryEmbedder(archive_paths=self._paths)
            except ImportError:
                if not self._stats.embed_disabled:
                    logger.warning("memory_indexer_embed_skipped_no_vec")
                self._stats.embed_disabled = True
                return
        try:
            await asyncio.to_thread(self._embedder.embed_specific, chunk_ids)
            self._stats.embeddings_committed += len(chunk_ids)
        except Exception as exc:
            self._stats.bump_failed("embed")
            logger.error("memory_indexer_embed_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _chunk_hash(chat_id: str, start_message_id: str) -> str:
    """Детерминированный chunk_id: sha256(chat_id + "\\x00" + start_msg_id)[:16]."""
    payload = f"{chat_id}\x00{start_message_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
