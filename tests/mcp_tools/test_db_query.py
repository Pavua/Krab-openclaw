"""Тесты db_query MCP tool (READ-ONLY SQL к whitelisted БД)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Фикстуры.
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_archive_db(tmp_path: Path, mcp_server, monkeypatch) -> Path:
    """Создаёт временную SQLite БД и подменяет whitelist 'archive' -> она."""
    db = tmp_path / "archive.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);")
        conn.executemany(
            "INSERT INTO items (name) VALUES (?);",
            [("alpha",), ("beta",), ("gamma",)],
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setitem(mcp_server._DB_WHITELIST, "archive", db)
    return db


# ---------------------------------------------------------------------------
# Тесты.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_query_select_ok(mcp_server, tmp_archive_db):
    """SELECT проходит, возвращает columns/rows/row_count."""
    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(sql="SELECT id, name FROM items ORDER BY id", db_name="archive")
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["columns"] == ["id", "name"]
    assert data["row_count"] == 3
    assert data["rows"][0] == [1, "alpha"]


@pytest.mark.asyncio
async def test_db_query_with_cte_ok(mcp_server, tmp_archive_db):
    """WITH (CTE) тоже read-only — должен пройти."""
    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(
            sql="WITH t AS (SELECT name FROM items) SELECT name FROM t",
            db_name="archive",
        )
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["row_count"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_sql",
    [
        "INSERT INTO items (name) VALUES ('x')",
        "UPDATE items SET name='x' WHERE id=1",
        "DELETE FROM items",
        "DROP TABLE items",
        "CREATE TABLE foo (x INT)",
        "ALTER TABLE items ADD COLUMN z INT",
        "ATTACH DATABASE '/tmp/x.db' AS other",
        "PRAGMA writable_schema = 1",
        "SELECT 1; DROP TABLE items",
    ],
)
async def test_db_query_rejects_write_statements(mcp_server, tmp_archive_db, bad_sql):
    """INSERT/DROP/ATTACH/PRAGMA-writes/multi-statement блокируются."""
    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(sql=bad_sql, db_name="archive")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "not_read_only_statement"


@pytest.mark.asyncio
async def test_db_query_unknown_db_rejected(mcp_server):
    """Неизвестный db_name отклоняется с db_not_whitelisted."""
    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(sql="SELECT 1", db_name="evil")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "db_not_whitelisted"
    assert "archive" in data["allowed"]


@pytest.mark.asyncio
async def test_db_query_whitelisted_memory_accepted(mcp_server, tmp_path, monkeypatch):
    """db_name='memory' (whitelisted) принимается."""
    db = tmp_path / "chroma.sqlite3"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE t (x INTEGER);")
        conn.execute("INSERT INTO t VALUES (42);")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setitem(mcp_server._DB_WHITELIST, "memory", db)

    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(sql="SELECT x FROM t", db_name="memory")
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["rows"] == [[42]]


@pytest.mark.asyncio
async def test_db_query_limit_capped(mcp_server, tmp_archive_db):
    """limit ограничивает число строк."""
    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(sql="SELECT id FROM items", db_name="archive", limit=2)
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["row_count"] == 2


@pytest.mark.asyncio
async def test_db_query_sql_error_path(mcp_server, tmp_archive_db):
    """Битый SQL возвращает error=sql_error."""
    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(sql="SELECT * FROM no_such_table", db_name="archive")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "sql_error"


@pytest.mark.asyncio
async def test_db_query_timeout_handling(mcp_server, tmp_archive_db, monkeypatch):
    """Если внутренний executor висит дольше лимита — возвращается 'timeout'."""

    async def _never_return(*args, **kwargs):
        await asyncio.sleep(30.0)

    # Патчим asyncio.to_thread внутри модуля сервера — чтобы wait_for сработал.
    # asyncio.wait_for с timeout=10s в проде, тут обрубим искусственно.
    original_wait_for = asyncio.wait_for

    async def _short_wait_for(coro, timeout):
        # Гасим реальный coroutine, подставляем короткий таймаут.
        try:
            coro.close()
        except Exception:
            pass
        return await original_wait_for(_never_return(), timeout=0.05)

    monkeypatch.setattr(mcp_server.asyncio, "wait_for", _short_wait_for)

    result = await mcp_server.db_query(
        mcp_server._DbQueryInput(sql="SELECT 1", db_name="archive")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "timeout"
