"""
Инкрементальный индексатор Memory Layer (Phase 4).

Назначение:
  Получает КАЖДОЕ входящее Telegram-сообщение через `ingest()`,
  буферизирует, дедуплицирует, batch'ит через PII scrubber + chunker
  и записывает в archive.db (FTS5 + опционально vec embeddings).

Lifecycle:
  1. Track B создаёт экземпляр в `userbot_bridge.__init__()`:
       `self._memory_indexer = MemoryIndexerWorker()`
  2. `start()` запускается в `aux_tasks` (рядом с proactive_watch):
       `self._memory_indexer_task = asyncio.create_task(self._memory_indexer.start())`
  3. `ingest(msg)` вызывается из `_process_message()` — fire-and-forget:
       `asyncio.create_task(self._memory_indexer.ingest(msg))`
  4. Внутренний flush loop каждые `flush_interval_sec` или при буфере > `flush_batch_size`.
  5. `stop()` при shutdown Краба — final flush + закрытие БД.

Pipeline:
  msg → whitelist check → PII redact → asyncio.Queue
  flush: Queue drain → chunk_messages() → INSERT chats/messages/chunks → FTS5 sync
         → embed batch (если embedder available)

Не трогаем:
  - userbot_bridge.py — Track B сам вставит hook
  - memory_engine.py — ChromaDB facts layer, другой namespace
  - memory_retrieval.py / memory_commands.py — downstream consumer, не наша забота

Duck-typing для Pyrogram Message:
  ingest() принимает любой объект с `.chat.id`, `.id`, `.text`, `.from_user`,
  `.date`, `.reply_to_message_id` — для тестов без pyrofork.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from structlog import get_logger

from src.core.memory_archive import (
    ArchivePaths,
    create_schema,
    open_archive,
)
from src.core.memory_chunking import (
    ChunkBuilder,
    Message as ChunkMessage,
)
from src.core.memory_pii_redactor import PIIRedactor
from src.core.memory_whitelist import MemoryWhitelist

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Stats.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FlushStats:
    """Результат одного flush'а."""

    messages_ingested: int
    messages_skipped: int   # whitelist deny / duplicate / empty text
    chunks_created: int
    fts_synced: int
    vectors_embedded: int   # 0 если embedder не подключён


@dataclass
class IndexerStats:
    """Кумулятивная статистика за lifetime worker'а."""

    total_ingested: int = 0
    total_skipped: int = 0
    total_chunks: int = 0
    total_flushes: int = 0
    last_flush_at: datetime | None = None


# ---------------------------------------------------------------------------
# Worker.
# ---------------------------------------------------------------------------

# Лимиты по умолчанию — умеренные для userbot'а с ~100-500 msgs/day.
DEFAULT_FLUSH_INTERVAL = 60.0    # секунд
DEFAULT_FLUSH_BATCH_SIZE = 50    # сообщений в буфере
DEFAULT_MAX_QUEUE_SIZE = 5000    # жёсткий потолок очереди (back-pressure)


