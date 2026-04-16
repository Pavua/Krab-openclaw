"""
Unit-тесты MemoryIndexerWorker (Phase 4).

Покрывают:
  - enqueue side (whitelist, overflow, empty text)

Запуск:
    venv/bin/python -m pytest tests/unit/test_memory_indexer_worker.py -q
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_indexer_worker import (
    DEFAULT_BATCH_SIZE,
    IndexerStats,
    MemoryIndexerWorker,
    QueuedMessage,
)
from src.core.memory_whitelist import MemoryWhitelist, WhitelistConfig


BASE_TIME = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
ALLOWED_CHAT_ID = "111"
ALLOWED_CHAT_TITLE = "allowed_chat"
DENIED_CHAT_ID = "222"
DENIED_CHAT_TITLE = "denied_chat"


def _fake_pyrofork_message(
    *,
    chat_id: str = ALLOWED_CHAT_ID,
    chat_title: str = ALLOWED_CHAT_TITLE,
    chat_type: str = "private",
    message_id: str = "1",
    sender_id: str | int | None = "999",
    text: str = "hello",
    offset_sec: int = 0,
    reply_to: str | None = None,
) -> SimpleNamespace:
    chat = SimpleNamespace(
        id=int(chat_id),
        title=chat_title,
        type=SimpleNamespace(value=chat_type),
    )
    from_user = SimpleNamespace(
        id=int(sender_id) if sender_id is not None else None,
        is_bot=False,
    )
    return SimpleNamespace(
        chat=chat,
        from_user=from_user,
        id=int(message_id),
        text=text,
        date=BASE_TIME + timedelta(seconds=offset_sec),
        reply_to_message_id=int(reply_to) if reply_to else None,
    )


@pytest.fixture
def temp_archive(tmp_path: Path) -> ArchivePaths:
    paths = ArchivePaths.under(tmp_path)
    conn = open_archive(paths)
    create_schema(conn)
    conn.close()
    return paths


@pytest.fixture
def whitelist_strict() -> MemoryWhitelist:
    config = WhitelistConfig(allow_ids={ALLOWED_CHAT_ID})
    return MemoryWhitelist(config=config)


@pytest.fixture
def worker(temp_archive: ArchivePaths, whitelist_strict: MemoryWhitelist) -> MemoryIndexerWorker:
    return MemoryIndexerWorker(
        archive_paths=temp_archive,
        whitelist=whitelist_strict,
        embedder=None,
        queue_maxsize=100,
        batch_size=5,
        batch_timeout_sec=0.5,
    )


class TestEnqueue:
    """Tests #1-4: producer-side enqueue + whitelist + overflow + empty text."""

    @pytest.mark.asyncio
    async def test_enqueue_respects_whitelist_deny(self, worker: MemoryIndexerWorker) -> None:
        await worker.start()
        try:
            msg = _fake_pyrofork_message(chat_id=DENIED_CHAT_ID, chat_title=DENIED_CHAT_TITLE)
            accepted = await worker.enqueue(msg)
            assert accepted is False
            stats = worker.get_stats()
            assert stats.skipped.get("whitelist", 0) == 1
            assert stats.queue_size == 0
        finally:
            await worker.stop(drain=False)

    @pytest.mark.asyncio
    async def test_enqueue_respects_whitelist_allow(self, worker: MemoryIndexerWorker) -> None:
        await worker.start()
        try:
            msg = _fake_pyrofork_message(chat_id=ALLOWED_CHAT_ID)
            accepted = await worker.enqueue(msg)
            assert accepted is True
            stats = worker.get_stats()
            assert stats.enqueued_total == 1
        finally:
            await worker.stop(drain=False)

    @pytest.mark.asyncio
    async def test_enqueue_queue_overflow_drops(
        self,
        temp_archive: ArchivePaths,
        whitelist_strict: MemoryWhitelist,
    ) -> None:
        small_worker = MemoryIndexerWorker(
            archive_paths=temp_archive,
            whitelist=whitelist_strict,
            queue_maxsize=2,
            batch_size=10,
        )
        small_worker._queue = asyncio.Queue(maxsize=2)
        for i in range(2):
            msg = _fake_pyrofork_message(message_id=str(i))
            assert await small_worker.enqueue(msg) is True
        msg_overflow = _fake_pyrofork_message(message_id="99")
        assert await small_worker.enqueue(msg_overflow) is False
        assert small_worker.get_stats().dropped_queue_full == 1

    @pytest.mark.asyncio
    async def test_enqueue_skips_empty_text(self, worker: MemoryIndexerWorker) -> None:
        await worker.start()
        try:
            msg = _fake_pyrofork_message(text="")
            accepted = await worker.enqueue(msg)
            assert accepted is False
            stats = worker.get_stats()
            assert stats.skipped.get("empty_text", 0) == 1
        finally:
            await worker.stop(drain=False)
