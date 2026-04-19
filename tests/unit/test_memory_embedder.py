"""
Unit-тесты Model2Vec embedder'а (Phase 2 вторая половина).

Покрывают:
  * ``create_vec_table`` — идемпотентна, sqlite-vec грузится, таблица живёт;
  * ``serialize_f32`` — numpy и list-пути, корректные размеры bytes;
  * ``EmbedStats`` — dataclass frozen, все поля присутствуют;
  * ``MemoryEmbedder.embed_all_unindexed()``:
      - на пустой БД → stats(0, 0, 0, ...);
      - с 3 chunks → vec_chunks заполнен, processed=3;
      - повторный запуск → skipped=3, processed=0 (идемпотентно);
  * ``MemoryEmbedder.rebuild_all()`` — пересоздаёт и переиндексирует;
  * ``MemoryEmbedder.embed_specific()`` — переиндексирует список chunk_ids;
  * smoke vec_chunks MATCH — возвращает ближайший результат;
  * integration smoke через fixture-level chunks → векторы реально пишутся.

Используется fake Model2Vec (FakeEmbedModel) — никогда не скачивает модель
с HuggingFace (слишком долго для unit-тестов).

Запуск::

    venv/bin/python -m pytest \
        tests/unit/test_memory_embedder.py --noconftest -q
"""

from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_embedder import (
    DEFAULT_DIM,
    EmbedStats,
    MemoryEmbedder,
    create_vec_table,
    serialize_f32,
)

# ---------------------------------------------------------------------------
# Фейковая модель (детерминированная).
# ---------------------------------------------------------------------------


class FakeEmbedModel:
    """
    Детерминированный fake Model2Vec для тестов.

    ``encode(texts)`` → numpy-массив shape (N, dim). Сид зависит от суммы
    кодов символов текста, что даёт воспроизводимые векторы без похода
    на HuggingFace.
    """

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def encode(self, texts):  # noqa: ANN001
        import numpy as np

        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            seed = (sum(ord(c) for c in t) + len(t)) % (10**6)
            rng = np.random.RandomState(seed)
            out[i] = rng.randn(self.dim).astype("float32")
        return out


# ---------------------------------------------------------------------------
# Хелперы.
# ---------------------------------------------------------------------------


def _make_archive(tmp_path: Path) -> tuple[ArchivePaths, sqlite3.Connection]:
    """Создать чистую archive.db с применённой схемой."""
    paths = ArchivePaths.under(tmp_path / "mem")
    conn = open_archive(paths)
    create_schema(conn)
    return paths, conn