class MemoryIndexerWorker:
    """
    Async background worker: buffer → flush → archive.db.

    Конструктор — ленивый: БД, whitelist, redactor инициализируются
    при первом flush'е. Это позволяет безопасно создавать объект
    в `__init__` userbot_bridge без побочных эффектов.
    """

    def __init__(
        self,
        archive_paths: ArchivePaths | None = None,
        whitelist: MemoryWhitelist | None = None,
        redactor: PIIRedactor | None = None,
        flush_interval_sec: float = DEFAULT_FLUSH_INTERVAL,
        flush_batch_size: int = DEFAULT_FLUSH_BATCH_SIZE,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        # Инъекция для тестов (без реального Model2Vec).
        _embedder: Any | None = None,
    ) -> None:
        self._paths = archive_paths or ArchivePaths.default()
        self._whitelist = whitelist
        self._redactor = redactor
        self._flush_interval = flush_interval_sec
        self._flush_batch_size = flush_batch_size
        self._embedder = _embedder

        self._queue: asyncio.Queue[_IngestItem] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._seen_ids: set[str] = set()  # дедупликация: "chat_id:msg_id"
        self._stats = IndexerStats()
        self._running = False
        self._task: asyncio.Task | None = None

        # Lazy-init.
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Запускает фоновый flush-loop. Идемпотентно — повторный вызов no-op.
        """
        if self._running:
            return
        self._running = True
        logger.info(
            "memory_indexer_started",
            interval=self._flush_interval,
            batch=self._flush_batch_size,
        )
        # Flush-loop работает бесконечно, пока не вызовется stop().
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
            except asyncio.CancelledError:
                break
            if not self._queue.empty():
                await self._do_flush()

    async def stop(self) -> None:
        """
        Graceful shutdown: финальный flush, закрытие БД.
        """
        self._running = False
        if not self._queue.empty():
            await self._do_flush()
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
        logger.info(
            "memory_indexer_stopped",
            total_ingested=self._stats.total_ingested,
            total_flushes=self._stats.total_flushes,
        )

    # ------------------------------------------------------------------
    # Public API: ingest.
    # ------------------------------------------------------------------

    async def ingest(self, message: Any) -> None:
        """
        Принимает Pyrogram Message (или duck-typed mock). Мгновенный return.

        Проверяет whitelist, дедуплицирует, ставит в очередь.
        Если очередь полна — drop с warning (back-pressure).
        """
        try:
            item = _extract_ingest_item(message)
        except Exception as exc:  # noqa: BLE001 — не ронять userbot
            logger.debug("memory_indexer_extract_failed", error=str(exc))
            return

        # Пустой текст — skip (media без caption).
        if not item.text.strip():
            self._stats.total_skipped += 1
            return

        # Whitelist (lazy-init).
        wl = self._ensure_whitelist()
        decision = wl.is_allowed(item.chat_id, item.chat_title)
        if not decision.allowed:
            self._stats.total_skipped += 1
            return

        # Дедупликация в рамках текущего buffer'а.
        dedup_key = f"{item.chat_id}:{item.message_id}"
        if dedup_key in self._seen_ids:
            self._stats.total_skipped += 1
            return

        # Ставим в очередь.
        try:
            self._queue.put_nowait(item)
            self._seen_ids.add(dedup_key)
        except asyncio.QueueFull:
            logger.warning(
                "memory_indexer_queue_full",
                queue_size=self._queue.qsize(),
                dropped_msg=dedup_key,
            )
            self._stats.total_skipped += 1
            return

        # Eager flush если буфер полон.
        if self._queue.qsize() >= self._flush_batch_size:
            await self._do_flush()

    # ------------------------------------------------------------------
    # Public API: manual flush + stats.
    # ------------------------------------------------------------------

    async def flush(self) -> FlushStats:
        """Принудительный flush (для тестов и graceful shutdown)."""
        return await self._do_flush()

    @property
    def stats(self) -> IndexerStats:
        return self._stats

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # Flush pipeline.
    # ------------------------------------------------------------------

    async def _do_flush(self) -> FlushStats:
        """
        Drain queue → redact → chunk → insert into archive.db → FTS sync.
        Синхронные SQLite-операции обёрнуты в to_thread для async-совместимости.
        """
        items = _drain_queue(self._queue)
        if not items:
            return FlushStats(0, 0, 0, 0, 0)

        conn = self._ensure_connection()
        if conn is None:
            # БД недоступна — кладём обратно? Нет, теряем (fire-and-forget).
            logger.warning("memory_indexer_flush_no_db", dropped=len(items))
            return FlushStats(0, len(items), 0, 0, 0)

        redactor = self._ensure_redactor()

        # Pipeline (sync I/O — оборачиваем в to_thread).
        stats = await asyncio.to_thread(
            _flush_sync, conn, items, redactor, self._embedder
        )

        # Очищаем seen_ids для обработанных (они теперь в БД, дубли будут
        # ловиться INSERT OR IGNORE на уровне schema).
        for item in items:
            self._seen_ids.discard(f"{item.chat_id}:{item.message_id}")

        # Кумулятивная статистика.
        self._stats.total_ingested += stats.messages_ingested
        self._stats.total_skipped += stats.messages_skipped
        self._stats.total_chunks += stats.chunks_created
        self._stats.total_flushes += 1
        self._stats.last_flush_at = datetime.now(timezone.utc)

        logger.info(
            "memory_indexer_flushed",
            ingested=stats.messages_ingested,
            skipped=stats.messages_skipped,
            chunks=stats.chunks_created,
            fts=stats.fts_synced,
            vecs=stats.vectors_embedded,
        )
        return stats

    # ------------------------------------------------------------------
    # Lazy-init.
    # ------------------------------------------------------------------

    def _ensure_connection(self) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        try:
            # Не используем open_archive() напрямую, потому что flush
            # работает в другом thread через asyncio.to_thread — нужен
            # check_same_thread=False. open_archive() не поддерживает
            # этот параметр, а добавлять его в общий API ради одного
            # consumer не хочется.
            self._paths.dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._paths.db),
                check_same_thread=False,
            )
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            create_schema(conn)
            self._conn = conn
            return conn
        except (sqlite3.Error, OSError) as exc:
            logger.warning("memory_indexer_db_init_failed", error=str(exc))
            return None

    def _ensure_whitelist(self) -> MemoryWhitelist:
        if self._whitelist is None:
            self._whitelist = MemoryWhitelist()
        return self._whitelist

    def _ensure_redactor(self) -> PIIRedactor:
        if self._redactor is None:
            self._redactor = PIIRedactor()
        return self._redactor


# ---------------------------------------------------------------------------
# Internal data + helpers.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _IngestItem:
    """Минимальная проекция Pyrogram Message для очереди."""

    message_id: str
    chat_id: str
    chat_title: str | None
    sender_id: str | None
    timestamp: datetime
    text: str
    reply_to_message_id: str | None


def _extract_ingest_item(message: Any) -> _IngestItem:
    """
    Duck-typing: принимает Pyrogram Message или тестовый mock.
    Вытаскивает нужные поля, конвертирует типы.
    """
    chat = message.chat
    chat_id = str(chat.id) if hasattr(chat, "id") else str(chat)
    chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", None)

    from_user = getattr(message, "from_user", None)
    sender_id = str(from_user.id) if from_user and hasattr(from_user, "id") else None

    text = message.text or message.caption or ""

    reply_id = getattr(message, "reply_to_message_id", None)
    if reply_id is not None:
        reply_id = str(reply_id)

    ts = getattr(message, "date", None)
    if ts is None:
        ts = datetime.now(timezone.utc)
    elif isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, tz=timezone.utc)

    return _IngestItem(
        message_id=str(message.id),
        chat_id=chat_id,
        chat_title=chat_title,
        sender_id=sender_id,
        timestamp=ts,
        text=text,
        reply_to_message_id=reply_id,
    )


def _drain_queue(queue: asyncio.Queue[_IngestItem]) -> list[_IngestItem]:
    """Достаёт все элементы из очереди без блокировки."""
    items: list[_IngestItem] = []
    while not queue.empty():
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def _chunk_id_hash(chat_id: str, first_msg_id: str) -> str:
    """Стабильный hash для chunk_id (тот же что в bootstrap)."""
    raw = f"{chat_id}:{first_msg_id}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _flush_sync(
    conn: sqlite3.Connection,
    items: list[_IngestItem],
    redactor: PIIRedactor,
    embedder: Any | None,
) -> FlushStats:
    """
    Синхронный flush pipeline (вызывается через asyncio.to_thread).
    Работает в одной транзакции — откат при ошибке.
    """
    ingested = 0
    skipped = 0
    chunks_created = 0
    fts_synced = 0
    vecs_embedded = 0

    # Группируем по chat_id (chunking per-chat).
    by_chat: dict[str, list[_IngestItem]] = {}
    for item in items:
        by_chat.setdefault(item.chat_id, []).append(item)

    for chat_id, chat_items in by_chat.items():
        # Ensure chat row.
        title = chat_items[0].chat_title
        conn.execute(
            "INSERT OR IGNORE INTO chats(chat_id, title) VALUES (?, ?);",
            (chat_id, title),
        )

        # Redact + prepare ChunkMessages.
        chunk_msgs: list[ChunkMessage] = []
        for item in chat_items:
            result = redactor.redact(item.text)
            redacted = result.text
            if not redacted.strip():
                skipped += 1
                continue

            # Insert message (идемпотентно).
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO messages
                        (message_id, chat_id, sender_id, timestamp,
                         text_redacted, reply_to_id)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        item.message_id,
                        chat_id,
                        item.sender_id,
                        item.timestamp.isoformat(),
                        redacted,
                        item.reply_to_message_id,
                    ),
                )
            except sqlite3.IntegrityError:
                skipped += 1
                continue

            ingested += 1
            chunk_msgs.append(
                ChunkMessage(
                    message_id=item.message_id,
                    chat_id=chat_id,
                    timestamp=item.timestamp,
                    text=redacted,
                    sender_id=item.sender_id,
                    reply_to_message_id=item.reply_to_message_id,
                )
            )

        if not chunk_msgs:
            continue

        # Chunk (sorted by timestamp).
        chunk_msgs.sort(key=lambda m: m.timestamp)
        builder = ChunkBuilder()
        for m in chunk_msgs:
            builder.add(m)
        chunks = builder.flush()

        for chunk in chunks:
            cid = _chunk_id_hash(chat_id, chunk.messages[0].message_id)
            start_ts = chunk.start_timestamp or datetime.now(timezone.utc)
            end_ts = chunk.end_timestamp or start_ts

            # Insert chunk (или skip если уже есть — incremental safe).
            try:
                cur = conn.execute(
                    """
                    INSERT INTO chunks
                        (chunk_id, chat_id, start_ts, end_ts,
                         message_count, char_len, text_redacted)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        cid,
                        chat_id,
                        start_ts.isoformat(),
                        end_ts.isoformat(),
                        len(chunk.messages),
                        chunk.char_len,
                        chunk.text,
                    ),
                )
                chunk_rowid = cur.lastrowid
            except sqlite3.IntegrityError:
                # Chunk уже есть (duplicate chunk_id) — skip.
                continue

            chunks_created += 1

            # chunk_messages bridge.
            for m in chunk.messages:
                conn.execute(
                    "INSERT OR IGNORE INTO chunk_messages"
                    "(chunk_id, message_id, chat_id) VALUES (?, ?, ?);",
                    (cid, m.message_id, chat_id),
                )

            # FTS5 sync.
            conn.execute(
                "INSERT INTO messages_fts(rowid, text_redacted) VALUES (?, ?);",
                (chunk_rowid, chunk.text),
            )
            fts_synced += 1

        # Update watermark.
        last_item = chat_items[-1]
        conn.execute(
            """
            INSERT OR REPLACE INTO indexer_state
                (chat_id, last_message_id, last_processed_at)
            VALUES (?, ?, ?);
            """,
            (
                chat_id,
                last_item.message_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    conn.commit()

    # Embed batch (если embedder доступен).
    if embedder is not None and chunks_created > 0:
        try:
            embed_stats = embedder.embed_all_unindexed()
            vecs_embedded = embed_stats.chunks_processed
        except Exception as exc:  # noqa: BLE001 — embedder optional
            logger.warning("memory_indexer_embed_failed", error=str(exc))

    return FlushStats(
        messages_ingested=ingested,
        messages_skipped=skipped,
        chunks_created=chunks_created,
        fts_synced=fts_synced,
        vectors_embedded=vecs_embedded,
    )
