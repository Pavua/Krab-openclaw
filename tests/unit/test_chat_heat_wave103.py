# -*- coding: utf-8 -*-
"""Wave 103: тесты chat heat scoring + observability."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core import chat_heat_score as chs

# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_archive(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Создаёт минимальный archive.db schema + вставляет rows.

    rows: list of (chat_id, sender_id, timestamp_iso, text).
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE chats (
                chat_id TEXT PRIMARY KEY,
                title TEXT,
                chat_type TEXT,
                last_indexed_at TEXT,
                message_count INTEGER NOT NULL DEFAULT 0
            ) WITHOUT ROWID;
            CREATE TABLE messages (
                message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                sender_id TEXT,
                timestamp TEXT NOT NULL,
                text_redacted TEXT NOT NULL,
                reply_to_id TEXT,
                PRIMARY KEY (chat_id, message_id)
            ) WITHOUT ROWID;
            """
        )
        seen_chats: set[str] = set()
        for i, (chat_id, sender_id, ts, text) in enumerate(rows):
            if chat_id not in seen_chats:
                conn.execute(
                    "INSERT INTO chats(chat_id, message_count) VALUES (?, ?)",
                    (chat_id, 0),
                )
                seen_chats.add(chat_id)
            conn.execute(
                "INSERT INTO messages(message_id, chat_id, sender_id, timestamp, text_redacted) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"m{i}", chat_id, sender_id, ts, text),
            )
        # Update message_count
        for cid in seen_chats:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ?", (cid,)
            ).fetchone()[0]
            conn.execute("UPDATE chats SET message_count = ? WHERE chat_id = ?", (cnt, cid))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clear_cache():
    chs.clear_cache()
    yield
    chs.clear_cache()


@pytest.fixture
def _patched_owner(monkeypatch):
    """Patch owner tokens."""
    monkeypatch.setattr(chs, "_owner_tokens", lambda: ({"111"}, {"owner_user"}))
    monkeypatch.setattr(chs, "_resolve_mode", lambda cid: "normal")


# ── Tests ───────────────────────────────────────────────────────────────────


def test_compute_returns_zero_when_db_missing(tmp_path, _patched_owner):
    """Wave 103: отсутствующая archive.db → score 0.0 без exception."""
    comp = chs.compute_chat_heat("123", db_path=tmp_path / "nope.db")
    assert comp.score == 0.0
    assert comp.mention_count_raw == 0
    assert comp.owner_messaged is False
    assert comp.member_count is None
    assert comp.mode == "normal"


def test_mention_factor_weighting(tmp_path, _patched_owner):
    """20 упоминаний 'краб' → mention_norm=1.0 → score ≈ 0.4."""
    db = tmp_path / "archive.db"
    now = datetime.now(timezone.utc)
    rows = [
        ("999", "2", (now - timedelta(minutes=10 * i)).isoformat(), "привет краб как дела")
        for i in range(20)
    ]
    _make_archive(db, rows)

    comp = chs.compute_chat_heat("999", db_path=db)
    assert comp.mention_count_raw == 20
    assert comp.mention_rate == pytest.approx(1.0)
    # 0.4 (mention) + 0.1 (member_inv, distinct=1 → score 1.0)
    assert 0.45 <= comp.score <= 0.55


def test_explicit_questions_weighting(tmp_path, _patched_owner):
    """30 вопросов → explicit_q_norm=1.0 → contribution 0.3."""
    db = tmp_path / "archive.db"
    now = datetime.now(timezone.utc)
    rows = [
        ("777", "2", (now - timedelta(minutes=5 * i)).isoformat(), f"что это {i}?")
        for i in range(30)
    ]
    _make_archive(db, rows)

    comp = chs.compute_chat_heat("777", db_path=db)
    assert comp.explicit_q_count_raw == 30
    assert comp.explicit_questions == pytest.approx(1.0)
    assert comp.score >= 0.3


def test_owner_engagement_binary(tmp_path, _patched_owner):
    """Owner писал → owner_engagement=1.0 → contribution 0.2."""
    db = tmp_path / "archive.db"
    now = datetime.now(timezone.utc).isoformat()
    rows = [("555", "111", now, "нейтральное сообщение")]
    _make_archive(db, rows)

    comp = chs.compute_chat_heat("555", db_path=db)
    assert comp.owner_messaged is True
    assert comp.owner_engagement == 1.0
    # 0.2 (owner) + 0.1 (member_inv от 1 sender)
    assert comp.score >= 0.2


