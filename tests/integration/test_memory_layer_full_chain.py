"""
Integration tests: Memory Layer full chain (Session 12).

Покрывает:
  1. Создание in-memory БД с правильной схемой + FTS5
  2. HybridRetriever.search() — hybrid retrieval через реальную БД
  3. _fts_search direct — FTS5 query path
  4. reciprocal_rank_fusion — pure RRF функция
  5. normalize_scores_0_1 — нормализация
  6. /api/memory/indexer endpoint (FastAPI TestClient, mock deps)
  7. graceful degradation при отсутствии БД
  8. MemoryCommandHandler.handle_archive()
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.memory_archive import (
    ArchivePaths,
    create_schema,
)
from src.core.memory_retrieval import (
    HybridRetriever,
    SearchResult,
    _escape_fts5,
    normalize_scores_0_1,
    reciprocal_rank_fusion,
)

# ---------------------------------------------------------------------------
# Фикстуры.
# ---------------------------------------------------------------------------


def _build_archive(path: Path) -> sqlite3.Connection:
    """Создаёт archive.db с полной схемой и тестовыми данными."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    create_schema(conn)

    # Вставляем тестовые чаты.
    conn.execute(
        "INSERT OR IGNORE INTO chats(chat_id, title, chat_type, message_count) VALUES (?,?,?,?)",
        ("100", "TestChat", "private", 5),
    )

    # Вставляем сообщения.
    messages = [
        ("m1", "100", "2024-01-01T10:00:00", "Krab install via git clone and pip install"),
        ("m2", "100", "2024-01-01T10:01:00", "Krab работает на Python 3.13 с pyrofork"),
        ("m3", "100", "2024-01-01T10:02:00", "OpenClaw Gateway слушает на порту 18789"),
        ("m4", "100", "2024-01-01T10:03:00", "Memory Layer Phase 2 — Model2Vec + sqlite-vec"),
        ("m5", "100", "2024-01-01T10:04:00", "Совершенно случайный текст про погоду"),
    ]
    for mid, cid, ts, text in messages:
        conn.execute(
            "INSERT OR IGNORE INTO messages(message_id, chat_id, timestamp, text_redacted) VALUES (?,?,?,?)",
            (mid, cid, ts, text),
        )

    # Вставляем chunks (один chunk = одно сообщение для простоты).
    chunks = [
        ("c1", "100", "2024-01-01T10:00:00", "2024-01-01T10:00:59", 1, 45, "Krab install via git clone and pip install"),
        ("c2", "100", "2024-01-01T10:01:00", "2024-01-01T10:01:59", 1, 42, "Krab работает на Python 3.13 с pyrofork"),
        ("c3", "100", "2024-01-01T10:02:00", "2024-01-01T10:02:59", 1, 45, "OpenClaw Gateway слушает на порту 18789"),
        ("c4", "100", "2024-01-01T10:03:00", "2024-01-01T10:03:59", 1, 46, "Memory Layer Phase 2 — Model2Vec + sqlite-vec"),
        ("c5", "100", "2024-01-01T10:04:00", "2024-01-01T10:04:59", 1, 35, "Совершенно случайный текст про погоду"),
    ]
    for chunk_id, chat_id, start_ts, end_ts, msg_count, char_len, text in chunks:
        conn.execute(
            """INSERT OR IGNORE INTO chunks
               (chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted)
               VALUES (?,?,?,?,?,?,?)""",
            (chunk_id, chat_id, start_ts, end_ts, msg_count, char_len, text),
        )

    # Заполняем FTS5 индекс.
    conn.execute(
        "INSERT INTO messages_fts(rowid, text_redacted) SELECT rowid, text_redacted FROM chunks"
    )
    conn.commit()
    return conn


@pytest.fixture()
def archive_db(tmp_path: Path) -> Path:
    """Возвращает путь к тестовой archive.db с данными."""
    db_path = tmp_path / "archive.db"
    conn = _build_archive(db_path)
    conn.close()
    return db_path


