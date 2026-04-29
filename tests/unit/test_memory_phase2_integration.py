"""
Integration-тесты Memory Phase 2 (C8).

End-to-end проверка гибридного retrieval'а поверх реальной схемы
`memory_archive.create_schema()` + `memory_embedder.create_vec_table()`
с инъекцией детерминированной fake Model2Vec-модели.

Покрытие (C8 deliverable):
  * `test_phase2_hybrid_retrieval_returns_vec_and_fts_hits` — flag=1, vec+fts;
  * `test_phase2_per_chat_filter_isolates_results` — chat_id фильтр на vec пути;
  * `test_phase2_disabled_flag_falls_back_to_fts` — flag=0 ≡ FTS-only;
  * `test_phase2_deterministic_ordering` — одинаковый query → идентичный order;
  * `test_phase2_empty_db_no_crash` — pristine schema без chunks → [] без crash.

Self-contained: всё на tmp_path + реальной sqlite3 (НЕ in-memory `:memory:`, т.к.
`create_vec_table()` грузит `sqlite_vec` и хочет полноценный файловый URI).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_embedder import DEFAULT_DIM, MemoryEmbedder
from src.core.memory_retrieval import HybridRetriever

# ---------------------------------------------------------------------------
# Детерминированная fake Model2Vec (inspired от test_memory_embedder.py).
# ---------------------------------------------------------------------------


class _FakeEmbedModel:
    """
    Fake Model2Vec: `encode(texts)` → numpy array (N, dim), seed = sum(ord(c)).

    Свойство "похожие тексты → близкие векторы" НЕ гарантируется (seed разный
    при любом отличии), поэтому "релевантность" в тестах проверяется через
    `query == chunk.text` — т.е. vec hits должны содержать именно тот chunk,
    чей текст равен query. Для top-K KNN это достаточное условие.
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
# Seed-хелперы.
# ---------------------------------------------------------------------------


def _seed_chunk_row(
    conn: sqlite3.Connection,
    chat_id: str,
    chunk_id: str,
    text: str,
    ts: str,
) -> int:
    """Вставляет один chunk + message + FTS5 row. Возвращает rowid."""
    conn.execute(
        "INSERT OR IGNORE INTO chats(chat_id, title, chat_type) VALUES (?, ?, ?);",
        (chat_id, f"chat {chat_id}", "private"),
    )
    msg_id = f"msg_{chunk_id}"
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
    return rowid


def _build_phase2_archive(tmp_path: Path, n_chunks: int = 100) -> ArchivePaths:
    """
    Полный e2e setup:
      1. Чистая archive.db + create_schema().
      2. Seed'им `n_chunks` chunks в chat "-100aaa" (+ 10 в "-100bbb" для
         per-chat теста).
      3. MemoryEmbedder с fake моделью → embed_all_unindexed() заполняет
         `vec_chunks` и `vec_chunks_meta`.
    """
    paths = ArchivePaths.under(tmp_path / "phase2_int")
    conn = open_archive(paths)
    create_schema(conn)

    # Chat A — большинство chunks, темы: dashboard / memory / embedding.
    for i in range(n_chunks):
        topic = ("dashboard", "memory", "embedding", "retrieval", "frontend")[i % 5]
        text = f"chunk {i:03d} about {topic} with unique marker alpha{i}"
        ts = f"2026-04-{(i % 28) + 1:02d}T10:{i % 60:02d}:00Z"
        _seed_chunk_row(conn, "-100aaa", f"a{i:03d}", text, ts)

    # Chat B — изолированный набор для per-chat теста.
    for i in range(10):
        text = f"isolated bravo marker beta{i} weather astronomy"
        ts = f"2026-04-{(i % 28) + 1:02d}T09:{i % 60:02d}:00Z"
        _seed_chunk_row(conn, "-100bbb", f"b{i:03d}", text, ts)

    conn.commit()
    conn.close()

    # Эмбеддим всё через реальный embedder + fake модель.
    embedder = MemoryEmbedder(
        archive_paths=paths,
        model_name="fake/test-model",
        dim=DEFAULT_DIM,
        _model=_FakeEmbedModel(dim=DEFAULT_DIM),
        batch_size=64,
    )
    try:
        stats = embedder.embed_all_unindexed()
        assert stats.chunks_processed == n_chunks + 10, (
            f"expected {n_chunks + 10} embeddings, got {stats.chunks_processed}"
        )
    finally:
        embedder.close()

    return paths


