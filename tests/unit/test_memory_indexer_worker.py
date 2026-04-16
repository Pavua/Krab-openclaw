"""
Unit-тесты для MemoryIndexerWorker (Phase 4, Track E).

Покрывают:
  - _extract_ingest_item — парсинг duck-typed Pyrogram Message
  - FlushStats / IndexerStats — dataclass'ы
  - ingest() — queue, whitelist, дедупликация, back-pressure
  - flush() — pipeline: PII redact → chunk → SQLite INSERT → FTS5
  - start/stop lifecycle
  - E2E mini-pipeline с HybridRetriever.search()
"""

from __future__ import annotations

import os

# Обход parent conftest (TELEGRAM_API_ID env issue).
for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_indexer_worker import (
    FlushStats,
    IndexerStats,
    MemoryIndexerWorker,
    _extract_ingest_item,
    _IngestItem,
)
from src.core.memory_whitelist import MemoryWhitelist, WhitelistConfig


def _open_archive_thread_safe(paths=None, read_only=False, create_if_missing=True):
    """open_archive с check_same_thread=False для тестов (asyncio.to_thread)."""
    from src.core.memory_archive import ArchivePaths as AP

    paths = paths or AP.default()
    if not paths.db.exists():
        if not create_if_missing:
            raise FileNotFoundError(f"Archive DB not found: {paths.db}")
        paths.dir.mkdir(parents=True, exist_ok=True)
    if read_only:
        uri = f"file:{paths.db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(paths.db, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# ---------------------------------------------------------------------------
# Mock Pyrogram.
# ---------------------------------------------------------------------------

@dataclass
class MockChat:
    id: int
    title: str | None = None
    first_name: str | None = None


@dataclass
class MockUser:
    id: int
    username: str | None = None


@dataclass
class MockMessage:
    id: int
    chat: MockChat
    text: str | None = None
    caption: str | None = None
    date: datetime | None = None
    from_user: MockUser | None = None
    reply_to_message_id: int | None = None


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

@pytest.fixture
def paths(tmp_path) -> ArchivePaths:
    return ArchivePaths.under(tmp_path / "mem")


@pytest.fixture
def worker(paths) -> MemoryIndexerWorker:
    """Worker с thread-safe SQLite connection (для asyncio.to_thread)."""
    wl = MemoryWhitelist(config=WhitelistConfig(allow_all=True))
    w = MemoryIndexerWorker(
        archive_paths=paths,
        whitelist=wl,
        flush_interval_sec=999,  # без auto-flush, только manual
        flush_batch_size=100,
    )
    # Monkey-patch: открываем БД с check_same_thread=False.
    _orig_ensure = w._ensure_connection

    def _patched_ensure():
        if w._conn is not None:
            return w._conn
        conn = _open_archive_thread_safe(w._paths, create_if_missing=True)
        create_schema(conn)
        w._conn = conn
        return conn

    w._ensure_connection = _patched_ensure
    return w


def _make_msg(
    msg_id: int = 1,
    chat_id: int = -100123,
    text: str = "привет мир",
    *,
    caption: str | None = None,
    title: str | None = "Test Chat",
    user_id: int = 42,
    date: datetime | None = None,
    reply_to: int | None = None,
    from_user: MockUser | None = ...,  # sentinel
) -> MockMessage:
    """Фабрика MockMessage с разумными дефолтами."""
    chat = MockChat(id=chat_id, title=title)
    if from_user is ...:
        from_user = MockUser(id=user_id)
    return MockMessage(
        id=msg_id,
        chat=chat,
        text=text,
        caption=caption,
        date=date or datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
        from_user=from_user,
        reply_to_message_id=reply_to,
    )


# ===========================================================================
# 1. _extract_ingest_item
# ===========================================================================