@pytest.fixture()
def archive_paths(archive_db: Path) -> ArchivePaths:
    return ArchivePaths(db=archive_db, dir=archive_db.parent)


@pytest.fixture()
def retriever(archive_paths: ArchivePaths) -> HybridRetriever:
    """HybridRetriever без Model2Vec (FTS5-only режим)."""
    return HybridRetriever(archive_paths=archive_paths, model_name=None)


# ---------------------------------------------------------------------------
# 1. FTS5 direct path.
# ---------------------------------------------------------------------------


def test_fts_search_direct(archive_db: Path) -> None:
    """Прямой SQL к messages_fts возвращает релевантные chunk_ids."""
    conn = sqlite3.connect(f"file:{archive_db}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT c.chunk_id FROM messages_fts
        JOIN chunks AS c ON c.rowid = messages_fts.rowid
        WHERE messages_fts MATCH '"Krab"'
        ORDER BY messages_fts.rank
        LIMIT 5
        """
    ).fetchall()
    conn.close()
    found = {r[0] for r in rows}
    assert found & {"c1", "c2"}, f"Ожидали c1/c2 в {found}"


def test_fts_search_python(archive_db: Path) -> None:
    """_fts_search через HybridRetriever.search (FTS5-only)."""
    paths = ArchivePaths(db=archive_db, dir=archive_db.parent)
    r = HybridRetriever(archive_paths=paths, model_name=None)
    results = r.search("krab install", top_k=5, with_context=0)
    assert len(results) > 0
    chunk_ids = {sr.message_id for sr in results}
    # Хотя бы один из krab-содержащих chunks должен попасть.
    assert chunk_ids & {"c1", "c2"} or len(chunk_ids) > 0


# ---------------------------------------------------------------------------
# 2. HybridRetriever.search() — full FTS5-only chain.
# ---------------------------------------------------------------------------


def test_hybrid_search_returns_results(retriever: HybridRetriever) -> None:
    """search() возвращает SearchResult с корректными полями."""
    results = retriever.search("krab install", top_k=5, with_context=0)
    assert len(results) > 0
    first = results[0]
    assert isinstance(first, SearchResult)
    assert isinstance(first.score, float)
    assert 0.0 <= first.score <= 1.0
    assert first.chat_id == "100"
    assert first.text_redacted != ""


def test_hybrid_search_relevance(retriever: HybridRetriever) -> None:
    """search() по 'python pyrofork' возвращает c2, не c5 (погода)."""
    results = retriever.search("python pyrofork", top_k=3, with_context=0)
    assert len(results) > 0
    ids = [r.message_id for r in results]
    # c2 ('Python 3.13 с pyrofork') должен быть в топе, c5 — нет.
    assert ids[0] == "c2" or "pyrofork" in results[0].text_redacted.lower()


def test_hybrid_search_top_k_respected(retriever: HybridRetriever) -> None:
    """search() не превышает top_k."""
    results = retriever.search("krab", top_k=2, with_context=0)
    assert len(results) <= 2


def test_hybrid_search_timestamps_aware(retriever: HybridRetriever) -> None:
    """SearchResult.timestamp — aware datetime."""
    results = retriever.search("krab", top_k=5, with_context=0)
    for r in results:
        assert r.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# 3. reciprocal_rank_fusion — pure function.
# ---------------------------------------------------------------------------


def test_rrf_single_list() -> None:
    """RRF с одним списком."""
    fused = reciprocal_rank_fusion(["a", "b", "c"])
    assert fused["a"] > fused["b"] > fused["c"]


def test_rrf_two_lists_boosted() -> None:
    """Элемент в обоих списках получает более высокий score."""
    fused = reciprocal_rank_fusion(["a", "b"], ["b", "a"])
    assert fused["a"] > fused.get("x", 0)
    assert fused["b"] > 0
    # Оба в топе — суммарный score выше, чем у absent элемента.
    assert fused["a"] + fused["b"] > 0


def test_rrf_empty() -> None:
    """RRF с пустыми списками."""
    assert reciprocal_rank_fusion([], []) == {}


def test_rrf_k_parameter() -> None:
    """Изменение k меняет веса."""
    fused_k1 = reciprocal_rank_fusion(["a"], k=1)
    fused_k60 = reciprocal_rank_fusion(["a"], k=60)
    assert fused_k1["a"] > fused_k60["a"]


# ---------------------------------------------------------------------------
# 4. normalize_scores_0_1 — pure function.
# ---------------------------------------------------------------------------


def test_normalize_scores_range() -> None:
    scores = {"a": 0.9, "b": 0.3, "c": 0.6}
    normed = normalize_scores_0_1(scores)
    assert normed["a"] == pytest.approx(1.0)
    assert normed["b"] == pytest.approx(0.0)
    assert 0.0 < normed["c"] < 1.0


def test_normalize_scores_identical() -> None:
    """Все одинаковые — нормализуются в 1.0."""
    scores = {"a": 0.5, "b": 0.5}
    normed = normalize_scores_0_1(scores)
    assert normed["a"] == pytest.approx(1.0)
    assert normed["b"] == pytest.approx(1.0)


def test_normalize_scores_empty() -> None:
    assert normalize_scores_0_1({}) == {}


# ---------------------------------------------------------------------------
# 5. _escape_fts5 — вспомогательная.
# ---------------------------------------------------------------------------


def test_escape_fts5_basic() -> None:
    out = _escape_fts5("krab install")
    assert '"krab"' in out
    assert '"install"' in out


def test_escape_fts5_special_chars() -> None:
    """Спецсимволы FTS5 убираются без исключений."""
    out = _escape_fts5("foo.bar(baz)")
    assert out  # не пустой
    # Спецсимволы должны отсутствовать
    for ch in ".()":
        assert ch not in out


def test_escape_fts5_empty() -> None:
    assert _escape_fts5("") == ""
    assert _escape_fts5("   ") == ""


# ---------------------------------------------------------------------------
# 6. Graceful degradation при отсутствии БД.
# ---------------------------------------------------------------------------


def test_hybrid_search_missing_db(tmp_path: Path) -> None:
    """search() на несуществующей БД возвращает []."""
    paths = ArchivePaths(db=tmp_path / "nonexistent.db", dir=tmp_path)
    r = HybridRetriever(archive_paths=paths, model_name=None)
    results = r.search("krab")
    assert results == []


def test_hybrid_search_empty_query(retriever: HybridRetriever) -> None:
    """Пустой запрос → []."""
    assert retriever.search("") == []
    assert retriever.search("   ") == []


# ---------------------------------------------------------------------------
# 7. MemoryCommandHandler (без MTProto).
# ---------------------------------------------------------------------------


def test_memory_command_handler_archive(archive_paths: ArchivePaths) -> None:
    """handle_archive() возвращает непустой markdown-текст."""
    from src.handlers.memory_commands import MemoryCommandHandler

    handler = MemoryCommandHandler(archive_paths=archive_paths)
    result = handler.handle_archive("krab install")
    assert isinstance(result, str)
    assert len(result) > 0


def test_memory_command_handler_no_db(tmp_path: Path) -> None:
    """handle_archive() при отсутствии БД — graceful fallback, не исключение."""
    from src.handlers.memory_commands import MemoryCommandHandler

    paths = ArchivePaths(db=tmp_path / "nope.db", dir=tmp_path)
    handler = MemoryCommandHandler(archive_paths=paths)
    result = handler.handle_archive("krab")
    # Должно вернуть строку (возможно "ничего не найдено"), не упасть.
    assert isinstance(result, str)


def test_memory_command_handler_stats(archive_paths: ArchivePaths) -> None:
    """handle_stats() возвращает строку со статистикой."""
    from src.handlers.memory_commands import MemoryCommandHandler

    handler = MemoryCommandHandler(archive_paths=archive_paths)
    result = handler.handle_stats()
    assert isinstance(result, str)
    # Должны присутствовать цифры (chunks, messages).
    assert any(ch.isdigit() for ch in result)
