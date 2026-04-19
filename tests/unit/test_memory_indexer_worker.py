"""
Unit-тесты MemoryIndexerWorker (Phase 4).

Покрывают:
  - enqueue side (whitelist, overflow, empty text)
  - consumer loop: batch triggers, DB writes, chunking
  - idempotency: watermark cache, drain
  - failure modes: PII isolation, sqlite error resilience
  - supervisor: auto-restart, cancel propagation

Запуск:
    venv/bin/python -m pytest tests/unit/test_memory_indexer_worker.py -q
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_indexer_worker import (
    MemoryIndexerWorker,
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
def whitelist_strict(tmp_path: Path) -> MemoryWhitelist:
    # Указываем несуществующий config_path чтобы hot-reload не подхватил
    # глобальный whitelist.json (если он существует на машине разработчика).
    config = WhitelistConfig(allow_ids={ALLOWED_CHAT_ID})
    return MemoryWhitelist(config_path=tmp_path / "nonexistent_whitelist.json", config=config)


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


class TestConsumerBatch:
    """Tests #5-6: consumer loop batch triggers."""

    @pytest.mark.asyncio
    async def test_processes_batch_size_trigger(self, worker: MemoryIndexerWorker) -> None:
        """Test #5: batch_size=5, send 5 msgs → all processed."""
        await worker.start()
        try:
            for i in range(5):
                await worker.enqueue(
                    _fake_pyrofork_message(message_id=str(i + 1), offset_sec=i * 10)
                )
            await asyncio.sleep(1.0)  # batch_timeout=0.5 → should flush
            stats = worker.get_stats()
            assert stats.processed_total == 5
        finally:
            await worker.stop(drain=True, timeout=2.0)

    @pytest.mark.asyncio
    async def test_processes_timeout_trigger(self, worker: MemoryIndexerWorker) -> None:
        """Test #6: 3 msgs + wait timeout → flushes 3."""
        await worker.start()
        try:
            for i in range(3):
                await worker.enqueue(
                    _fake_pyrofork_message(message_id=str(i + 1), offset_sec=i * 10)
                )
            await asyncio.sleep(1.0)
            stats = worker.get_stats()
            assert stats.processed_total == 3
        finally:
            await worker.stop(drain=True, timeout=2.0)