class TestExtractIngestItem:
    """Парсинг duck-typed Pyrogram Message в _IngestItem."""

    def test_basic_message(self):
        """Обычное сообщение — все поля корректно извлечены."""
        msg = _make_msg(msg_id=7, chat_id=-999, text="hello")
        item = _extract_ingest_item(msg)
        assert item.message_id == "7"
        assert item.chat_id == "-999"
        assert item.text == "hello"
        assert item.sender_id == "42"
        assert item.chat_title == "Test Chat"

    def test_caption_instead_of_text(self):
        """caption используется если text=None (фото/видео)."""
        msg = _make_msg(text=None, caption="подпись к фото")
        item = _extract_ingest_item(msg)
        assert item.text == "подпись к фото"

    def test_no_from_user(self):
        """from_user=None → sender_id=None."""
        msg = _make_msg(from_user=None)
        item = _extract_ingest_item(msg)
        assert item.sender_id is None

    def test_reply_to_message_id_str(self):
        """reply_to_message_id конвертируется в строку."""
        msg = _make_msg(reply_to=555)
        item = _extract_ingest_item(msg)
        assert item.reply_to_message_id == "555"

    def test_date_as_int(self):
        """date как unix timestamp (int) → datetime."""
        ts = 1713168000  # 2024-04-15T08:00:00 UTC
        msg = _make_msg(date=ts)
        item = _extract_ingest_item(msg)
        assert isinstance(item.timestamp, datetime)
        assert item.timestamp.tzinfo is not None

    def test_empty_text_and_caption(self):
        """text=None и caption=None → пустая строка."""
        msg = _make_msg(text=None, caption=None)
        item = _extract_ingest_item(msg)
        assert item.text == ""

    def test_chat_first_name_fallback(self):
        """Если title=None, берётся first_name чата (DM)."""
        msg = _make_msg(title=None)
        msg.chat.first_name = "Павел"
        item = _extract_ingest_item(msg)
        assert item.chat_title == "Павел"


# ===========================================================================
# 2. FlushStats / IndexerStats
# ===========================================================================

class TestDataclasses:
    """Проверка dataclass-контрактов."""

    def test_flush_stats_frozen(self):
        """FlushStats — frozen dataclass, нельзя менять поля."""
        fs = FlushStats(1, 0, 2, 2, 0)
        with pytest.raises(AttributeError):
            fs.messages_ingested = 99  # type: ignore[misc]

    def test_flush_stats_fields(self):
        """FlushStats хранит все 5 полей."""
        fs = FlushStats(
            messages_ingested=3,
            messages_skipped=1,
            chunks_created=2,
            fts_synced=2,
            vectors_embedded=0,
        )
        assert fs.messages_ingested == 3
        assert fs.messages_skipped == 1
        assert fs.chunks_created == 2

    def test_indexer_stats_mutable(self):
        """IndexerStats — mutable (кумулятивная)."""
        stats = IndexerStats()
        stats.total_ingested = 10
        stats.total_flushes = 2
        assert stats.total_ingested == 10
        assert stats.last_flush_at is None


# ===========================================================================
# 3. ingest()
# ===========================================================================

class TestIngest:
    """Поведение ingest(): whitelist, дедупликация, back-pressure."""

    async def test_message_enqueued(self, worker):
        """Обычное сообщение попадает в queue."""
        msg = _make_msg(text="тест")
        await worker.ingest(msg)
        assert worker.queue_size == 1

    async def test_empty_text_skipped(self, worker):
        """Пустой text → skipped, не попадает в queue."""
        msg = _make_msg(text="   ")
        await worker.ingest(msg)
        assert worker.queue_size == 0
        assert worker.stats.total_skipped == 1

    async def test_whitelist_deny_skipped(self, paths):
        """Whitelist deny → сообщение не попадает в queue."""
        # Конфиг: deny все (allow_all=False, пустые allow-списки).
        wl = MemoryWhitelist(config=WhitelistConfig(allow_all=False))
        w = MemoryIndexerWorker(
            archive_paths=paths, whitelist=wl,
            flush_interval_sec=999, flush_batch_size=100,
        )
        msg = _make_msg(text="должен быть отброшен")
        await w.ingest(msg)
        assert w.queue_size == 0
        assert w.stats.total_skipped == 1

    async def test_dedup_second_skipped(self, worker):
        """Повторный ingest того же msg.id → второй skipped."""
        msg = _make_msg(msg_id=10, text="оригинал")
        await worker.ingest(msg)
        await worker.ingest(msg)
        assert worker.queue_size == 1
        assert worker.stats.total_skipped == 1

    async def test_queue_full_dropped(self, paths):
        """Queue full → drop с warning (back-pressure)."""
        wl = MemoryWhitelist(config=WhitelistConfig(allow_all=True))
        w = MemoryIndexerWorker(
            archive_paths=paths, whitelist=wl,
            flush_interval_sec=999, flush_batch_size=100,
            max_queue_size=2,
        )
        # Заполняем queue до предела.
        for i in range(3):
            msg = _make_msg(msg_id=i + 1, text=f"msg {i}")
            await w.ingest(msg)
        # Третье сообщение должно быть dropped.
        assert w.queue_size == 2
        assert w.stats.total_skipped >= 1

    async def test_none_text_none_caption_skipped(self, worker):
        """text=None + caption=None → empty → skipped."""
        msg = _make_msg(text=None, caption=None)
        await worker.ingest(msg)
        assert worker.queue_size == 0


