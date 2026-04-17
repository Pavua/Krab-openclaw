"""
Integration-тесты `/api/memory/search` endpoint'а (Memory Layer Phase 2 retrieval).

Покрывают:
  - пустой запрос → ok=False, error=empty_query;
  - отсутствующая archive.db → ok=False, error=archive_db_missing;
  - FTS5-путь с реальной tmp archive.db → результаты возвращаются;
  - hybrid режим дедуплицирует по chunk_id (через RRF в HybridRetriever);
  - invalid mode → ok=False.

Тесты создают реальный tmp archive.db и монкипатчат `ArchivePaths.default()`,
чтобы endpoint видел тестовую БД, а не production `~/.openclaw/krab_memory`.
"""

from __future__ import annotations

# ── env-guard до импортов src.* ──────────────────────────────────────────
import os

for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.core.memory_archive import ArchivePaths, create_schema, open_archive  # noqa: E402
from src.modules.web_app import WebApp  # noqa: E402

# ---------------------------------------------------------------------------
# Вспомогательные заглушки (аналогично test_web_app_dashboard_endpoints).
# ---------------------------------------------------------------------------


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free"}

    async def get_cloud_runtime_check(self) -> dict:
        return {"ok": True}

    async def health_check(self) -> bool:
        return True


class _FakeKraab:
    pass


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


def _make_client() -> TestClient:
    deps: dict[str, Any] = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# Фикстуры для БД.
# ---------------------------------------------------------------------------


