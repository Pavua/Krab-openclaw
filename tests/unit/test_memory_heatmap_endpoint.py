"""Тесты для GET /api/memory/heatmap (chat × time density).

Используем FastAPI TestClient с monkey-patch архивного пути,
чтобы не зависеть от реальной ~/.openclaw/krab_memory/archive.db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_archive(path: Path, *, rows: list[tuple] | None = None) -> None:
    """Создаёт минимальную schema + данные archive.db для тестов."""
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
            );
            CREATE TABLE messages (
                message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                sender_id TEXT,
                timestamp TEXT NOT NULL,
                text_redacted TEXT NOT NULL,
                reply_to_id TEXT,
                PRIMARY KEY (chat_id, message_id)
            );
            """
        )
        if rows:
            conn.executemany(
                "INSERT INTO messages(message_id, chat_id, timestamp, text_redacted) VALUES(?,?,?,?)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def _make_archive_with_titles(path: Path) -> None:
    """archive.db со справочником чатов (chat_title)."""
    _make_archive(
        path,
        rows=[
            ("m1", "chat_alpha", "2026-04-10T10:00:00Z", "hello"),
            ("m2", "chat_alpha", "2026-04-11T10:00:00Z", "world"),
            ("m3", "chat_beta", "2026-04-10T10:00:00Z", "foo"),
        ],
    )
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("INSERT INTO chats(chat_id, title) VALUES('chat_alpha', 'Alpha Chat')")
        conn.execute("INSERT INTO chats(chat_id, title) VALUES('chat_beta', NULL)")
        conn.commit()
    finally:
        conn.close()


def _build_app_with_db(db_path: Path) -> FastAPI:
    """
    Собирает изолированное FastAPI-приложение только с /api/memory/heatmap.
    Патчит Path("~/.openclaw/krab_memory/archive.db").expanduser() → db_path.
    """
    app = FastAPI()

    @app.get("/api/memory/heatmap")
    async def memory_heatmap(bucket_hours: int = 24, top_chats: int = 20):
        import sqlite3 as _sqlite3
        from collections import defaultdict
        from datetime import datetime, timezone

        from fastapi.responses import JSONResponse

        from src.modules.web_app_heatmap import build_bucket_sql_expr

        bucket_hours = max(1, min(int(bucket_hours), 8760))

        # Позволяем подменять путь через атрибут приложения (инъекция в тест)
        db_path_inner: Path = app.state.archive_db  # type: ignore[attr-defined]

        if not db_path_inner.exists():
            return JSONResponse(
                status_code=503,
                content={"error": f"archive.db not found: {db_path_inner}"},
            )

        try:
            uri = f"file:{db_path_inner}?mode=ro"
            conn = _sqlite3.connect(uri, uri=True)
        except _sqlite3.OperationalError as exc:
            return JSONResponse(
                status_code=503,
                content={"error": f"archive.db open failed: {exc}"},
            )

        try:
            try:
                top_rows = conn.execute(
                    """
                    SELECT chat_id, COUNT(*) as cnt
                    FROM messages
                    GROUP BY chat_id
                    ORDER BY cnt DESC
                    LIMIT ?
                    """,
                    (max(1, top_chats),),
                ).fetchall()
            except _sqlite3.DatabaseError as exc:
                return JSONResponse(
                    status_code=503,
                    content={"error": f"archive.db malformed: {exc}"},
                )

            if not top_rows:
                return {
                    "bucket_hours": bucket_hours,
                    "chats": [],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }

            top_chat_ids = [r[0] for r in top_rows]

            chat_titles: dict[str, str] = {}
            try:
                placeholders = ",".join("?" * len(top_chat_ids))
                title_rows = conn.execute(
                    f"SELECT chat_id, title FROM chats WHERE chat_id IN ({placeholders})",
                    top_chat_ids,
                ).fetchall()
                chat_titles = {r[0]: r[1] for r in title_rows if r[1]}
            except _sqlite3.DatabaseError:
                pass

            bucket_expr = build_bucket_sql_expr(bucket_hours)
            try:
                placeholders = ",".join("?" * len(top_chat_ids))
                density_rows = conn.execute(
                    f"""
                    SELECT chat_id,
                           {bucket_expr} AS bucket_ts,
                           COUNT(*) AS cnt
                    FROM messages
                    WHERE chat_id IN ({placeholders})
                    GROUP BY chat_id, bucket_ts
                    ORDER BY chat_id, bucket_ts
                    """,
                    top_chat_ids,
                ).fetchall()
            except _sqlite3.DatabaseError as exc:
                return JSONResponse(
                    status_code=503,
                    content={"error": f"archive.db malformed on density query: {exc}"},
                )

            buckets_by_chat: dict[str, list[dict]] = defaultdict(list)
            for chat_id, bucket_ts, cnt in density_rows:
                buckets_by_chat[chat_id].append({"ts": bucket_ts, "count": cnt})

            chats_out = []
            for chat_id in top_chat_ids:
                chats_out.append(
                    {
                        "chat_id": chat_id,
                        "chat_title": chat_titles.get(chat_id, chat_id),
                        "buckets": buckets_by_chat.get(chat_id, []),
                    }
                )

            return {
                "bucket_hours": bucket_hours,
                "chats": chats_out,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        finally:
            conn.close()

    app.state.archive_db = db_path
    return app


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestMemoryHeatmapEndpoint:
    """Тесты /api/memory/heatmap через TestClient."""

    def test_200_default_params(self, tmp_path: Path) -> None:
        """200 с дефолтными параметрами, ключи присутствуют."""
        db = tmp_path / "archive.db"
        _make_archive(
            db,
            rows=[
                ("m1", "c1", "2026-04-10T10:00:00Z", "hello"),
                ("m2", "c1", "2026-04-11T10:00:00Z", "world"),
                ("m3", "c2", "2026-04-10T10:00:00Z", "foo"),
            ],
        )
        app = _build_app_with_db(db)
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.get("/api/memory/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert "bucket_hours" in data
        assert data["bucket_hours"] == 24
        assert "chats" in data
        assert "generated_at" in data
        assert len(data["chats"]) == 2

    def test_bucket_hours_param_reflected(self, tmp_path: Path) -> None:
        """bucket_hours в ответе совпадает с переданным значением."""
        db = tmp_path / "archive.db"
        _make_archive(db, rows=[("m1", "c1", "2026-04-10T10:00:00Z", "hi")])
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?bucket_hours=6")
        assert resp.status_code == 200
        assert resp.json()["bucket_hours"] == 6

    def test_top_chats_limit(self, tmp_path: Path) -> None:
        """top_chats ограничивает количество чатов в ответе."""
        db = tmp_path / "archive.db"
        rows = [(f"m{i}", f"chat_{i:03d}", "2026-04-10T10:00:00Z", "msg") for i in range(30)]
        _make_archive(db, rows=rows)
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?top_chats=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chats"]) == 5

    def test_chat_title_resolves_from_chats_table(self, tmp_path: Path) -> None:
        """chat_title берётся из таблицы chats если title IS NOT NULL."""
        db = tmp_path / "archive.db"
        _make_archive_with_titles(db)
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?top_chats=10")
        assert resp.status_code == 200
        chats = {c["chat_id"]: c for c in resp.json()["chats"]}
        # chat_alpha: title = "Alpha Chat"
        assert chats["chat_alpha"]["chat_title"] == "Alpha Chat"
        # chat_beta: title = NULL → fallback to chat_id
        assert chats["chat_beta"]["chat_title"] == "chat_beta"

    def test_chat_title_fallback_to_chat_id(self, tmp_path: Path) -> None:
        """Если таблицы chats нет — chat_title = chat_id."""
        db = tmp_path / "archive.db"
        # Только messages, без chats
        conn = sqlite3.connect(str(db))
        try:
            conn.executescript(
                """
                CREATE TABLE messages (
                    message_id TEXT, chat_id TEXT, timestamp TEXT, text_redacted TEXT,
                    PRIMARY KEY (chat_id, message_id)
                );
                INSERT INTO messages VALUES('m1','lonely_chat','2026-04-10T10:00:00Z','hi');
                """
            )
            conn.commit()
        finally:
            conn.close()
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap")
        assert resp.status_code == 200
        chats = resp.json()["chats"]
        assert len(chats) == 1
        assert chats[0]["chat_title"] == "lonely_chat"

    def test_db_missing_returns_503(self, tmp_path: Path) -> None:
        """Если archive.db не существует, возвращается 503, не 500/crash."""
        app = _build_app_with_db(tmp_path / "no_such_file.db")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/memory/heatmap")
        assert resp.status_code == 503
        assert "error" in resp.json()

    def test_empty_db_returns_empty_chats(self, tmp_path: Path) -> None:
        """Пустая таблица messages → chats: []."""
        db = tmp_path / "archive.db"
        _make_archive(db, rows=[])
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chats"] == []
        assert data["bucket_hours"] == 24

    def test_buckets_structure(self, tmp_path: Path) -> None:
        """Каждый bucket содержит ts (дата) и count (int > 0)."""
        db = tmp_path / "archive.db"
        _make_archive(
            db,
            rows=[
                ("m1", "c1", "2026-04-10T08:00:00Z", "a"),
                ("m2", "c1", "2026-04-10T20:00:00Z", "b"),
                ("m3", "c1", "2026-04-11T10:00:00Z", "c"),
            ],
        )
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap")
        assert resp.status_code == 200
        chats = resp.json()["chats"]
        assert len(chats) == 1
        buckets = chats[0]["buckets"]
        # 2 дня
        assert len(buckets) == 2
        days = {b["ts"] for b in buckets}
        assert "2026-04-10" in days
        assert "2026-04-11" in days
        # April 10 = 2 сообщения
        april10 = next(b for b in buckets if b["ts"] == "2026-04-10")
        assert april10["count"] == 2

    def test_bucket_hours_1_hourly_aggregation(self, tmp_path: Path) -> None:
        """bucket_hours=1: каждый час — отдельный bucket."""
        db = tmp_path / "archive.db"
        _make_archive(
            db,
            rows=[
                ("m1", "c1", "2026-04-10T08:15:00Z", "a"),
                ("m2", "c1", "2026-04-10T08:45:00Z", "b"),  # тот же час → один bucket
                ("m3", "c1", "2026-04-10T09:05:00Z", "c"),  # другой час
                ("m4", "c1", "2026-04-10T10:00:00Z", "d"),
            ],
        )
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?bucket_hours=1")
        assert resp.status_code == 200
        buckets = resp.json()["chats"][0]["buckets"]
        # 3 уникальных часовых корзины
        assert len(buckets) == 3
        ts_to_count = {b["ts"]: b["count"] for b in buckets}
        assert ts_to_count["2026-04-10T08:00:00Z"] == 2
        assert ts_to_count["2026-04-10T09:00:00Z"] == 1
        assert ts_to_count["2026-04-10T10:00:00Z"] == 1

    def test_bucket_hours_6_six_hour_buckets(self, tmp_path: Path) -> None:
        """bucket_hours=6: сообщения внутри одной 6-часовой корзины сливаются."""
        db = tmp_path / "archive.db"
        _make_archive(
            db,
            rows=[
                ("m1", "c1", "2026-04-10T00:30:00Z", "a"),  # bucket 00:00
                ("m2", "c1", "2026-04-10T05:55:00Z", "b"),  # bucket 00:00
                ("m3", "c1", "2026-04-10T06:01:00Z", "c"),  # bucket 06:00
                ("m4", "c1", "2026-04-10T13:00:00Z", "d"),  # bucket 12:00
            ],
        )
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?bucket_hours=6")
        assert resp.status_code == 200
        buckets = resp.json()["chats"][0]["buckets"]
        ts_to_count = {b["ts"]: b["count"] for b in buckets}
        assert ts_to_count["2026-04-10T00:00:00Z"] == 2
        assert ts_to_count["2026-04-10T06:00:00Z"] == 1
        assert ts_to_count["2026-04-10T12:00:00Z"] == 1

    def test_bucket_hours_168_weekly(self, tmp_path: Path) -> None:
        """bucket_hours=168 (неделя): сообщения внутри одной недели сливаются."""
        db = tmp_path / "archive.db"
        _make_archive(
            db,
            rows=[
                # Все три внутри одной 168-часовой корзины
                ("m1", "c1", "2026-04-10T00:00:00Z", "a"),
                ("m2", "c1", "2026-04-12T00:00:00Z", "b"),
                ("m3", "c1", "2026-04-15T00:00:00Z", "c"),
            ],
        )
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?bucket_hours=168")
        assert resp.status_code == 200
        buckets = resp.json()["chats"][0]["buckets"]
        # Все 3 сообщения в одной недельной корзине
        assert len(buckets) == 1
        assert buckets[0]["count"] == 3

    def test_bucket_hours_results_differ_across_intervals(self, tmp_path: Path) -> None:
        """Разные bucket_hours должны давать разные результаты на одном датасете."""
        db = tmp_path / "archive.db"
        _make_archive(
            db,
            rows=[
                ("m1", "c1", "2026-04-10T00:30:00Z", "a"),
                ("m2", "c1", "2026-04-10T08:30:00Z", "b"),
                ("m3", "c1", "2026-04-10T16:30:00Z", "c"),
                ("m4", "c1", "2026-04-11T08:30:00Z", "d"),
            ],
        )
        app = _build_app_with_db(db)
        client = TestClient(app)

        hourly = client.get("/api/memory/heatmap?bucket_hours=1").json()["chats"][0]["buckets"]
        six = client.get("/api/memory/heatmap?bucket_hours=6").json()["chats"][0]["buckets"]
        daily = client.get("/api/memory/heatmap?bucket_hours=24").json()["chats"][0]["buckets"]

        # Hourly: 4 уникальных часа → 4 bucket'а
        assert len(hourly) == 4
        # 6-hour: 4 разные 6-часовые корзины (00, 06, 12, 06 след. дня)
        assert len(six) == 4
        # Daily: 2 дня
        assert len(daily) == 2
        # Daily всё ещё в формате YYYY-MM-DD (backward-compat)
        assert all(len(b["ts"]) == 10 for b in daily)
        # Sub-daily — ISO datetime с 'Z'
        assert all(b["ts"].endswith("Z") for b in hourly)

    def test_bucket_hours_clamped_to_minimum(self, tmp_path: Path) -> None:
        """bucket_hours <= 0 clamp'ится к 1."""
        db = tmp_path / "archive.db"
        _make_archive(db, rows=[("m1", "c1", "2026-04-10T08:15:00Z", "a")])
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?bucket_hours=0")
        assert resp.status_code == 200
        assert resp.json()["bucket_hours"] == 1

    def test_top_chats_ordering_by_message_count(self, tmp_path: Path) -> None:
        """Чаты упорядочены по убыванию количества сообщений."""
        db = tmp_path / "archive.db"
        rows = [("m_big_%d" % i, "big_chat", "2026-04-10T10:00:00Z", "x") for i in range(10)] + [
            ("m_small_%d" % i, "small_chat", "2026-04-10T10:00:00Z", "x") for i in range(3)
        ]
        _make_archive(db, rows=rows)
        app = _build_app_with_db(db)
        client = TestClient(app)

        resp = client.get("/api/memory/heatmap?top_chats=2")
        assert resp.status_code == 200
        chats = resp.json()["chats"]
        assert chats[0]["chat_id"] == "big_chat"
        assert chats[1]["chat_id"] == "small_chat"