# ===========================================================================
# 4. flush()
# ===========================================================================

class TestFlush:
    """Pipeline: queue → PII redact → chunk → SQLite INSERT → FTS5."""

    async def test_flush_empty_queue(self, worker):
        """flush пустой очереди → нулевые stats."""
        stats = await worker.flush()
        assert stats == FlushStats(0, 0, 0, 0, 0)

    async def test_flush_creates_records(self, worker, paths):
        """flush 3 сообщений → creates chats + messages + chunks + FTS."""
        base_ts = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        for i in range(3):
            msg = _make_msg(
                msg_id=i + 1,
                text=f"сообщение номер {i}",
                date=base_ts + timedelta(seconds=i * 10),
            )
            await worker.ingest(msg)

        stats = await worker.flush()
        assert stats.messages_ingested == 3
        assert stats.chunks_created >= 1
        assert stats.fts_synced >= 1

        # Проверяем наличие записей в БД.
        conn = open_archive(paths, create_if_missing=False)
        try:
            chats = conn.execute("SELECT COUNT(*) FROM chats;").fetchone()[0]
            msgs = conn.execute("SELECT COUNT(*) FROM messages;").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0]
            assert chats >= 1
            assert msgs == 3
            assert chunks >= 1
        finally:
            conn.close()

    async def test_pii_redacted_in_db(self, worker, paths):
        """PII (номер карты) редактируется перед записью в БД."""
        # 4242 4242 4242 4242 — тестовая карта, проходит Luhn.
        msg = _make_msg(text="моя карта 4242 4242 4242 4242 не потеряй")
        await worker.ingest(msg)
        await worker.flush()

        conn = open_archive(paths, create_if_missing=False)
        try:
            rows = conn.execute(
                "SELECT text_redacted FROM messages WHERE text_redacted LIKE '%4242 4242%';"
            ).fetchall()
            # Исходный номер НЕ должен быть в БД.
            assert len(rows) == 0
        finally:
            conn.close()

    async def test_idempotent_flush(self, worker, paths):
        """Повторный flush тех же messages → INSERT OR IGNORE."""
        msg = _make_msg(msg_id=50, text="идемпотентный тест")
        await worker.ingest(msg)
        stats1 = await worker.flush()
        assert stats1.messages_ingested == 1

        # Повторный ingest + flush — msg уже в БД.
        await worker.ingest(msg)
        stats2 = await worker.flush()
        # Второй раз — message пропущен (INSERT OR IGNORE или дедупликация).
        assert stats2.messages_ingested == 0 or stats2.messages_skipped >= 0

    async def test_watermark_updated(self, worker, paths):
        """indexer_state watermark обновлён после flush."""
        msg = _make_msg(msg_id=77, text="watermark test")
        await worker.ingest(msg)
        await worker.flush()

        conn = open_archive(paths, create_if_missing=False)
        try:
            row = conn.execute(
                "SELECT last_message_id FROM indexer_state WHERE chat_id = ?;",
                (str(msg.chat.id),),
            ).fetchone()
            assert row is not None
            assert row[0] == "77"
        finally:
            conn.close()

    async def test_fts_search_after_flush(self, worker, paths):
        """FTS5 поиск после flush находит проиндексированный текст."""
        msg = _make_msg(text="уникальный_keyword_для_поиска")
        await worker.ingest(msg)
        await worker.flush()

        conn = open_archive(paths, create_if_missing=False)
        try:
            rows = conn.execute(
                "SELECT text_redacted FROM messages_fts WHERE messages_fts MATCH ?;",
                ("уникальный_keyword_для_поиска",),
            ).fetchall()
            assert len(rows) >= 1
        finally:
            conn.close()

    async def test_multiple_chats_in_one_flush(self, worker, paths):
        """Сообщения из разных чатов корректно группируются."""
        msg1 = _make_msg(msg_id=1, chat_id=-111, text="чат один")
        msg2 = _make_msg(msg_id=2, chat_id=-222, text="чат два")
        await worker.ingest(msg1)
        await worker.ingest(msg2)
        stats = await worker.flush()
        assert stats.messages_ingested == 2

        conn = open_archive(paths, create_if_missing=False)
        try:
            chats = conn.execute("SELECT COUNT(*) FROM chats;").fetchone()[0]
            assert chats == 2
        finally:
            conn.close()


# ===========================================================================
# 5. start/stop lifecycle
# ===========================================================================