def _seed_minimal_archive(conn: sqlite3.Connection, chat_id: str = "-100111") -> None:
    """Заполняет chunks + messages_fts минимальным набором данных."""
    conn.execute(
        "INSERT OR IGNORE INTO chats(chat_id, title, chat_type) VALUES (?, ?, ?);",
        (chat_id, "test chat", "supergroup"),
    )
    chunks = [
        ("c1", "2026-04-01T10:00:00Z", "обсудили dashboard redesign в команде"),
        ("c2", "2026-04-01T10:05:00Z", "dashboard layout и metrics"),
        ("c3", "2026-04-01T11:00:00Z", "совсем другой разговор про docker"),
    ]
    for chunk_id, ts, text in chunks:
        msg_id = f"m_{chunk_id}"
        conn.execute(
            "INSERT INTO messages(message_id, chat_id, timestamp, text_redacted) "
            "VALUES (?, ?, ?, ?);",
            (msg_id, chat_id, ts, text),
        )
        cur = conn.execute(
            "INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts, "
            "message_count, char_len, text_redacted) "
            "VALUES (?, ?, ?, ?, ?, ?, ?);",
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
    conn.commit()


@pytest.fixture
def seeded_archive(tmp_path: Path, monkeypatch):
    """Создаёт tmp archive.db со schema+данными и подменяет ArchivePaths.default."""
    paths = ArchivePaths.under(tmp_path / "mem")
    conn = open_archive(paths)
    create_schema(conn)
    _seed_minimal_archive(conn)
    conn.close()

    # Monkeypatch ArchivePaths.default, чтобы endpoint видел tmp БД.
    from src.core import memory_archive as _ma

    monkeypatch.setattr(_ma.ArchivePaths, "default", classmethod(lambda cls: paths))
    yield paths


@pytest.fixture
def missing_archive(tmp_path: Path, monkeypatch):
    """ArchivePaths указывает на несуществующую БД."""
    paths = ArchivePaths.under(tmp_path / "missing")
    # НЕ создаём paths.db — пусть отсутствует.
    from src.core import memory_archive as _ma

    monkeypatch.setattr(_ma.ArchivePaths, "default", classmethod(lambda cls: paths))
    yield paths


# ---------------------------------------------------------------------------
# Тесты.
# ---------------------------------------------------------------------------


def test_search_empty_query_returns_error() -> None:
    """Пустой q → ok=False, error='empty_query'."""
    client = _make_client()
    resp = client.get("/api/memory/search", params={"q": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "empty_query"


def test_search_whitespace_query_returns_error() -> None:
    """Пробельный q также trim'ится и считается empty."""
    client = _make_client()
    resp = client.get("/api/memory/search", params={"q": "   "})
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "empty_query"


def test_search_invalid_mode_returns_error() -> None:
    """Неизвестный mode → ok=False, error='invalid_mode'."""
    client = _make_client()
    resp = client.get("/api/memory/search", params={"q": "test", "mode": "banana"})
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "invalid_mode"


def test_search_archive_missing(missing_archive) -> None:
    """Если archive.db нет → ok=False, error='archive_db_missing'."""
    client = _make_client()
    resp = client.get("/api/memory/search", params={"q": "hello"})
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "archive_db_missing"


def test_search_fts_returns_results(seeded_archive) -> None:
    """FTS5 путь возвращает chunks с text и score."""
    client = _make_client()
    resp = client.get(
        "/api/memory/search",
        params={"q": "dashboard", "mode": "fts", "limit": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["query"] == "dashboard"
    assert data["count"] >= 1
    # chunks с "dashboard" должны прийти.
    texts = [r["text"] for r in data["results"]]
    assert any("dashboard" in t for t in texts)
    # Score — float в 0..1 (после нормализации).
    for r in data["results"]:
        assert isinstance(r["score"], float)
        assert 0.0 <= r["score"] <= 1.0
        assert "chunk_id" in r
        assert "text" in r
        assert "mode" in r


def test_search_hybrid_dedups(seeded_archive) -> None:
    """Режим hybrid дедуплицирует результаты по chunk_id (через RRF)."""
    client = _make_client()
    resp = client.get(
        "/api/memory/search",
        params={"q": "dashboard", "mode": "hybrid", "limit": 10},
    )
    data = resp.json()
    assert data["ok"] is True
    chunk_ids = [r["chunk_id"] for r in data["results"]]
    # Нет дубликатов chunk_id.
    assert len(chunk_ids) == len(set(chunk_ids))


def test_search_limit_respected(seeded_archive) -> None:
    """Параметр limit ограничивает размер ответа."""
    client = _make_client()
    resp = client.get(
        "/api/memory/search",
        params={"q": "dashboard docker разговор", "mode": "fts", "limit": 1},
    )
    data = resp.json()
    assert data["ok"] is True
    assert len(data["results"]) <= 1


def test_search_text_truncated_to_300_chars(seeded_archive, tmp_path, monkeypatch) -> None:
    """Если chunk.text длиннее 300 символов — ответ truncate'ится."""
    # Создаём отдельную БД с длинным текстом.
    paths = ArchivePaths.under(tmp_path / "long")
    conn = open_archive(paths)
    create_schema(conn)
    long_text = "dashboard " + ("x" * 600)
    conn.execute(
        "INSERT INTO chats(chat_id, title, chat_type) VALUES (?, ?, ?);",
        ("-100999", "long", "supergroup"),
    )
    conn.execute(
        "INSERT INTO messages(message_id, chat_id, timestamp, text_redacted) VALUES (?, ?, ?, ?);",
        ("m1", "-100999", "2026-04-01T10:00:00Z", long_text),
    )
    cur = conn.execute(
        "INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts, "
        "message_count, char_len, text_redacted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?);",
        (
            "cl",
            "-100999",
            "2026-04-01T10:00:00Z",
            "2026-04-01T10:00:00Z",
            1,
            len(long_text),
            long_text,
        ),
    )
    rowid = cur.lastrowid
    conn.execute(
        "INSERT INTO chunk_messages(chunk_id, message_id, chat_id) VALUES (?, ?, ?);",
        ("cl", "m1", "-100999"),
    )
    conn.execute(
        "INSERT INTO messages_fts(rowid, text_redacted) VALUES (?, ?);",
        (rowid, long_text),
    )
    conn.commit()
    conn.close()

    from src.core import memory_archive as _ma

    monkeypatch.setattr(_ma.ArchivePaths, "default", classmethod(lambda cls: paths))

    client = _make_client()
    resp = client.get("/api/memory/search", params={"q": "dashboard"})
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] >= 1
    r = data["results"][0]
    # 300 preview + "..." суффикс = 303.
    assert len(r["text"]) <= 303
    assert r["text"].endswith("...")