def test_member_count_inverse(tmp_path, _patched_owner):
    """DM (1 sender) → member_inv=1.0; крупная группа → меньше."""
    db_dm = tmp_path / "dm.db"
    db_grp = tmp_path / "grp.db"
    now = datetime.now(timezone.utc)

    dm_rows = [("dm1", "555", now.isoformat(), "hello")]
    _make_archive(db_dm, dm_rows)

    grp_rows = [
        ("grp1", str(1000 + i), (now - timedelta(minutes=i)).isoformat(), "msg")
        for i in range(80)
    ]
    _make_archive(db_grp, grp_rows)

    dm = chs.compute_chat_heat("dm1", db_path=db_dm)
    grp = chs.compute_chat_heat("grp1", db_path=db_grp)

    assert dm.member_count_inverse == pytest.approx(1.0)
    assert grp.member_count_inverse < dm.member_count_inverse
    assert grp.member_count_inverse < 0.6


def test_cache_ttl_returns_same_object_within_window(tmp_path, _patched_owner):
    """Кэш отдаёт тот же результат в течение TTL без повторного query."""
    db = tmp_path / "archive.db"
    now = datetime.now(timezone.utc).isoformat()
    _make_archive(db, [("c1", "2", now, "краб привет")])

    first = chs.compute_chat_heat("c1", db_path=db)
    # Удалить файл — если кэш работает, второй вызов вернёт то же
    db.unlink()
    second = chs.compute_chat_heat("c1", db_path=db)
    assert first.score == second.score
    assert first.computed_at == second.computed_at

    # use_cache=False должен пересчитать и получить 0 (db нет)
    fresh = chs.compute_chat_heat("c1", db_path=db, use_cache=False)
    assert fresh.score == 0.0


def test_cache_expires_after_ttl(tmp_path, _patched_owner):
    """После TTL кэш не возвращает stale entry."""
    db = tmp_path / "archive.db"
    now = datetime.now(timezone.utc).isoformat()
    _make_archive(db, [("c2", "2", now, "краб")])

    t0 = time.time()
    first = chs.compute_chat_heat("c2", db_path=db, now=t0)
    assert first.score > 0
    # Через 6 минут (>TTL=5min) — пересчёт
    t1 = t0 + 6 * 60
    db.unlink()
    second = chs.compute_chat_heat("c2", db_path=db, now=t1)
    # db удалена, score = 0 — кэш протух
    assert second.score == 0.0


def test_top_chats_ranking_by_score(tmp_path, _patched_owner):
    """top_chats_by_heat сортирует по score desc."""
    db = tmp_path / "archive.db"
    now = datetime.now(timezone.utc)
    rows: list[tuple[str, str, str, str]] = []
    # cold chat: 1 нейтральное сообщение
    rows.append(("cold", "2", now.isoformat(), "просто текст"))
    # hot chat: 20 mention'ов + owner писал
    for i in range(20):
        rows.append(
            ("hot", "2", (now - timedelta(minutes=i)).isoformat(), "краб привет!")
        )
    rows.append(("hot", "111", now.isoformat(), "owner вот тут"))
    _make_archive(db, rows)

    top = chs.top_chats_by_heat(limit=10, db_path=db)
    assert len(top) >= 2
    assert top[0].chat_id == "hot"
    assert top[0].score > top[-1].score


def test_score_is_clamped_to_unit_interval(tmp_path, _patched_owner):
    """Score всегда в [0, 1] даже при экстремальных факторах."""
    db = tmp_path / "archive.db"
    now = datetime.now(timezone.utc)
    rows = [
        ("ex", "111", (now - timedelta(minutes=i)).isoformat(), "краб ? @owner_user")
        for i in range(100)
    ]
    _make_archive(db, rows)

    comp = chs.compute_chat_heat("ex", db_path=db)
    assert 0.0 <= comp.score <= 1.0


def test_metric_recorder_fail_safe():
    """record_chat_heat_score не падает при невалидных входах."""
    from src.core.metrics.chat_heat import record_chat_heat_score

    record_chat_heat_score("123", "normal", 0.5)
    record_chat_heat_score("123", "", 2.0)  # clamp до 1.0
    record_chat_heat_score("456", "silent", -1.0)  # clamp до 0.0
    # не должно бросать