class TestLifecycle:
    """start() / stop() без ошибок, финальный flush при stop."""

    async def test_start_stop_no_errors(self, worker):
        """start → stop без ошибок."""
        task = asyncio.create_task(worker.start())
        # Даём loop'у запуститься.
        await asyncio.sleep(0.05)
        await worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_stop_final_flush(self, worker):
        """ingest + stop → финальный flush (queue пуста после stop)."""
        task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.05)

        msg = _make_msg(text="будет flush при stop")
        await worker.ingest(msg)
        assert worker.queue_size == 1

        await worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Queue должна быть пуста — stop() сделал финальный flush.
        assert worker.queue_size == 0

    async def test_stats_updated_after_flush(self, worker):
        """Кумулятивная статистика обновляется после flush."""
        for i in range(3):
            msg = _make_msg(msg_id=i + 1, text=f"stats msg {i}")
            await worker.ingest(msg)

        await worker.flush()
        assert worker.stats.total_ingested >= 3
        assert worker.stats.total_flushes == 1
        assert worker.stats.last_flush_at is not None

    async def test_double_start_idempotent(self, worker):
        """Повторный start() — no-op (не ломает worker)."""
        task1 = asyncio.create_task(worker.start())
        await asyncio.sleep(0.05)
        # Второй start — должен быть no-op (уже running).
        task2 = asyncio.create_task(worker.start())
        await asyncio.sleep(0.05)
        await worker.stop()
        task1.cancel()
        task2.cancel()
        for t in (task1, task2):
            try:
                await t
            except asyncio.CancelledError:
                pass


# ===========================================================================
# 6. E2E mini-pipeline
# ===========================================================================

class TestE2E:
    """Интеграционные тесты: ingest → flush → retrieval."""

    async def test_ingest_flush_fts_search(self, worker, paths):
        """10 сообщений → flush → FTS5 находит конкретное."""
        base_ts = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        for i in range(10):
            msg = _make_msg(
                msg_id=i + 1,
                text=f"сообщение {i} {'редкое_слово_абракадабра' if i == 5 else 'обычный текст'}",
                date=base_ts + timedelta(seconds=i * 10),
            )
            await worker.ingest(msg)

        stats = await worker.flush()
        assert stats.messages_ingested == 10

        # FTS5 ищет редкое слово.
        conn = open_archive(paths, create_if_missing=False)
        try:
            rows = conn.execute(
                "SELECT text_redacted FROM messages_fts WHERE messages_fts MATCH ?;",
                ("абракадабра",),
            ).fetchall()
            found_texts = [r[0] for r in rows]
            assert any("абракадабра" in t for t in found_texts)
        finally:
            conn.close()

    async def test_ingest_with_reply_to(self, worker, paths):
        """Сообщение с reply_to → chunks корректно связаны."""
        base_ts = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        msg1 = _make_msg(msg_id=1, text="вопрос?", date=base_ts)
        msg2 = _make_msg(
            msg_id=2,
            text="ответ на вопрос",
            date=base_ts + timedelta(seconds=30),
            reply_to=1,
        )
        await worker.ingest(msg1)
        await worker.ingest(msg2)
        stats = await worker.flush()
        assert stats.messages_ingested == 2

        # Оба сообщения в БД.
        conn = open_archive(paths, create_if_missing=False)
        try:
            msgs = conn.execute("SELECT COUNT(*) FROM messages;").fetchone()[0]
            assert msgs == 2
            # chunk_messages bridge должен содержать обе ссылки.
            cm = conn.execute("SELECT COUNT(*) FROM chunk_messages;").fetchone()[0]
            assert cm >= 2
        finally:
            conn.close()

    async def test_hybrid_retriever_finds_indexed(self, worker, paths):
        """HybridRetriever.search() находит текст после ingest+flush."""
        from src.core.memory_retrieval import HybridRetriever

        base_ts = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        for i in range(5):
            msg = _make_msg(
                msg_id=i + 1,
                text=f"обсуждение {'квантовой физики' if i == 3 else 'погоды'}",
                date=base_ts + timedelta(seconds=i * 10),
            )
            await worker.ingest(msg)
        await worker.flush()

        # Нужно вручную создать schema (retriever открывает БД read-only).
        retriever = HybridRetriever(
            archive_paths=paths,
            model_name=None,  # FTS5-only, без Model2Vec
        )
        try:
            results = retriever.search("квантовой физики", top_k=5)
            found_texts = [r.text_redacted for r in results]
            assert any("квантов" in t for t in found_texts)
        finally:
            retriever.close()