def _seed_chunks(conn: sqlite3.Connection, count: int, chat_id: str = "-100111") -> None:
    """Воткнуть N chunks + messages + FTS5 row для smoke-тестов."""
    conn.execute(
        "INSERT OR IGNORE INTO chats(chat_id, title, chat_type) VALUES (?, ?, ?);",
        (chat_id, f"chat {chat_id}", "private"),
    )
    for i in range(count):
        chunk_id = f"chunk_{i:03d}"
        text = f"sample text number {i} about dashboard and memory embedding"
        ts = f"2026-04-0{(i % 9) + 1}T10:0{i % 10}:00Z"
        msg_id = f"msg_{i}"

        conn.execute(
            """
            INSERT INTO messages(message_id, chat_id, timestamp, text_redacted)
            VALUES (?, ?, ?, ?);
            """,
            (msg_id, chat_id, ts, text),
        )
        cur = conn.execute(
            """
            INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                               message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (chunk_id, chat_id, ts, ts, 1, len(text), text),
        )
        rowid = cur.lastrowid
        conn.execute(
            "INSERT INTO chunk_messages(chunk_id, message_id, chat_id) VALUES (?, ?, ?);",
            (chunk_id, msg_id, chat_id),
        )
        conn.execute(
            "INSERT INTO messages_fts(rowid, text_redacted) VALUES (?, ?);",
            (rowid, text),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# serialize_f32.
# ---------------------------------------------------------------------------


class TestSerializeF32:
    def test_list_small(self) -> None:
        vec = [1.0, 2.0, 3.0, 4.0]
        out = serialize_f32(vec)
        assert isinstance(out, bytes)
        # 4 float32 = 16 байт.
        assert len(out) == 4 * 4

    def test_numpy_256(self) -> None:
        import numpy as np

        vec = np.random.randn(256).astype("float32")
        out = serialize_f32(vec)
        # 256 * 4 = 1024 байт.
        assert len(out) == 256 * 4
        # Round-trip: распакуем обратно и сравним.
        import struct

        unpacked = struct.unpack("<256f", out)
        for a, b in zip(unpacked, vec.tolist()):
            assert abs(a - b) < 1e-5

    def test_numpy_float64_casts_to_float32(self) -> None:
        import numpy as np

        vec = np.array([1.0, 2.0, 3.0, 4.0], dtype="float64")
        out = serialize_f32(vec)
        # После преобразования в float32 размер всё равно 4*4=16.
        assert len(out) == 16

    def test_list_short_length(self) -> None:
        vec = [1.5, -2.5, 3.5]
        out = serialize_f32(vec)
        assert len(out) == 3 * 4


# ---------------------------------------------------------------------------
# create_vec_table.
# ---------------------------------------------------------------------------


class TestCreateVecTable:
    def test_creates_virtual_table(self) -> None:
        conn = sqlite3.connect(":memory:")
        create_vec_table(conn, dim=4)
        # Таблица должна появиться в sqlite_master.
        row = conn.execute("SELECT name FROM sqlite_master WHERE name='vec_chunks';").fetchone()
        assert row is not None
        assert row[0] == "vec_chunks"

    def test_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        create_vec_table(conn, dim=4)
        # Повторный вызов не падает.
        create_vec_table(conn, dim=4)
        create_vec_table(conn, dim=4)
        # Таблица всё ещё существует.
        row = conn.execute("SELECT name FROM sqlite_master WHERE name='vec_chunks';").fetchone()
        assert row is not None

    def test_insert_vector(self) -> None:
        conn = sqlite3.connect(":memory:")
        create_vec_table(conn, dim=4)
        vec = serialize_f32([1.0, 0.0, 0.0, 0.0])
        conn.execute("INSERT INTO vec_chunks(rowid, vector) VALUES (?, ?);", (1, vec))
        conn.commit()
        cnt = conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]
        assert cnt == 1


# ---------------------------------------------------------------------------
# EmbedStats dataclass.
# ---------------------------------------------------------------------------


class TestEmbedStats:
    def test_fields(self) -> None:
        field_names = {f.name for f in fields(EmbedStats)}
        assert field_names == {
            "chunks_processed",
            "chunks_skipped",
            "batches",
            "duration_sec",
            "model_load_sec",
        }

    def test_frozen(self) -> None:
        s = EmbedStats(
            chunks_processed=0,
            chunks_skipped=0,
            batches=0,
            duration_sec=0.0,
            model_load_sec=0.0,
        )
        with pytest.raises(FrozenInstanceError):
            s.chunks_processed = 5  # type: ignore[misc]

    def test_roundtrip(self) -> None:
        s = EmbedStats(
            chunks_processed=3,
            chunks_skipped=2,
            batches=1,
            duration_sec=0.123,
            model_load_sec=0.0,
        )
        assert s.chunks_processed == 3
        assert s.chunks_skipped == 2
        assert s.batches == 1


# ---------------------------------------------------------------------------
# MemoryEmbedder.embed_all_unindexed.
# ---------------------------------------------------------------------------


class TestEmbedAllUnindexed:
    def test_empty_db(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        conn.close()

        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            stats = emb.embed_all_unindexed()
        finally:
            emb.close()

        assert stats.chunks_processed == 0
        assert stats.chunks_skipped == 0
        assert stats.batches == 0
        assert stats.model_load_sec == 0.0  # инжектированная модель

    def test_three_chunks_processed(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        _seed_chunks(conn, count=3)
        conn.close()

        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            stats = emb.embed_all_unindexed()

            # 3 chunks обработаны.
            assert stats.chunks_processed == 3
            assert stats.chunks_skipped == 0
            assert stats.batches == 1

            # vec_chunks должен содержать 3 записи.
            cnt = emb._conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]  # noqa: SLF001
            assert cnt == 3
        finally:
            emb.close()

    def test_idempotent_second_run(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        _seed_chunks(conn, count=3)
        conn.close()

        # Первый прогон — всё обрабатываем.
        emb1 = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            stats1 = emb1.embed_all_unindexed()
            assert stats1.chunks_processed == 3
        finally:
            emb1.close()

        # Второй прогон — ни одного нового chunk нет.
        emb2 = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            stats2 = emb2.embed_all_unindexed()
            assert stats2.chunks_processed == 0
            assert stats2.chunks_skipped == 3
            assert stats2.batches == 0
        finally:
            emb2.close()

    def test_batch_boundary(self, tmp_path: Path) -> None:
        """batch_size=2 на 5 chunks → 3 batch'а (2+2+1)."""
        paths, conn = _make_archive(tmp_path)
        _seed_chunks(conn, count=5)
        conn.close()

        emb = MemoryEmbedder(archive_paths=paths, batch_size=2, _model=FakeEmbedModel())
        try:
            stats = emb.embed_all_unindexed()
            assert stats.chunks_processed == 5
            assert stats.batches == 3
        finally:
            emb.close()


# ---------------------------------------------------------------------------
# MemoryEmbedder.rebuild_all.
# ---------------------------------------------------------------------------


class TestRebuildAll:
    def test_drops_and_rebuilds(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        _seed_chunks(conn, count=4)
        conn.close()

        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            # Первый embed.
            s1 = emb.embed_all_unindexed()
            assert s1.chunks_processed == 4

            # rebuild_all: DROP и переиндексация.
            s2 = emb.rebuild_all()
            assert s2.chunks_processed == 4
            # После rebuild skipped должен быть 0: vec_chunks пустая после DROP.
            assert s2.chunks_skipped == 0

            # Число векторов в таблице не должно удвоиться.
            cnt = emb._conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]  # noqa: SLF001
            assert cnt == 4
        finally:
            emb.close()


# ---------------------------------------------------------------------------
# MemoryEmbedder.embed_specific.
# ---------------------------------------------------------------------------


class TestEmbedSpecific:
    def test_reindex_single(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        _seed_chunks(conn, count=3)
        conn.close()

        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            emb.embed_all_unindexed()
            # Re-index одного chunk'а. Старый вектор должен быть заменён.
            stats = emb.embed_specific(["chunk_001"])
            assert stats.chunks_processed == 1
            assert stats.chunks_skipped == 0

            # Общее число векторов всё ещё 3 (не дублируется).
            cnt = emb._conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]  # noqa: SLF001
            assert cnt == 3
        finally:
            emb.close()

    def test_missing_chunk_id_skipped(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        _seed_chunks(conn, count=2)
        conn.close()

        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            emb.embed_all_unindexed()
            # Несуществующий chunk_id → skipped=1, processed=0.
            stats = emb.embed_specific(["chunk_000", "chunk_DOESNT_EXIST"])
            # chunk_000 существует — будет processed; второй — skipped.
            assert stats.chunks_processed == 1
            assert stats.chunks_skipped == 1
        finally:
            emb.close()

    def test_empty_ids(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        conn.close()
        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            stats = emb.embed_specific([])
            assert stats.chunks_processed == 0
            assert stats.batches == 0
        finally:
            emb.close()


# ---------------------------------------------------------------------------
# Vector search smoke.
# ---------------------------------------------------------------------------


class TestVectorSearchSmoke:
    def test_vec_match_returns_nearest(self, tmp_path: Path) -> None:
        """
        После embed_all_unindexed sqlite-vec MATCH должен вернуть
        ровно тот rowid, чей вектор ближе всего к query-вектору.
        """
        paths, conn = _make_archive(tmp_path)
        _seed_chunks(conn, count=5)
        conn.close()

        fake = FakeEmbedModel()
        emb = MemoryEmbedder(archive_paths=paths, _model=fake)
        try:
            emb.embed_all_unindexed()

            # Берём вектор первого chunk'а как query — он же должен быть nearest.
            row = emb._conn.execute(  # noqa: SLF001
                "SELECT id, text_redacted FROM chunks ORDER BY id LIMIT 1;"
            ).fetchone()
            target_id, target_text = row[0], row[1]
            q_vec = fake.encode([target_text])[0]
            q_blob = serialize_f32(q_vec)

            rows = emb._conn.execute(  # noqa: SLF001
                "SELECT rowid, distance FROM vec_chunks "
                "WHERE vector MATCH ? ORDER BY distance LIMIT 3;",
                (q_blob,),
            ).fetchall()

            assert len(rows) >= 1
            # Nearest должен быть сам target.
            nearest_rowid = rows[0][0]
            assert nearest_rowid == target_id
            # Distance для самого себя должен быть ~0.
            assert rows[0][1] < 1e-4
        finally:
            emb.close()


# ---------------------------------------------------------------------------
# Integration fixture smoke.
# ---------------------------------------------------------------------------


class TestIntegrationSmoke:
    def test_full_pipeline_schema_to_embedding(self, tmp_path: Path) -> None:
        """
        End-to-end: создать БД → применить схему → вставить chunks →
        прогнать MemoryEmbedder → SELECT COUNT(*) FROM vec_chunks > 0.
        Эмулирует то, что делает bootstrap_memory + воркер.
        """
        paths = ArchivePaths.under(tmp_path / "archive")
        conn = open_archive(paths)
        create_schema(conn)
        _seed_chunks(conn, count=7, chat_id="-100777")
        conn.close()

        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        try:
            stats = emb.embed_all_unindexed()
            assert stats.chunks_processed == 7

            # Проверяем что vec_chunks заполнен.
            cnt = emb._conn.execute("SELECT COUNT(*) FROM vec_chunks;").fetchone()[0]  # noqa: SLF001
            assert cnt == 7

            # LEFT JOIN должен теперь возвращать 0 chunks без вектора.
            unindexed = emb._conn.execute(  # noqa: SLF001
                """
                SELECT COUNT(*) FROM chunks c
                LEFT JOIN vec_chunks v ON v.rowid = c.id
                WHERE v.rowid IS NULL;
                """
            ).fetchone()[0]
            assert unindexed == 0
        finally:
            emb.close()

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        paths, conn = _make_archive(tmp_path)
        conn.close()
        emb = MemoryEmbedder(archive_paths=paths, _model=FakeEmbedModel())
        emb.embed_all_unindexed()
        emb.close()
        # Повторный close не падает.
        emb.close()
