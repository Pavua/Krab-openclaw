"""
Unit-тесты Memory Phase 2 shadow-reads mode.

Покрывают контракт KRAB_RAG_PHASE2_SHADOW:
  * env=0 → shadow не вызывается (no background task);
  * env=1 → shadow scheduled, основной результат не меняется;
  * shadow raise → main search продолжает работать;
  * shadow пишет структурированный log с нужными полями.

Тесты намеренно не требуют sqlite-vec / model2vec — шедевр патчится
monkeypatch'ем через подмену `_shadow_compare` / `_vector_search`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.memory_archive import ArchivePaths, create_schema, open_archive
from src.core.memory_retrieval import HybridRetriever


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO chats(chat_id, title, chat_type) VALUES (?, ?, ?);",
        ("-100", "t", "private_supergroup"),
    )
    for i, text in enumerate(["alpha beta gamma", "beta gamma delta", "delta epsilon"]):
        cid = f"c{i}"
        mid = f"m{i}"
        ts = f"2026-04-01T10:0{i}:00Z"
        conn.execute(
            "INSERT INTO messages(message_id, chat_id, timestamp, text_redacted) "
            "VALUES (?, ?, ?, ?);",
            (mid, "-100", ts, text),
        )
        cur = conn.execute(
            "INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts, "
            "message_count, char_len, text_redacted) VALUES (?, ?, ?, ?, ?, ?, ?);",
            (cid, "-100", ts, ts, 1, len(text), text),
        )
        conn.execute(
            "INSERT INTO chunk_messages(chunk_id, message_id, chat_id) VALUES (?, ?, ?);",
            (cid, mid, "-100"),
        )
        conn.execute(
            "INSERT INTO messages_fts(rowid, text_redacted) VALUES (?, ?);",
            (cur.lastrowid, text),
        )
    conn.commit()


@pytest.fixture
def retriever(tmp_path: Path) -> HybridRetriever:
    paths = ArchivePaths.under(tmp_path / "mem")
    conn = open_archive(paths)
    create_schema(conn)
    _seed(conn)
    conn.close()
    r = HybridRetriever(archive_paths=paths, model_name=None)
    return r


def test_shadow_disabled_no_background_call(
    retriever: HybridRetriever,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_RAG_PHASE2_SHADOW unset / 0 → _schedule_shadow_compare не вызывается."""
    monkeypatch.delenv("KRAB_RAG_PHASE2_SHADOW", raising=False)
    monkeypatch.delenv("KRAB_RAG_PHASE2_ENABLED", raising=False)

    called: list[bool] = []
    monkeypatch.setattr(
        HybridRetriever,
        "_schedule_shadow_compare",
        lambda self, **kw: called.append(True),
    )

    results = retriever.search("beta", top_k=3)
    assert called == []
    # Main search должен всё же что-то вернуть (FTS-only работает).
    assert results, "FTS-only search должен возвращать hits для 'beta'"


def test_shadow_enabled_fires_and_forgets(
    retriever: HybridRetriever,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env=1 → _schedule_shadow_compare вызывается ровно 1 раз; results не меняются."""
    monkeypatch.setenv("KRAB_RAG_PHASE2_SHADOW", "1")
    monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "0")

    calls: list[dict] = []

    def fake_schedule(self: HybridRetriever, **kwargs: object) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr(HybridRetriever, "_schedule_shadow_compare", fake_schedule)

    results = retriever.search("beta", top_k=3)
    assert results, "main search должен работать независимо от shadow"
    assert len(calls) == 1
    kw = calls[0]
    # Контракт: shadow получает тот же query, chat_id (None здесь), fts_ids и top5.
    assert kw["query"] == "beta"
    assert kw["top_k"] == 3
    assert isinstance(kw["fts_ids"], list) and kw["fts_ids"]
    assert isinstance(kw["top5_fts"], list)
    assert kw["top5_fts"] == [r.message_id for r in results[:5]]


def test_shadow_failure_does_not_break_main_search(
    retriever: HybridRetriever,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_schedule_shadow_compare raise → main results целы, warning залогирован."""
    monkeypatch.setenv("KRAB_RAG_PHASE2_SHADOW", "1")
    monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "0")

    def boom(self: HybridRetriever, **kwargs: object) -> None:
        raise RuntimeError("shadow exploded")

    monkeypatch.setattr(HybridRetriever, "_schedule_shadow_compare", boom)

    # Не должно поднимать.
    results = retriever.search("beta", top_k=3)
    assert results, "main search не должен падать из-за shadow failure"


def test_shadow_logs_comparison_summary(
    retriever: HybridRetriever,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_shadow_compare() пишет structured log с ожидаемыми полями."""
    monkeypatch.setenv("KRAB_RAG_PHASE2_SHADOW", "1")
    monkeypatch.setenv("KRAB_RAG_PHASE2_ENABLED", "0")
    # MMR off — избегаем попытки загрузить model2vec из реального HF.
    monkeypatch.setenv("KRAB_RAG_MMR_ENABLED", "0")

    # Включаем vec_available, но подменяем _vector_search на контролируемый возврат.
    retriever._vec_available = True  # type: ignore[attr-defined]
    retriever._model_name = "dummy"  # type: ignore[attr-defined]

    monkeypatch.setattr(
        HybridRetriever,
        "_vector_search",
        lambda self, conn, query, chat_id, limit: ["c0", "c_new_from_vec"],
    )

    captured: list[dict] = []

    class _FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

        def warning(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

        def debug(self, *a: object, **k: object) -> None:  # noqa: D401
            pass

    monkeypatch.setattr("src.core.memory_retrieval.logger", _FakeLogger())

    results = retriever.search("beta", top_k=3)
    assert results

    compare_events = [e for e in captured if e.get("event") == "memory_phase2_shadow_compare"]
    assert compare_events, f"expected shadow_compare log, got: {captured}"
    ev = compare_events[0]
    # Структурированные поля по контракту задачи.
    for field_name in (
        "query_preview",
        "fts_hits",
        "vec_hits",
        "shadow_merged",
        "would_change_top5",
        "latency_fts_ms",
        "latency_vec_ms",
        "model_mode",
    ):
        assert field_name in ev, f"missing field {field_name} in {ev}"
    assert ev["model_mode"] == "shadow"
    assert ev["vec_hits"] == 2
    assert ev["query_preview"].startswith("beta")