def _make_retriever(paths: ArchivePaths) -> HybridRetriever:
    """HybridRetriever с инъекцией fake модели (минуем HF download)."""
    r = HybridRetriever(
        archive_paths=paths,
        model_name="fake/test-model",
        model_dim=DEFAULT_DIM,
    )
    # Инжектим модель напрямую — _ensure_model() вернёт cached instance.
    r._model = _FakeEmbedModel(dim=DEFAULT_DIM)
    return r


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def phase2_archive(tmp_path: Path) -> ArchivePaths:
    """Архив с 100 chunks в chat A + 10 в chat B + embeddings."""
    return _build_phase2_archive(tmp_path, n_chunks=100)


@pytest.fixture
def pristine_archive(tmp_path: Path) -> ArchivePaths:
    """Пустая схема без chunks — для empty_db smoke-теста."""
    paths = ArchivePaths.under(tmp_path / "pristine")
    conn = open_archive(paths)
    create_schema(conn)
    conn.close()
    # vec_chunks создадим, но пустым.
    from src.core.memory_embedder import create_vec_table

    conn = open_archive(paths)
    create_vec_table(conn, dim=DEFAULT_DIM)
    conn.close()
    return paths


# ---------------------------------------------------------------------------
# Тесты.
# ---------------------------------------------------------------------------


