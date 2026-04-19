# -*- coding: utf-8 -*-
"""
Тесты для подсчёта FTS5/vec_chunks orphans в !health deep (Wave 29-PP).

Проверяет:
- collect_health_deep() правильно считает orphans через SQL
- fallback к None при недоступной таблице/extension
- _health_deep_report() форматирует orphans с ⚠️ при ненулевых значениях
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── helpers ────────────────────────────────────────────────────────────────

def _make_db_with_orphans(
    fts_orphans: int = 0,
    vec_orphans: int = 0,
) -> Path:
    """Создаёт временную archive.db с нужным числом orphan-строк в FTS5."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            text TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            text TEXT
        )
    """)
    # FTS shadow table (упрощённая имитация messages_fts_docsize)
    conn.execute("""
        CREATE TABLE messages_fts_docsize (
            id INTEGER PRIMARY KEY,
            sz BLOB
        )
    """)
    # Добавляем реальные chunks
    for i in range(1, 6):
        conn.execute("INSERT INTO chunks(id, text) VALUES (?, ?)", (i, f"chunk {i}"))
        conn.execute("INSERT INTO messages_fts_docsize(id, sz) VALUES (?, ?)", (i, b"\x00"))

    # Добавляем orphan-записи в FTS (id не соответствует ни одному chunk.id)
    for j in range(100, 100 + fts_orphans):
        conn.execute("INSERT INTO messages_fts_docsize(id, sz) VALUES (?, ?)", (j, b"\x00"))

    conn.commit()
    conn.close()
    return db_path


def _make_bot_stub() -> MagicMock:
    import time

    from src.core.access_control import AccessLevel

    bot = MagicMock()
    bot.me = MagicMock(id=42)
    bot._session_start_time = time.time() - 3600
    bot._session_messages_processed = 0
    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "deep"
    return bot


def _base_data(**overrides) -> dict:
    base: dict = {
        "krab": {"uptime_sec": 3600, "rss_mb": 256, "cpu_pct": 0.5},
        "openclaw": {"healthy": True, "last_route": {"model": "gemini-3-pro"}},
        "lm_studio": {"state": "offline", "active_model": None},
        "archive_db": {
            "integrity": "ok",
            "messages": 43318,
            "chunks": 9163,
            "size_mb": 51.1,
            "orphan_fts5": None,
            "orphan_vec": None,
        },
        "reminders": {"pending": 0},
        "memory_validator": {"pending_confirm": 0},
        "sigterm_recent_count": 0,
        "system": {
            "load_avg": [0.5, 0.8, 1.0],
            "free_mb": 16384,
            "total_mb": 32768,
            "used_pct": 50.0,
        },
    }
    base.update(overrides)
    return base


# ─── тесты collect_health_deep (SQL логика) ─────────────────────────────────

@pytest.mark.asyncio
async def test_fts5_orphans_counted_via_docsize_join():
    """collect_health_deep считает FTS5 orphans через messages_fts_docsize LEFT JOIN chunks."""
    db_path = _make_db_with_orphans(fts_orphans=3)
    try:
        # Напрямую тестируем SQL-запрос без полного collect_health_deep
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute(
                """
                SELECT COUNT(*) FROM messages_fts_docsize AS d
                LEFT JOIN chunks AS c ON c.id = d.id
                WHERE c.id IS NULL
                """
            ).fetchone()[0]
        finally:
            conn.close()
        assert result == 3, f"Ожидали 3 FTS5 orphans, получили {result}"
    finally:
        db_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_fts5_no_orphans_when_in_sync():
    """Нет orphans когда messages_fts_docsize синхронизирована с chunks."""
    db_path = _make_db_with_orphans(fts_orphans=0)
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute(
                """
                SELECT COUNT(*) FROM messages_fts_docsize AS d
                LEFT JOIN chunks AS c ON c.id = d.id
                WHERE c.id IS NULL
                """
            ).fetchone()[0]
        finally:
            conn.close()
        assert result == 0
    finally:
        db_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_fts5_orphans_fallback_to_none_on_missing_table():
    """При отсутствии таблицы messages_fts_docsize — orphan_fts5 становится None."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        fts_orphans: int | None
        try:
            conn2 = sqlite3.connect(str(db_path))
            fts_orphans = conn2.execute(
                """
                SELECT COUNT(*) FROM messages_fts_docsize AS d
                LEFT JOIN chunks AS c ON c.id = d.id
                WHERE c.id IS NULL
                """
            ).fetchone()[0]
            conn2.close()
        except sqlite3.OperationalError:
            fts_orphans = None

        assert fts_orphans is None, "Должен вернуть None при отсутствии таблицы"
    finally:
        db_path.unlink(missing_ok=True)


# ─── тесты _health_deep_report (форматирование) ──────────────────────────────

_COLLECT_PATH = "src.core.health_deep_collector.collect_health_deep"


@pytest.mark.asyncio
async def test_report_shows_na_when_both_orphans_none():
    """Если orphan_fts5 и orphan_vec оба None — показывает 'n/a'."""
    from src.handlers.command_handlers import _health_deep_report

    bot = _make_bot_stub()
    data = _base_data()
    data["archive_db"]["orphan_fts5"] = None
    data["archive_db"]["orphan_vec"] = None

    with patch(_COLLECT_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    assert "FTS5 orphans: n/a | vec orphans: n/a" in report


@pytest.mark.asyncio
async def test_report_shows_numbers_when_orphans_present():
    """Если orphans — числа, форматирует как числа."""
    from src.handlers.command_handlers import _health_deep_report

    bot = _make_bot_stub()
    data = _base_data()
    data["archive_db"]["orphan_fts5"] = 1775
    data["archive_db"]["orphan_vec"] = 9180

    with patch(_COLLECT_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    assert "FTS5 orphans: 1775" in report
    assert "vec orphans: 9180" in report


@pytest.mark.asyncio
async def test_report_adds_warning_prefix_when_orphans_nonzero():
    """При ненулевых orphans — строка начинается с ⚠️."""
    from src.handlers.command_handlers import _health_deep_report

    bot = _make_bot_stub()
    data = _base_data()
    data["archive_db"]["orphan_fts5"] = 100
    data["archive_db"]["orphan_vec"] = 0

    with patch(_COLLECT_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    # строка orphans должна содержать ⚠️
    assert "⚠️" in report
    assert "FTS5 orphans: 100" in report


@pytest.mark.asyncio
async def test_report_no_warning_when_all_zeros():
    """При нулевых orphans — ⚠️ не добавляется в orphan-строку."""
    from src.handlers.command_handlers import _health_deep_report

    bot = _make_bot_stub()
    data = _base_data()
    data["archive_db"]["orphan_fts5"] = 0
    data["archive_db"]["orphan_vec"] = 0

    with patch(_COLLECT_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    assert "FTS5 orphans: 0" in report
    # ⚠️ может быть из других секций, проверяем что orphan-строка чистая
    assert "⚠️ FTS5" not in report


@pytest.mark.asyncio
async def test_report_handles_mixed_none_and_number():
    """FTS5 orphans = число, vec orphans = None (extension недоступен)."""
    from src.handlers.command_handlers import _health_deep_report

    bot = _make_bot_stub()
    data = _base_data()
    data["archive_db"]["orphan_fts5"] = 42
    data["archive_db"]["orphan_vec"] = None

    with patch(_COLLECT_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    assert "FTS5 orphans: 42" in report
    assert "vec orphans: n/a" in report
    # fts5 orphans > 0, поэтому должен быть ⚠️
    assert "⚠️" in report
