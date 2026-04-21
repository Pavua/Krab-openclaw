# -*- coding: utf-8 -*-
"""
Unit тесты для MCP memory-tools: krab_memory_search и krab_memory_stats.

Модуль живёт в `mcp-servers/telegram/server.py` (dir с дефисом, не Python package),
поэтому импортируем через importlib.util.spec_from_file_location.

Основное поведение:
  - krab_memory_search: пустой query → ok=False / error=empty_query
  - krab_memory_search: happy path → вызывает HybridRetriever.search()
  - krab_memory_search: ImportError → graceful HTTP fallback
  - krab_memory_stats: читает archive.db в read-only (или exists=false)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_server_module():
    """Импортирует mcp-servers/telegram/server.py как модуль.

    Директория содержит дефис, поэтому обычный import невозможен; используем
    importlib.util + ручной путь. Модуль кэшируется в sys.modules['server'].
    """
    module_path = Path(__file__).resolve().parents[2] / "mcp-servers" / "telegram" / "server.py"
    # Обеспечиваем путь к telegram_bridge (лежит рядом с server.py)
    server_dir = str(module_path.parent)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)

    if "mcp_memory_server_under_test" in sys.modules:
        return sys.modules["mcp_memory_server_under_test"]

    spec = importlib.util.spec_from_file_location("mcp_memory_server_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["mcp_memory_server_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server_mod():
    return _load_server_module()


# ---------------------------------------------------------------------------
# krab_memory_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_search_empty_query(server_mod) -> None:
    """Пустая строка в q → ok=False с кодом empty_query."""
    input_model = server_mod._MemorySearchInput(q="   ")
    raw = await server_mod.krab_memory_search(input_model)
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["error"] == "empty_query"


@pytest.mark.asyncio
async def test_memory_search_happy_path(server_mod, monkeypatch) -> None:
    """HybridRetriever.search() мокается, проверяем сериализацию результатов."""
    from src.core import memory_retrieval as _mr

    fake_result = _mr.SearchResult(
        message_id="msg_1",
        chat_id="-100123",
        text_redacted="Пример текста без PII",
        timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        score=0.875,
        context_before=[],
        context_after=[],
    )

    fake_retriever = MagicMock()
    fake_retriever.search.return_value = [fake_result]
    fake_retriever.close.return_value = None

    # Подменяем класс HybridRetriever в src.core.memory_retrieval
    monkeypatch.setattr(_mr, "HybridRetriever", lambda: fake_retriever)

    input_model = server_mod._MemorySearchInput(q="тест", limit=5)
    raw = await server_mod.krab_memory_search(input_model)
    data = json.loads(raw)

    assert data["ok"] is True
    assert data["query"] == "тест"
    assert data["mode"] == "hybrid"
    assert data["count"] == 1
    assert len(data["results"]) == 1
    r0 = data["results"][0]
    assert r0["chunk_id"] == "msg_1"
    assert r0["chat_id"] == "-100123"
    assert r0["text"] == "Пример текста без PII"
    assert abs(r0["score"] - 0.875) < 1e-6
    assert r0["timestamp"].startswith("2025-01-01T12:00")

    fake_retriever.search.assert_called_once()
    fake_retriever.close.assert_called_once()


@pytest.mark.asyncio
async def test_memory_search_truncates_long_text(server_mod, monkeypatch) -> None:
    """Текст длиннее 500 символов обрезается с многоточием."""
    from src.core import memory_retrieval as _mr

    long_text = "x" * 800
    fake_result = _mr.SearchResult(
        message_id="m2",
        chat_id="-100",
        text_redacted=long_text,
        timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
        score=0.5,
    )
    fake_retriever = MagicMock()
    fake_retriever.search.return_value = [fake_result]
    fake_retriever.close.return_value = None
    monkeypatch.setattr(_mr, "HybridRetriever", lambda: fake_retriever)

    raw = await server_mod.krab_memory_search(server_mod._MemorySearchInput(q="long", limit=1))
    data = json.loads(raw)
    assert data["results"][0]["text"].endswith("...")
    assert len(data["results"][0]["text"]) == 503  # 500 + "..."


@pytest.mark.asyncio
async def test_memory_search_exception_returns_error(server_mod, monkeypatch) -> None:
    """Любое исключение из retriever → ok=False / error=search_failed."""
    from src.core import memory_retrieval as _mr

    fake_retriever = MagicMock()
    fake_retriever.search.side_effect = RuntimeError("db locked")
    fake_retriever.close.return_value = None
    monkeypatch.setattr(_mr, "HybridRetriever", lambda: fake_retriever)

    raw = await server_mod.krab_memory_search(server_mod._MemorySearchInput(q="anything"))
    data = json.loads(raw)
    assert data["ok"] is False
    assert "search_failed" in data["error"]
    assert "db locked" in data["error"]


@pytest.mark.asyncio
async def test_memory_search_http_fallback(server_mod, monkeypatch) -> None:
    """ImportError на HybridRetriever → fallback на httpx GET /api/memory/search."""
    import builtins

    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "src.core.memory_retrieval":
            raise ImportError("simulated missing module")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Мокаем httpx.AsyncClient
    fake_resp = MagicMock()
    fake_resp.content = b'{"ok": true, "results": []}'
    fake_resp.json.return_value = {"ok": True, "results": []}

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(server_mod.httpx, "AsyncClient", return_value=fake_client):
        raw = await server_mod.krab_memory_search(server_mod._MemorySearchInput(q="hello", limit=3))
        data = json.loads(raw)
        assert data["ok"] is True
        assert data["results"] == []


# ---------------------------------------------------------------------------
# krab_memory_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_stats_db_missing(server_mod, tmp_path, monkeypatch) -> None:
    """Если archive.db отсутствует → exists=false без ошибки."""
    # Подменяем Path.expanduser, чтобы указать на несуществующий путь
    missing = tmp_path / "ghost" / "archive.db"
    monkeypatch.setenv("HOME", str(tmp_path))  # соф-защита

    import pathlib

    orig_expanduser = pathlib.Path.expanduser

    def fake_expanduser(self):
        if "krab_memory" in str(self):
            return missing
        return orig_expanduser(self)

    monkeypatch.setattr(pathlib.Path, "expanduser", fake_expanduser)

    raw = await server_mod.krab_memory_stats()
    data = json.loads(raw)
    assert data["archive"]["exists"] is False
    assert "path" in data["archive"]


@pytest.mark.asyncio
async def test_memory_stats_reads_db(server_mod, tmp_path, monkeypatch) -> None:
    """Создаём минимальную схему в tmp-БД и проверяем, что stats её читают."""
    import sqlite3

    db = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY);
            CREATE TABLE chats (id INTEGER PRIMARY KEY);
            CREATE TABLE chunks (id INTEGER PRIMARY KEY);
            INSERT INTO meta(key, value) VALUES ('schema_version', '1');
            INSERT INTO messages DEFAULT VALUES;
            INSERT INTO messages DEFAULT VALUES;
            INSERT INTO chats DEFAULT VALUES;
            INSERT INTO chunks DEFAULT VALUES;
            """
        )
        conn.commit()
    finally:
        conn.close()

    import pathlib

    orig_expanduser = pathlib.Path.expanduser

    def fake_expanduser(self):
        if "krab_memory" in str(self):
            return db
        return orig_expanduser(self)

    monkeypatch.setattr(pathlib.Path, "expanduser", fake_expanduser)

    raw = await server_mod.krab_memory_stats()
    data = json.loads(raw)

    assert data["archive"]["exists"] is True
    assert data["archive"]["messages"] == 2
    assert data["archive"]["chats"] == 1
    assert data["archive"]["chunks"] == 1
    assert data["archive"]["schema_version"] == 1
    # vec_chunks_rowids нет — embedded должен быть 0
    assert data["archive"]["embedded"] == 0
    # encoded_chunks — синоним embedded (совместимость с /api/memory/stats)
    assert data["archive"]["encoded_chunks"] == 0
    assert data["archive"]["size_mb"] > 0