class TestMemoryPhase2Integration:
    """End-to-end: real schema + fake Model2Vec + HybridRetriever."""

    def test_phase2_hybrid_retrieval_returns_vec_and_fts_hits(
        self,
        phase2_archive: ArchivePaths,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        Flag=1: search() должен вернуть результаты (>0) и mode=hybrid/vec.

        Проверяем через внутренние счётчики _vector_search и _fts_search,
        что оба пути отработали.
        """
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")
        monkeypatch.delenv("MEMORY_ADAPTIVE_RERANK_ENABLED", raising=False)
        monkeypatch.delenv("KRAB_RAG_LLM_RERANK_ENABLED", raising=False)

        r = _make_retriever(phase2_archive)

        # Spy на обе фазы — считаем вызовы и захватываем length.
        vec_calls: dict[str, int] = {"hits": 0}
        fts_calls: dict[str, int] = {"hits": 0}
        real_vec = r._vector_search
        real_fts = r._fts_search

        def spy_vec(conn, q, cid, limit):  # noqa: ANN001
            ids = real_vec(conn, q, cid, limit)
            vec_calls["hits"] = len(ids)
            return ids

        def spy_fts(conn, q, cid, limit):  # noqa: ANN001
            ids = real_fts(conn, q, cid, limit)
            fts_calls["hits"] = max(fts_calls["hits"], len(ids))
            return ids

        monkeypatch.setattr(r, "_vector_search", spy_vec)
        monkeypatch.setattr(r, "_fts_search", spy_fts)

        try:
            results = r.search("dashboard", top_k=10, with_context=0)
        finally:
            r.close()

        assert len(results) > 0, "hybrid search должен вернуть результаты"
        assert fts_calls["hits"] > 0, "FTS5 должен найти matching chunks по 'dashboard'"
        # vec hits > 0 требует sqlite-vec загруженного; в CI без extension
        # отрабатывает FTS-only, но на dev-машине с sqlite_vec — hybrid.
        # Проверяем XOR: либо vec сработал, либо явно отключился (no extension).
        assert r._vec_available or vec_calls["hits"] == 0
        if r._vec_available:
            assert vec_calls["hits"] > 0, (
                "vec path должен вернуть hits при enabled + loaded extension"
            )

    def test_phase2_per_chat_filter_isolates_results(
        self,
        phase2_archive: ArchivePaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """chat_id='-100bbb' → только chunks b*, не a*."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")
        r = _make_retriever(phase2_archive)
        try:
            results_b = r.search(
                "isolated bravo marker",
                chat_id="-100bbb",
                top_k=10,
                with_context=0,
            )
            results_a = r.search(
                "dashboard",
                chat_id="-100aaa",
                top_k=10,
                with_context=0,
            )
        finally:
            r.close()

        assert len(results_b) > 0
        assert all(res.chat_id == "-100bbb" for res in results_b)
        assert len(results_a) > 0
        assert all(res.chat_id == "-100aaa" for res in results_a)
        # Ни один b-результат не должен быть в a-ответе (и наоборот).
        b_ids = {r.message_id for r in results_b}
        a_ids = {r.message_id for r in results_a}
        assert b_ids.isdisjoint(a_ids), "per-chat filter должен полностью изолировать"

    def test_phase2_disabled_flag_falls_back_to_fts(
        self,
        phase2_archive: ArchivePaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Flag=0: vec-путь отключён, но FTS всё ещё работает.

        Важно: ИМЕННО FTS-only, а НЕ пусто. BM25 найдёт chunks по keyword'у.
        """
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "0")
        r = _make_retriever(phase2_archive)

        vec_calls: dict[str, int] = {"hits": 0, "n_calls": 0}
        real_vec = r._vector_search

        def spy_vec(conn, q, cid, limit):  # noqa: ANN001
            vec_calls["n_calls"] += 1
            ids = real_vec(conn, q, cid, limit)
            vec_calls["hits"] += len(ids)
            return ids

        monkeypatch.setattr(r, "_vector_search", spy_vec)

        try:
            results = r.search("dashboard", top_k=10, with_context=0)
        finally:
            r.close()

        assert len(results) > 0, "FTS должен найти chunks про 'dashboard'"
        # vec_search либо вообще не вызывается (vec_available=False), либо
        # возвращает [] (early-return по flag).
        assert vec_calls["hits"] == 0, "Flag=0 → vec path не должен отдавать hits"

    def test_phase2_deterministic_ordering(
        self,
        phase2_archive: ArchivePaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Тот же query трижды подряд → идентичный порядок message_id.

        Детерминированность fused RRF + decay + stable sort → репродуцируемо.
        """
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")
        # Фиксируем "сейчас" — иначе decay будет зависеть от clock-time между
        # прогонами (разные age_days).
        from datetime import datetime, timezone

        fixed_now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

        def make_r() -> HybridRetriever:
            r = HybridRetriever(
                archive_paths=phase2_archive,
                model_name="fake/test-model",
                model_dim=DEFAULT_DIM,
                now=lambda: fixed_now,
            )
            r._model = _FakeEmbedModel(dim=DEFAULT_DIM)
            return r

        orders: list[list[str]] = []
        for _ in range(3):
            r = make_r()
            try:
                results = r.search("memory embedding alpha", top_k=5, with_context=0)
            finally:
                r.close()
            orders.append([res.message_id for res in results])

        assert orders[0] == orders[1] == orders[2], f"ordering не детерминирован: {orders}"
        assert len(orders[0]) > 0

    def test_phase2_empty_db_no_crash(
        self,
        pristine_archive: ArchivePaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pristine schema без chunks → search() возвращает [], не падает."""
        monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "1")
        r = _make_retriever(pristine_archive)
        try:
            results = r.search("anything at all", top_k=10, with_context=0)
        finally:
            r.close()
        assert results == []
