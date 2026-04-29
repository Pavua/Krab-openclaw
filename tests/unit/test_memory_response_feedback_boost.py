"""Тесты Feature A — Successful Response Retrieval Boost.

Проверяем:
  1. positive_count повышает rrf_score → ranking сдвигается.
  2. negative_count понижает rrf_score → ranking падает.
  3. Чанк без feedback'а: multiplier == 1.0, порядок не меняется.
  4. Отсутствие таблицы response_feedback → graceful fetch=={}.
  5. Idempotence формулы compute_feedback_multiplier.
  6. Schema sanity: create_schema создаёт таблицу + индекс, record/fetch round-trip.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from src.core.memory_archive import (
    ArchivePaths,
    create_schema,
    ensure_response_feedback_table,
    fetch_response_feedback_for_chunks,
    list_tables,
    open_archive,
    record_response_feedback,
)
from src.core.memory_hybrid_reranker import (
    RESPONSE_FEEDBACK_MIN_MULTIPLIER,
    RESPONSE_FEEDBACK_NEGATIVE_COEF,
    RESPONSE_FEEDBACK_POSITIVE_COEF,
    SearchResult,
    apply_response_feedback_boost,
    compute_feedback_multiplier,
)

# ---------------------------------------------------------------------------
# Чистые формулы.
# ---------------------------------------------------------------------------


def test_compute_multiplier_no_feedback_is_identity():
    # Без feedback'а множитель == 1.0 (no-op для не помеченных чанков).
    assert compute_feedback_multiplier(0, 0) == pytest.approx(1.0)


def test_compute_multiplier_positive_boosts_logarithmic():
    # Положительный feedback даёт log-боост по формуле 1 + log(1+pos)*coef.
    expected = 1.0 + math.log(1.0 + 5) * RESPONSE_FEEDBACK_POSITIVE_COEF
    assert compute_feedback_multiplier(5, 0) == pytest.approx(expected)
    # Монотонность — больше positive → больше multiplier.
    assert compute_feedback_multiplier(10, 0) > compute_feedback_multiplier(5, 0)


def test_compute_multiplier_negative_clamped_at_min():
    # 100 negatives → линейный penalty загнан в floor MIN_MULTIPLIER.
    multiplier = compute_feedback_multiplier(0, 100)
    assert multiplier == pytest.approx(RESPONSE_FEEDBACK_MIN_MULTIPLIER)
    # Один negative — penalty = 1 - 0.2 = 0.8.
    expected = 1.0 - 1 * RESPONSE_FEEDBACK_NEGATIVE_COEF
    assert compute_feedback_multiplier(0, 1) == pytest.approx(expected)


def test_compute_multiplier_idempotent_and_negative_inputs_clamped():
    # Идемпотентность — повторный вызов возвращает то же значение.
    a = compute_feedback_multiplier(3, 2)
    b = compute_feedback_multiplier(3, 2)
    assert a == b
    # Отрицательные входы (защита от деградации БД) обнуляются.
    assert compute_feedback_multiplier(-1, -1) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# apply_response_feedback_boost — ranking shift.
# ---------------------------------------------------------------------------


def test_apply_boost_promotes_positive_feedback_chunk():
    # Чанк A с rrf_score=0.05 + 5 positives обходит чанк B с 0.06 без feedback'а.
    a = SearchResult(chunk_id="A", rrf_score=0.05)
    b = SearchResult(chunk_id="B", rrf_score=0.06)
    out = apply_response_feedback_boost([b, a], {"A": (5, 0)})
    assert out[0].chunk_id == "A"
    assert out[1].chunk_id == "B"


def test_apply_boost_no_feedback_unchanged():
    # Пустой feedback_map — порядок не меняется, scores не трогаются.
    a = SearchResult(chunk_id="A", rrf_score=0.05)
    b = SearchResult(chunk_id="B", rrf_score=0.06)
    out = apply_response_feedback_boost([b, a], {})
    assert [r.chunk_id for r in out] == ["B", "A"]
    assert a.rrf_score == pytest.approx(0.05)
    assert b.rrf_score == pytest.approx(0.06)


def test_apply_boost_negative_demotes_chunk():
    # Negative feedback на топовый чанк — он опускается под второго.
    a = SearchResult(chunk_id="A", rrf_score=0.10)
    b = SearchResult(chunk_id="B", rrf_score=0.06)
    out = apply_response_feedback_boost([a, b], {"A": (0, 3)})
    assert out[0].chunk_id == "B"
    assert out[1].chunk_id == "A"


# ---------------------------------------------------------------------------
# Schema + storage round-trip.
# ---------------------------------------------------------------------------


def test_response_feedback_schema_round_trip(tmp_path: Path):
    paths = ArchivePaths.under(tmp_path)
    conn = open_archive(paths=paths)
    create_schema(conn)
    try:
        tables = list_tables(conn)
        assert "response_feedback" in tables

        # Минимальный chat/message/chunk pipeline для JOIN.
        conn.execute("INSERT INTO chats(chat_id, title) VALUES (?, ?);", ("c1", "test"))
        conn.execute(
            """
            INSERT INTO messages(message_id, chat_id, sender_id, timestamp, text_redacted)
            VALUES (?, ?, ?, ?, ?);
            """,
            ("m1", "c1", "krab", "2026-04-28T00:00:00Z", "hi"),
        )
        conn.execute(
            """
            INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                               message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            ("ch1", "c1", "2026-04-28T00:00:00Z", "2026-04-28T00:00:00Z", 1, 2, "hi"),
        )
        conn.execute(
            """
            INSERT INTO chunk_messages(chunk_id, message_id, chat_id)
            VALUES (?, ?, ?);
            """,
            ("ch1", "m1", "c1"),
        )
        conn.commit()

        # UPSERT positive_delta=2, потом negative_delta=1 — round-trip aggregation.
        assert record_response_feedback(conn, "c1", "m1", positive_delta=2)
        assert record_response_feedback(conn, "c1", "m1", negative_delta=1)
        fb = fetch_response_feedback_for_chunks(conn, ["ch1"])
        assert fb == {"ch1": (2, 1)}
    finally:
        conn.close()


def test_fetch_response_feedback_missing_table_graceful():
    # БД совсем без схемы — fetch возвращает {} вместо exception (default-safe).
    conn = sqlite3.connect(":memory:")
    try:
        result = fetch_response_feedback_for_chunks(conn, ["ch1"])
        assert result == {}
    finally:
        conn.close()


def test_ensure_response_feedback_table_idempotent():
    # ensure_* можно вызвать на чистой in-memory БД и повторно — без ошибок.
    conn = sqlite3.connect(":memory:")
    try:
        assert ensure_response_feedback_table(conn) is True
        assert ensure_response_feedback_table(conn) is True
        # После создания запись/чтение работают, но без chunk_messages JOIN
        # вернёт {} — это OK, мы только проверяем что таблица доступна.
        record_response_feedback(conn, "c", "m", positive_delta=1)
        row = conn.execute(
            "SELECT positive_count FROM response_feedback WHERE chat_id='c';"
        ).fetchone()
        assert row[0] == 1
    finally:
        conn.close()
