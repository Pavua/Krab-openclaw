"""
Unit-тесты hybrid re-ranker (RRF combiner + FTS/semantic paths).

Покрывают:
  * rrf_combine на разных комбинациях входов (оба источника / один / пустота);
  * SearchResult dataclass (default sources=[], rrf_score=0.0);
  * hybrid_search при отсутствии БД → [];
  * hybrid_search с пустым query → [];
  * _escape_fts5 убирает служебные символы;
  * _fts_search end-to-end на in-memory schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_hybrid_reranker import (
    RRF_K,
    SearchResult,
    _escape_fts5,
    _fts_search,
    hybrid_search,
    rrf_combine,
)

# ---------------------------------------------------------------------------
# rrf_combine — чистая функция, тестируется без БД.
# ---------------------------------------------------------------------------


def test_rrf_combine_both_sources_top_chunk_is_shared():
    """Chunk присутствующий в обоих списках должен получить максимальный RRF-score."""
    fts = [("a", 1.0), ("b", 0.9), ("c", 0.5)]
    sem = [("c", 0.95), ("a", 0.8), ("d", 0.6)]
    results = rrf_combine(fts, sem)
    ids = [r.chunk_id for r in results]
    # "a" — rank1 в FTS + rank2 в sem; "c" — rank3 в FTS + rank1 в sem.
    # "a" = 1/(60+1) + 1/(60+2), "c" = 1/(60+3) + 1/(60+1) — "a" чуть выше.
    assert ids[0] == "a"
    # Оба общих должны быть выше уникальных "b" и "d".
    assert set(ids[:2]) == {"a", "c"}


def test_rrf_combine_empty_semantic_returns_fts_only():
    """Без semantic — все результаты с sources=['fts']."""
    fts = [("a", 1.0), ("b", 0.9)]
    results = rrf_combine(fts, [])
    assert len(results) == 2
    assert all(r.sources == ["fts"] for r in results)
    assert results[0].fts_rank == 1.0
    assert results[0].semantic_score is None


def test_rrf_combine_empty_fts_returns_semantic_only():
    """Симметрично: без FTS — всё из semantic."""
    sem = [("x", 0.9), ("y", 0.7)]
    results = rrf_combine([], sem)
    assert len(results) == 2
    assert all(r.sources == ["semantic"] for r in results)
    assert results[0].semantic_score == 0.9
    assert results[0].fts_rank is None


def test_rrf_combine_empty_both_returns_empty():
    """Оба пустые → []."""
    assert rrf_combine([], []) == []


def test_rrf_combine_shared_chunk_has_both_sources():
    """Один chunk в обоих списках → sources == ['fts', 'semantic']."""
    results = rrf_combine([("a", 1.0)], [("a", 0.9)])
    assert len(results) == 1
    assert results[0].sources == ["fts", "semantic"]
    assert results[0].fts_rank == 1.0
    assert results[0].semantic_score == 0.9


def test_rrf_combine_custom_k_affects_score():
    """Чем выше k — тем сглаженнее разница между рангами."""
    fts = [("a", 1.0), ("b", 0.9)]
    res_k10 = rrf_combine(fts, [], k=10)
    res_k60 = rrf_combine(fts, [], k=60)
    # При меньшем k первое место имеет больший отрыв.
    diff_k10 = res_k10[0].rrf_score - res_k10[1].rrf_score
    diff_k60 = res_k60[0].rrf_score - res_k60[1].rrf_score
    assert diff_k10 > diff_k60


def test_rrf_combine_score_formula():
    """Проверяем точную формулу: 1/(k + rank)."""
    results = rrf_combine([("a", 1.0)], [], k=60)
    assert results[0].rrf_score == pytest.approx(1.0 / 61.0)


# ---------------------------------------------------------------------------
# SearchResult dataclass.
# ---------------------------------------------------------------------------


def test_search_result_defaults():
    """Default sources=[], rrf_score=0.0, fts_rank/semantic_score = None."""
    r = SearchResult(chunk_id="x")
    assert r.sources == []
    assert r.rrf_score == 0.0
    assert r.text == ""
    assert r.fts_rank is None
    assert r.semantic_score is None


def test_search_result_independent_sources_per_instance():
    """Каждый SearchResult имеет свою sources list (нет shared mutable default)."""
    a = SearchResult(chunk_id="a")
    b = SearchResult(chunk_id="b")
    a.sources.append("fts")
    assert b.sources == []


# ---------------------------------------------------------------------------
# hybrid_search — публичный API.
# ---------------------------------------------------------------------------


def test_hybrid_search_missing_db(monkeypatch, tmp_path: Path):
    """При отсутствии archive.db — безопасно возвращаем []."""
    missing = tmp_path / "nope.db"
    monkeypatch.setattr("src.core.memory_hybrid_reranker.ARCHIVE_DB", missing)
    assert hybrid_search("anything") == []


def test_hybrid_search_empty_query(monkeypatch, tmp_path: Path):
    """Пустой / whitespace query → []."""
    monkeypatch.setattr("src.core.memory_hybrid_reranker.ARCHIVE_DB", tmp_path / "any.db")
    assert hybrid_search("") == []
    assert hybrid_search("   ") == []


def test_hybrid_search_fts_only_fallback(monkeypatch, tmp_path: Path):
    """БД есть, schema есть, но vec_chunks нет → FTS-only path работает."""
    paths = ArchivePaths.under(tmp_path / "mem")
    conn = open_archive(paths)
    create_schema(conn)
    # Seed одного chunk'а с текстом.
    conn.execute(
        "INSERT OR IGNORE INTO chats(chat_id, title) VALUES (?, ?);",
        ("-1001", "test"),
    )
    cur = conn.execute(
        """
        INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                           message_count, char_len, text_redacted)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (
            "chunk_a",
            "-1001",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            1,
            22,
            "dashboard redesign spec",
        ),
    )
    conn.execute(
        "INSERT INTO messages_fts(rowid, text_redacted) VALUES (?, ?);",
        (cur.lastrowid, "dashboard redesign spec"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("src.core.memory_hybrid_reranker.ARCHIVE_DB", paths.db)
    results = hybrid_search("dashboard", limit=5)
    assert len(results) == 1
    assert results[0].chunk_id == "chunk_a"
    assert results[0].text == "dashboard redesign spec"
    assert "fts" in results[0].sources
    assert results[0].rrf_score > 0


def test_hybrid_search_no_matches_returns_empty(monkeypatch, tmp_path: Path):
    """FTS ничего не нашёл и vec unavailable → []."""
    paths = ArchivePaths.under(tmp_path / "mem")
    conn = open_archive(paths)
    create_schema(conn)
    conn.close()
    monkeypatch.setattr("src.core.memory_hybrid_reranker.ARCHIVE_DB", paths.db)
    assert hybrid_search("nothing-matches-this") == []


def test_hybrid_search_enriches_text(monkeypatch, tmp_path: Path):
    """Результаты обогащаются text_redacted из chunks."""
    paths = ArchivePaths.under(tmp_path / "mem")
    conn = open_archive(paths)
    create_schema(conn)
    conn.execute(
        "INSERT OR IGNORE INTO chats(chat_id, title) VALUES (?, ?);",
        ("-1002", "enrich"),
    )
    for i, text in enumerate(["alpha beta", "gamma beta"], start=1):
        cur = conn.execute(
            """
            INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                               message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (f"c{i}", "-1002", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 1, len(text), text),
        )
        conn.execute(
            "INSERT INTO messages_fts(rowid, text_redacted) VALUES (?, ?);",
            (cur.lastrowid, text),
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr("src.core.memory_hybrid_reranker.ARCHIVE_DB", paths.db)
    results = hybrid_search("beta", limit=5)
    assert {r.text for r in results} == {"alpha beta", "gamma beta"}
    assert all(r.text for r in results)


# ---------------------------------------------------------------------------
# _fts_search / _escape_fts5 — внутренние хелперы.
# ---------------------------------------------------------------------------


def test_escape_fts5_strips_operators():
    """Операторы FTS5 заменяются на пробелы, токены OR'ятся в кавычках."""
    out = _escape_fts5("dashboard AND (metrics OR layout)")
    # AND/OR исходной строки превратились в токены (заключены в кавычки).
    assert '"dashboard"' in out
    assert '"metrics"' in out
    assert " OR " in out
    # Скобки не должны просочиться.
    assert "(" not in out and ")" not in out


def test_escape_fts5_empty_after_cleanup():
    """Query из одних служебных символов → пустая строка."""
    assert _escape_fts5("!!!???") == ""
    assert _escape_fts5("") == ""


def test_fts_search_returns_empty_on_missing_table():
    """_fts_search корректно обрабатывает OperationalError (нет messages_fts)."""
    conn = sqlite3.connect(":memory:")
    # Нет messages_fts — OperationalError ожидается и глотается.
    assert _fts_search(conn, "query") == []
    conn.close()


def test_rrf_k_constant():
    """Проверяем дефолтное значение k."""
    assert RRF_K == 60