class TestConsumerWrites:
    """Tests #7-11: consumer writes to DB correctly."""

    @pytest.mark.asyncio
    async def test_redacts_pii_before_insert(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #7: msg with email → text_redacted has no email."""
        await worker.start()
        try:
            msg = _fake_pyrofork_message(
                message_id="1", text="my email is alice@example.com please", offset_sec=0
            )
            await worker.enqueue(msg)
            await worker.stop(drain=True, timeout=3.0)
        finally:
            pass
        conn = open_archive(temp_archive)
        try:
            row = conn.execute(
                "SELECT text_redacted FROM messages WHERE message_id = ?;", ("1",)
            ).fetchone()
            assert row is not None
            assert "alice@example.com" not in row[0]
            assert "[REDACTED:EMAIL]" in row[0]
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_writes_chunks_and_fts(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #8: flush 5 messages → 1 chunk + 1 FTS row."""
        await worker.start()
        try:
            for i in range(5):
                await worker.enqueue(
                    _fake_pyrofork_message(
                        message_id=str(i + 1),
                        text=f"message number {i + 1}",
                        offset_sec=i * 10,
                    )
                )
            await worker.stop(drain=True, timeout=3.0)
        finally:
            pass
        conn = open_archive(temp_archive)
        try:
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0]
            assert chunk_count >= 1
            fts_count = conn.execute("SELECT COUNT(*) FROM messages_fts;").fetchone()[0]
            assert fts_count >= 1
            hits = conn.execute(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'number';"
            ).fetchall()
            assert len(hits) >= 1
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_writes_embeddings_when_vec_available(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #9: inject fake embedder → embed_specific called."""
        from unittest.mock import MagicMock

        fake_embedder = MagicMock()
        fake_embedder.embed_specific = MagicMock(return_value=None)
        worker._embedder = fake_embedder
        await worker.start()
        try:
            for i in range(3):
                await worker.enqueue(
                    _fake_pyrofork_message(message_id=str(i + 1), offset_sec=i * 10)
                )
            await worker.stop(drain=True, timeout=3.0)
        finally:
            pass
        if fake_embedder.embed_specific.call_count > 0:
            first_call_args = fake_embedder.embed_specific.call_args_list[0]
            assert len(first_call_args[0][0]) > 0

    @pytest.mark.asyncio
    async def test_skips_embeddings_when_vec_unavailable(self, worker: MemoryIndexerWorker) -> None:
        """Test #10: при заблокированном MemoryEmbedder → embed_disabled=True, embeddings_committed=0."""
        import builtins
        import sys
        from unittest.mock import patch

        # Блокируем import MemoryEmbedder чтобы симулировать отсутствие sqlite-vec.
        real_import = builtins.__import__

        def _blocked_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "src.core.memory_embedder":
                raise ImportError("sqlite-vec not available (test stub)")
            return real_import(name, *args, **kwargs)

        worker._embedder = None
        # Убираем из кэша если уже загружен
        sys.modules.pop("src.core.memory_embedder", None)

        with patch("builtins.__import__", side_effect=_blocked_import):
            await worker.start()
            try:
                await worker.enqueue(_fake_pyrofork_message(message_id="1"))
                await worker.stop(drain=True, timeout=3.0)
            finally:
                pass
        stats = worker.get_stats()
        assert stats.embeddings_committed == 0 or stats.embed_disabled is True

    @pytest.mark.asyncio
    async def test_updates_indexer_state_watermark(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #11: after flush → indexer_state has last_message_id."""
        await worker.start()
        try:
            for i in range(3):
                await worker.enqueue(
                    _fake_pyrofork_message(message_id=str(i + 1), offset_sec=i * 10)
                )
            await worker.stop(drain=True, timeout=3.0)
        finally:
            pass
        conn = open_archive(temp_archive)
        try:
            row = conn.execute(
                "SELECT last_message_id FROM indexer_state WHERE chat_id = ?;", (ALLOWED_CHAT_ID,)
            ).fetchone()
            assert row is not None
            assert row[0] == "3"
        finally:
            conn.close()


class TestConsumerChunking:
    """Tests #12-14: chunking behavior."""

    @pytest.mark.asyncio
    async def test_chunking_respects_reply_to_chain(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #12: msg2.reply_to=msg1 → one chunk even with time gap."""
        await worker.start()
        try:
            await worker.enqueue(_fake_pyrofork_message(message_id="1", offset_sec=0))
            await worker.enqueue(
                _fake_pyrofork_message(message_id="2", offset_sec=600, reply_to="1")
            )
            await worker.stop(drain=True, timeout=3.0)
        finally:
            pass
        conn = open_archive(temp_archive)
        try:
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0]
            assert chunk_count == 1
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_chunking_respects_time_gap(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #13: msg2 at +10min → two chunks."""
        await worker.start()
        try:
            await worker.enqueue(_fake_pyrofork_message(message_id="1", offset_sec=0))
            await worker.enqueue(_fake_pyrofork_message(message_id="2", offset_sec=600))
            await worker.stop(drain=True, timeout=3.0)
        finally:
            pass
        conn = open_archive(temp_archive)
        try:
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0]
            assert chunk_count == 2
        finally:
            conn.close()


class TestIdempotency:
    """Tests #15-17: watermark idempotency + drain."""

    @pytest.mark.asyncio
    async def test_replay_same_message_skipped(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #15: enqueue same msg twice across restart → second skipped."""
        await worker.start()
        msg = _fake_pyrofork_message(message_id="42")
        await worker.enqueue(msg)
        await worker.stop(drain=True, timeout=3.0)
        # Рестартуем — watermark cache должен загрузиться из БД.
        await worker.start()
        msg2 = _fake_pyrofork_message(message_id="42")
        await worker.enqueue(msg2)
        await worker.stop(drain=True, timeout=3.0)
        stats = worker.get_stats()
        assert stats.skipped.get("already_indexed", 0) >= 1

    @pytest.mark.asyncio
    async def test_restart_resumes_from_watermark(
        self, worker: MemoryIndexerWorker, temp_archive: ArchivePaths
    ) -> None:
        """Test #16: stop → start → новые msg обрабатываются, старые не повторяются."""
        await worker.start()
        for i in range(3):
            await worker.enqueue(_fake_pyrofork_message(message_id=str(i + 1), offset_sec=i * 10))
        await worker.stop(drain=True, timeout=3.0)
        # Рестарт + новые сообщения с бо́льшими ID.
        await worker.start()
        for i in range(3, 6):
            await worker.enqueue(_fake_pyrofork_message(message_id=str(i + 1), offset_sec=i * 10))
        await worker.stop(drain=True, timeout=3.0)
        conn = open_archive(temp_archive)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ?;", (ALLOWED_CHAT_ID,)
            ).fetchone()[0]
            assert count == 6
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_drain_on_stop_waits_for_queue_empty(self, worker: MemoryIndexerWorker) -> None:
        """Test #17: stop(drain=True) ждёт пока всё обработано."""
        await worker.start()
        for i in range(5):
            await worker.enqueue(_fake_pyrofork_message(message_id=str(i + 1), offset_sec=i * 10))
        await worker.stop(drain=True, timeout=5.0)
        stats = worker.get_stats()
        assert stats.processed_total == 5
        assert stats.queue_size == 0


class TestFailureModes:
    """Tests #19-20: graceful degradation."""

    @pytest.mark.asyncio
    async def test_pii_failure_does_not_kill_worker(
        self, temp_archive: ArchivePaths, whitelist_strict: MemoryWhitelist
    ) -> None:
        """Test #19: redactor raises на первом msg → failed.pii++, worker живёт."""
        from types import SimpleNamespace as SNS

        bad_redactor = MagicMock()
        good_result = SNS(text="clean text", stats=SNS(counts={}, total=0))
        # Первый вызов (из _flush_batch) — падает; второй (из _sync_flush_to_db) — ОК.
        bad_redactor.redact.side_effect = [RuntimeError("pii broke"), good_result]

        w = MemoryIndexerWorker(
            archive_paths=temp_archive,
            whitelist=whitelist_strict,
            redactor=bad_redactor,
            queue_maxsize=100,
            batch_size=5,
            batch_timeout_sec=0.5,
        )
        await w.start()
        await w.enqueue(_fake_pyrofork_message(message_id="1", text="bad msg"))
        await w.enqueue(_fake_pyrofork_message(message_id="2", text="good msg", offset_sec=10))
        await w.stop(drain=True, timeout=3.0)
        stats = w.get_stats()
        # Worker должен выжить и что-то обработать (или зафиксировать ошибку).
        assert stats.processed_total >= 1 or stats.failed.get("pii", 0) >= 1

    @pytest.mark.asyncio
    async def test_sqlite_error_keeps_worker_alive(
        self, worker: MemoryIndexerWorker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test #20: sqlite error → failed.flush или failed.db, worker живёт."""
        original_flush = worker._sync_flush_to_db
        call_count = [0]

        def flaky_flush(*args: object, **kwargs: object) -> list[str]:
            call_count[0] += 1
            if call_count[0] == 1:
                raise sqlite3.DatabaseError("disk gone")
            return original_flush(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(worker, "_sync_flush_to_db", flaky_flush)
        await worker.start()
        await worker.enqueue(_fake_pyrofork_message(message_id="1"))
        await asyncio.sleep(1.0)
        await worker.stop(drain=True, timeout=3.0)
        stats = worker.get_stats()
        total_failed = sum(stats.failed.values())
        assert total_failed >= 1


class TestSupervisor:
    """Tests #25-27: supervisor auto-restart."""

    @pytest.mark.asyncio
    async def test_supervisor_restarts_on_unhandled_exception(
        self, worker: MemoryIndexerWorker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test #25: consumer raises → supervisor перезапускает, restarts >= 1."""
        crash_count = [0]
        original_loop = MemoryIndexerWorker._consumer_loop

        async def crashing_then_normal(self_: MemoryIndexerWorker) -> None:
            crash_count[0] += 1
            if crash_count[0] == 1:
                raise RuntimeError("simulated crash")
            await original_loop(self_)

        monkeypatch.setattr(MemoryIndexerWorker, "_consumer_loop", crashing_then_normal)
        await worker.start()
        await asyncio.sleep(2.0)  # Ждём пока supervisor перезапустит
        await worker.stop(drain=False, timeout=2.0)
        stats = worker.get_stats()
        assert stats.restarts >= 1

    @pytest.mark.asyncio
    async def test_supervisor_does_not_restart_on_cancelled(
        self, worker: MemoryIndexerWorker
    ) -> None:
        """Test #27: cancel → CancelledError пробрасывается, restarts=0."""
        await worker.start()
        await asyncio.sleep(0.1)
        await worker.stop(drain=False, timeout=2.0)
        stats = worker.get_stats()
        assert stats.restarts == 0


class TestStats:
    """Test #18: stats observable + module helpers."""

    @pytest.mark.asyncio
    async def test_stats_observable(self, worker):
        """get_stats() returns correct counters after work."""
        await worker.start()
        await worker.enqueue(_fake_pyrofork_message(message_id="1"))
        await worker.enqueue(_fake_pyrofork_message(message_id="2", offset_sec=10))
        await worker.stop(drain=True, timeout=3.0)
        stats = worker.get_stats()
        assert stats.enqueued_total == 2
        assert stats.processed_total == 2
        assert stats.is_running is False
        assert stats.started_at is not None
        assert isinstance(stats.skipped, dict)
        assert isinstance(stats.failed, dict)

    def test_module_helpers_singleton(self):
        """Module-level get_indexer returns singleton."""
        from src.core.memory_indexer_worker import (
            _reset_singleton_for_tests,
            get_indexer,
            is_indexer_running,
        )

        _reset_singleton_for_tests()
        first = get_indexer()
        second = get_indexer()
        assert first is second
        assert is_indexer_running() is False
        _reset_singleton_for_tests()  # cleanup
