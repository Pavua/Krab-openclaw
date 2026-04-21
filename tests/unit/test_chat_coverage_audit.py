"""Тесты для scripts/audit_chat_coverage.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_archive_db(path: Path, chats: list[tuple[str, str, int]]) -> None:
    """Создаёт минимальную archive.db с тестовыми данными.

    chats: список (chat_id, title, msg_count)
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE chats (
                chat_id         TEXT PRIMARY KEY,
                title           TEXT,
                chat_type       TEXT,
                last_indexed_at TEXT,
                message_count   INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE messages (
                message_id    TEXT NOT NULL,
                chat_id       TEXT NOT NULL,
                sender_id     TEXT,
                timestamp     TEXT NOT NULL,
                text_redacted TEXT NOT NULL,
                reply_to_id   TEXT,
                PRIMARY KEY (chat_id, message_id)
            );
            """
        )
        for chat_id, title, cnt in chats:
            conn.execute(
                "INSERT INTO chats(chat_id, title, message_count) VALUES(?, ?, ?)",
                (chat_id, title, cnt),
            )
            for i in range(cnt):
                conn.execute(
                    "INSERT INTO messages(message_id, chat_id, timestamp, text_redacted) "
                    "VALUES(?, ?, ?, ?)",
                    (f"{chat_id}-{i}", chat_id, f"2026-04-{10 + (i % 20):02d}T12:00:00Z", "hello"),
                )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# read_archive_chats
# ---------------------------------------------------------------------------


def test_read_archive_chats_returns_list(tmp_path: Path) -> None:
    """read_archive_chats возвращает список чатов с правильными полями."""
    from scripts.audit_chat_coverage import read_archive_chats

    db = tmp_path / "archive.db"
    _make_archive_db(db, [("c1", "Chat One", 5), ("c2", "Chat Two", 3)])

    rows = read_archive_chats(db)
    assert len(rows) == 2
    # Сортировка по убыванию count
    assert rows[0]["db_count"] == 5
    assert rows[0]["chat_id"] == "c1"
    assert rows[0]["title"] == "Chat One"
    assert rows[1]["db_count"] == 3


def test_read_archive_chats_missing_db(tmp_path: Path) -> None:
    """Если archive.db нет — возвращаем пустой список."""
    from scripts.audit_chat_coverage import read_archive_chats

    rows = read_archive_chats(tmp_path / "does_not_exist.db")
    assert rows == []


def test_read_archive_chats_empty_db(tmp_path: Path) -> None:
    """Пустая DB без сообщений."""
    from scripts.audit_chat_coverage import read_archive_chats

    db = tmp_path / "archive.db"
    _make_archive_db(db, [])

    rows = read_archive_chats(db)
    assert rows == []


# ---------------------------------------------------------------------------
# build_audit_rows
# ---------------------------------------------------------------------------


def test_build_audit_rows_known_tg_sorted_by_delta(tmp_path: Path) -> None:
    """Known TG counts: чат с наибольшей delta идёт первым."""
    from scripts.audit_chat_coverage import build_audit_rows

    archive_chats = [
        {"chat_id": "c1", "title": "Chat 1", "db_count": 10},
        {"chat_id": "c2", "title": "Chat 2", "db_count": 500},
        {"chat_id": "c3", "title": "Chat 3", "db_count": 50},
    ]
    tg_counts = {"c1": 1000, "c2": 600, "c3": 100}

    rows = build_audit_rows(archive_chats, tg_counts, threshold=100)

    # Delta: c1=990, c3=50, c2=100 → c1 first (largest delta)
    assert rows[0]["chat_id"] == "c1"
    assert rows[0]["delta"] == 990
    assert rows[0]["coverage_pct"] == 1.0

    # c2 delta=100, c3 delta=50 → c2 before c3
    assert rows[1]["chat_id"] == "c2"
    assert rows[2]["chat_id"] == "c3"


def test_build_audit_rows_unknown_tg_sorted_by_db_count(tmp_path: Path) -> None:
    """Unknown TG: чаты с меньшим db_count идут первыми."""
    from scripts.audit_chat_coverage import build_audit_rows

    archive_chats = [
        {"chat_id": "c1", "title": "T1", "db_count": 50},
        {"chat_id": "c2", "title": "T2", "db_count": 8},
        {"chat_id": "c3", "title": "T3", "db_count": 200},
    ]
    tg_counts = {"c1": None, "c2": None, "c3": None}

    rows = build_audit_rows(archive_chats, tg_counts, threshold=100)

    # Ascending by db_count (8, 50, 200)
    assert rows[0]["chat_id"] == "c2"
    assert rows[1]["chat_id"] == "c1"
    assert rows[2]["chat_id"] == "c3"


def test_build_audit_rows_under_threshold_flag(tmp_path: Path) -> None:
    """under_threshold корректно выставляется."""
    from scripts.audit_chat_coverage import build_audit_rows

    archive_chats = [
        {"chat_id": "c1", "title": "T", "db_count": 8},  # under
        {"chat_id": "c2", "title": "T", "db_count": 100},  # exactly threshold → not under
        {"chat_id": "c3", "title": "T", "db_count": 200},  # above
    ]
    tg_counts = {k: None for k in ("c1", "c2", "c3")}

    rows = build_audit_rows(archive_chats, tg_counts, threshold=100)
    by_id = {r["chat_id"]: r for r in rows}

    assert by_id["c1"]["under_threshold"] is True
    assert by_id["c2"]["under_threshold"] is False
    assert by_id["c3"]["under_threshold"] is False


def test_build_audit_rows_ymb_scenario(tmp_path: Path) -> None:
    """YMB case: chat с 8 msg помечается как under_threshold."""
    from scripts.audit_chat_coverage import build_audit_rows

    archive_chats = [
        {"chat_id": "-1001804661353", "title": "YMB FAMILY FOREVER", "db_count": 8},
    ]
    tg_counts = {"-1001804661353": None}

    rows = build_audit_rows(archive_chats, tg_counts, threshold=100)

    assert len(rows) == 1
    assert rows[0]["under_threshold"] is True
    assert rows[0]["db_count"] == 8
    assert rows[0]["delta_str"] == "—"
    assert rows[0]["pct_str"] == "—"


# ---------------------------------------------------------------------------
# generate_markdown
# ---------------------------------------------------------------------------


def test_generate_markdown_has_required_headers(tmp_path: Path) -> None:
    """Markdown содержит ожидаемые заголовки."""
    from scripts.audit_chat_coverage import build_audit_rows, generate_markdown

    archive_chats = [
        {"chat_id": "c1", "title": "YMB FAMILY FOREVER", "db_count": 8},
        {"chat_id": "c2", "title": "BigChat", "db_count": 5000},
    ]
    tg_counts = {"c1": None, "c2": None}
    rows = build_audit_rows(archive_chats, tg_counts, threshold=100)

    md = generate_markdown(
        rows,
        threshold=100,
        db_path=tmp_path / "archive.db",
        generated_at="2026-04-21T00:00:00+00:00",
    )

    assert "# Chat Coverage Audit" in md
    assert "## Таблица покрытия" in md
    assert "## Чаты с недостаточным покрытием" in md
    assert "## Рекомендации по бэкфиллу" in md
    # Таблица
    assert "| Chat title |" in md
    assert "| DB count |" in md
    assert "| % coverage |" in md
    # YMB должен появиться в разделе under threshold
    assert "YMB FAMILY FOREVER" in md


def test_generate_markdown_table_row_contains_chat(tmp_path: Path) -> None:
    """Строка таблицы содержит правильные данные."""
    from scripts.audit_chat_coverage import build_audit_rows, generate_markdown

    archive_chats = [{"chat_id": "42", "title": "TestChat", "db_count": 5}]
    rows = build_audit_rows(archive_chats, {"42": None}, threshold=10)

    md = generate_markdown(rows, 10, tmp_path / "archive.db", "2026-04-21T00:00:00+00:00")

    assert "TestChat" in md
    assert "5" in md


# ---------------------------------------------------------------------------
# run_audit (end-to-end)
# ---------------------------------------------------------------------------


def test_run_audit_end_to_end(tmp_path: Path) -> None:
    """run_audit запускается end-to-end на тестовой DB и возвращает правильные данные."""
    from scripts.audit_chat_coverage import run_audit

    db = tmp_path / "archive.db"
    _make_archive_db(
        db,
        [
            ("ymb", "YMB FAMILY FOREVER", 8),
            ("big", "Big Chat", 5000),
            ("mid", "Mid Chat", 150),
        ],
    )

    result = run_audit(db_path=db, threshold=100, skip_telegram=True, output=None)

    assert result["total_chats"] == 3
    assert result["total_db_messages"] == 5158  # 8 + 5000 + 150
    assert result["threshold"] == 100
    assert result["under_threshold_count"] == 1  # только ymb
    assert result["markdown_path"] is None  # output=None

    rows_by_id = {r["chat_id"]: r for r in result["rows"]}
    assert rows_by_id["ymb"]["under_threshold"] is True
    assert rows_by_id["big"]["under_threshold"] is False
    assert rows_by_id["mid"]["under_threshold"] is False


def test_run_audit_threshold_param(tmp_path: Path) -> None:
    """Параметр threshold корректно меняет количество under_threshold чатов."""
    from scripts.audit_chat_coverage import run_audit

    db = tmp_path / "archive.db"
    _make_archive_db(
        db,
        [
            ("a", "Chat A", 50),
            ("b", "Chat B", 200),
            ("c", "Chat C", 1000),
        ],
    )

    # threshold=100 → только "a" под порогом
    r1 = run_audit(db_path=db, threshold=100, skip_telegram=True, output=None)
    assert r1["under_threshold_count"] == 1

    # threshold=500 → "a" и "b"
    r2 = run_audit(db_path=db, threshold=500, skip_telegram=True, output=None)
    assert r2["under_threshold_count"] == 2

    # threshold=2000 → все три
    r3 = run_audit(db_path=db, threshold=2000, skip_telegram=True, output=None)
    assert r3["under_threshold_count"] == 3


def test_run_audit_writes_markdown(tmp_path: Path) -> None:
    """run_audit записывает Markdown файл при output != None."""
    from scripts.audit_chat_coverage import run_audit

    db = tmp_path / "archive.db"
    _make_archive_db(db, [("c1", "Test Chat", 8)])
    out = tmp_path / "audit.md"

    run_audit(db_path=db, threshold=100, skip_telegram=True, output=out)

    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "# Chat Coverage Audit" in content
    assert "Test Chat" in content


def test_run_audit_missing_db(tmp_path: Path) -> None:
    """run_audit gracefully обрабатывает отсутствующую DB."""
    from scripts.audit_chat_coverage import run_audit

    result = run_audit(
        db_path=tmp_path / "no_archive.db",
        threshold=100,
        skip_telegram=True,
        output=None,
    )

    assert result["total_chats"] == 0
    assert result["total_db_messages"] == 0
    assert result["under_threshold_count"] == 0
    assert result["rows"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