@pytest.mark.asyncio
async def test_memory_stats_embedded_via_vec_chunks_rowids(server_mod, tmp_path, monkeypatch) -> None:
    """embedded считается через vec_chunks_rowids, а не через vec_chunks."""
    import sqlite3

    db = tmp_path / "archive.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY);
            CREATE TABLE chats (id INTEGER PRIMARY KEY);
            CREATE TABLE chunks (id INTEGER PRIMARY KEY);
            CREATE TABLE vec_chunks_rowids (
                rowid INTEGER PRIMARY KEY, id INTEGER, chunk_id INTEGER, chunk_offset INTEGER
            );
            INSERT INTO meta(key, value) VALUES ('schema_version', '2');
            INSERT INTO messages DEFAULT VALUES;
            INSERT INTO chats DEFAULT VALUES;
            INSERT INTO chunks DEFAULT VALUES;
            INSERT INTO chunks DEFAULT VALUES;
            INSERT INTO vec_chunks_rowids(rowid, id, chunk_id, chunk_offset) VALUES (1, 1, 1, 0);
            INSERT INTO vec_chunks_rowids(rowid, id, chunk_id, chunk_offset) VALUES (2, 2, 2, 0);
            INSERT INTO vec_chunks_rowids(rowid, id, chunk_id, chunk_offset) VALUES (3, 3, 1, 1);
            """
        )
        conn.commit()
    finally:
        conn.close()

    import pathlib

    orig_expanduser = pathlib.Path.expanduser

    def fake_expanduser(self):
        if "krab_memory" in str(self):
            return db
        return orig_expanduser(self)

    monkeypatch.setattr(pathlib.Path, "expanduser", fake_expanduser)

    raw = await server_mod.krab_memory_stats()
    data = json.loads(raw)

    assert data["archive"]["exists"] is True
    # vec_chunks_rowids содержит 3 строки — embedded должен быть 3
    assert data["archive"]["embedded"] == 3
    # encoded_chunks — синоним, одинаковое значение
    assert data["archive"]["encoded_chunks"] == data["archive"]["embedded"]
