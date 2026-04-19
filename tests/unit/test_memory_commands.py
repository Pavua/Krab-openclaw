"""
Unit-тесты userbot команд Memory Layer.

Покрывают:
  - handle_archive() возвращает usage при пустом query;
  - handle_archive() интегрируется с HybridRetriever (via fake);
  - пустые результаты дают человекочитаемое сообщение;
  - MarkdownV2 escape реально экранирует спец-символы;
  - collect_stats() на отсутствующей БД, пустой БД, заполненной;
  - vectors=-1 если vec_chunks нет в БД;
  - форматирование не выходит за лимит 4000 символов.
"""

from __future__ import annotations

# ВАЖНО: установить env-vars ДО любых импортов src.*, потому что
# `src/handlers/__init__.py` подтягивает `command_handlers` → `config.py`,
# где `TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))` падает
# на пустой строке (а не на отсутствующей переменной).
import os

for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.core.memory_archive import (
    ArchivePaths,
    create_schema,
    open_archive,
)
from src.core.memory_retrieval import SearchResult
from src.handlers.memory_commands import (
    TELEGRAM_MESSAGE_LIMIT,
    MemoryCommandHandler,
    MemoryStats,
    _escape_md,
    _format_bytes,
    _format_stats,
    _short_date,
    _truncate,
)

# ---------------------------------------------------------------------------
# Fake retriever.
# ---------------------------------------------------------------------------


class _FakeRetriever:
    """Контролируемая заглушка HybridRetriever для command-тестов."""

    def __init__(
        self, canned: list[SearchResult] | None = None, raise_on_search: bool = False
    ) -> None:
        self.canned = canned or []
        self.raise_on_search = raise_on_search
        self.last_kwargs: dict | None = None

    def search(self, query: str, **kwargs) -> list[SearchResult]:
        if self.raise_on_search:
            raise RuntimeError("boom")
        self.last_kwargs = {"query": query, **kwargs}
        return list(self.canned)

    def close(self) -> None:
        pass


def _sr(
    mid: str = "m1",
    chat_id: str = "-100",
    text: str = "hello world",
    score: float = 0.75,
    ts: Optional[datetime] = None,
    before: list[str] | None = None,
    after: list[str] | None = None,
) -> SearchResult:
    return SearchResult(
        message_id=mid,
        chat_id=chat_id,
        text_redacted=text,
        timestamp=ts or datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        score=score,
        context_before=before or [],
        context_after=after or [],
    )


# ---------------------------------------------------------------------------
# Архивная поисковая команда.
# ---------------------------------------------------------------------------


class TestHandleArchive:
    def test_empty_query_returns_usage(self) -> None:
        handler = MemoryCommandHandler(retriever=_FakeRetriever())
        out = handler.handle_archive("")
        assert "archive" in out.lower()

    def test_whitespace_query_returns_usage(self) -> None:
        handler = MemoryCommandHandler(retriever=_FakeRetriever())
        out = handler.handle_archive("   \t\n")
        assert "archive" in out.lower()

    def test_no_results_returns_human_readable(self) -> None:
        handler = MemoryCommandHandler(retriever=_FakeRetriever(canned=[]))
        out = handler.handle_archive("dashboard")
        assert "ничего не найдено" in out
        # Query подставлен в сообщение.
        assert "dashboard" in out

    def test_results_included(self) -> None:
        canned = [
            _sr(mid="1", text="обсуждали dashboard redesign"),
            _sr(mid="2", text="второй результат", score=0.5),
        ]
        handler = MemoryCommandHandler(retriever=_FakeRetriever(canned=canned))
        out = handler.handle_archive("dashboard")
        assert "обсуждали dashboard redesign" in out
        assert "второй результат" in out
        # Индексы нумерации.
        assert "1" in out and "2" in out

    def test_context_rendered(self) -> None:
        canned = [
            _sr(
                text="main message",
                before=["chunk before"],
                after=["chunk after"],
            ),
        ]
        handler = MemoryCommandHandler(retriever=_FakeRetriever(canned=canned))
        out = handler.handle_archive("q")
        assert "chunk before" in out
        assert "chunk after" in out
        assert "⤴" in out  # маркер before
        assert "⤵" in out  # маркер after

    def test_search_exception_caught(self) -> None:
        handler = MemoryCommandHandler(retriever=_FakeRetriever(raise_on_search=True))
        # Не должен пробросить — userbot не должен падать.
        out = handler.handle_archive("anything")
        assert "ошибка" in out.lower()

    def test_kwargs_forwarded(self) -> None:
        fake = _FakeRetriever(canned=[])
        handler = MemoryCommandHandler(retriever=fake)
        handler.handle_archive("query", chat_id="-123", top_k=3, with_context=2)
        assert fake.last_kwargs is not None
        assert fake.last_kwargs["chat_id"] == "-123"
        assert fake.last_kwargs["top_k"] == 3
        assert fake.last_kwargs["with_context"] == 2


# ---------------------------------------------------------------------------
# Защита от переполнения Telegram-лимита.
# ---------------------------------------------------------------------------


class TestMessageLimit:
    def test_long_results_are_truncated_with_notice(self) -> None:
        # 40 длинных результатов гарантированно вылезут за 4000 символов.
        canned = [_sr(mid=str(i), text=("xyz " * 200)) for i in range(40)]
        handler = MemoryCommandHandler(retriever=_FakeRetriever(canned=canned))
        out = handler.handle_archive("q")
        assert len(out) <= TELEGRAM_MESSAGE_LIMIT + 500  # header + truncation notice
        assert "не влезли" in out or "…" in out

    def test_all_filtered_graceful(self) -> None:
        """Если ни один результат не поместился — сообщение не пустое."""
        # Один гигантский результат: не поместится после header.
        canned = [_sr(mid="1", text="z" * (TELEGRAM_MESSAGE_LIMIT * 2))]
        handler = MemoryCommandHandler(retriever=_FakeRetriever(canned=canned))
        out = handler.handle_archive("q")
        # Хотя бы header и notice есть.
        assert out.strip()


# ---------------------------------------------------------------------------
# Stats.
# ---------------------------------------------------------------------------


class TestCollectStats:
    def test_missing_db(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "absent")
        handler = MemoryCommandHandler(
            archive_paths=paths,
            retriever=_FakeRetriever(),
        )
        stats = handler.collect_stats()
        assert stats.chats == 0
        assert stats.messages == 0
        assert stats.chunks == 0
        assert stats.vectors == -1
        assert stats.db_size_bytes == 0

    def test_empty_db(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        create_schema(conn)
        conn.close()

        handler = MemoryCommandHandler(archive_paths=paths, retriever=_FakeRetriever())
        stats = handler.collect_stats()
        assert stats.chats == 0
        assert stats.messages == 0
        assert stats.chunks == 0
        # vec_chunks не создана — должен вернуть -1.
        assert stats.vectors == -1
        assert stats.db_size_bytes > 0  # БД создана — файл ненулевой

    def test_populated_db(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        create_schema(conn)
        conn.execute(
            "INSERT INTO chats(chat_id, title) VALUES (?, ?);",
            ("-100", "dev"),
        )
        conn.execute(
            """
            INSERT INTO messages(message_id, chat_id, timestamp, text_redacted)
            VALUES (?, ?, ?, ?);
            """,
            ("m1", "-100", "2026-04-01T10:00:00Z", "hello"),
        )
        conn.execute(
            """
            INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                               message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            ("c1", "-100", "t0", "t1", 1, 5, "hello"),
        )
        conn.commit()
        conn.close()

        handler = MemoryCommandHandler(archive_paths=paths, retriever=_FakeRetriever())
        stats = handler.collect_stats()
        assert stats.chats == 1
        assert stats.messages == 1
        assert stats.chunks == 1
        assert stats.vectors == -1  # без vec extension таблицы нет


class TestStatsWithRealVec:
    """
    Регрессия на production-баг, пойманный e2e smoke (Session 8 post-merge):

      Embedder вписал 7 векторов → `!memory stats` продолжал показывать
      "Vectors: (sqlite-vec не подключён)". Причина: collect_stats
      открывал read-only conn, не грузил sqlite-vec extension, и
      SELECT на vec0 virtual table падал OperationalError.

    Этот тест требует реально установленный sqlite-vec — pytest.importorskip
    корректно skip'нет его если extension недоступен.
    """

    def test_stats_with_vec_chunks_populated(self, tmp_path: Path) -> None:
        import struct

        sqlite_vec = pytest.importorskip("sqlite_vec")

        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        create_schema(conn)

        # Вставляем chunk.
        conn.execute("INSERT INTO chats(chat_id, title) VALUES (?, ?);", ("-100", "dev"))
        cur = conn.execute(
            """
            INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                               message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            ("c1", "-100", "t0", "t1", 1, 5, "hello"),
        )
        chunk_rowid = cur.lastrowid

        # Создаём vec-таблицу и вставляем один вектор с тем же rowid.
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_chunks USING vec0(vector float[4] distance_metric=cosine);"
        )
        conn.execute(
            "INSERT INTO vec_chunks(rowid, vector) VALUES (?, ?);",
            (chunk_rowid, struct.pack("4f", 0.1, 0.2, 0.3, 0.4)),
        )
        conn.commit()
        conn.close()

        handler = MemoryCommandHandler(archive_paths=paths, retriever=_FakeRetriever())
        stats = handler.collect_stats()

        # КЛЮЧЕВАЯ проверка: vectors должно быть 1, не -1.
        assert stats.vectors == 1, (
            f"expected vectors=1, got {stats.vectors} — регрессия: "
            "collect_stats не загружает sqlite-vec перед COUNT(*) на vec_chunks"
        )
        assert stats.chats == 1
        assert stats.chunks == 1

    def test_stats_format_shows_vectors_when_present(self, tmp_path: Path) -> None:
        """handle_stats() рендерит число, а не '(sqlite-vec не подключён)'."""
        import struct

        sqlite_vec = pytest.importorskip("sqlite_vec")

        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        create_schema(conn)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_chunks USING vec0(vector float[4] distance_metric=cosine);"
        )
        conn.execute(
            "INSERT INTO vec_chunks(rowid, vector) VALUES (?, ?);",
            (1, struct.pack("4f", 0.0, 0.0, 0.0, 1.0)),
        )
        conn.commit()
        conn.close()

        handler = MemoryCommandHandler(archive_paths=paths, retriever=_FakeRetriever())
        out = handler.handle_stats()
        # "Vectors:       1" должно присутствовать (экранированное MarkdownV2
        # может добавить слэши, но сама цифра не скрыта).
        assert "Vectors" in out
        assert "не подключ" not in out.lower(), (
            "Регрессия: stats показывает 'не подключён' при реальных векторах"
        )


class TestHandleStats:
    def test_stats_rendered(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        create_schema(conn)
        conn.close()

        handler = MemoryCommandHandler(archive_paths=paths, retriever=_FakeRetriever())
        out = handler.handle_stats()
        assert "Memory Layer" in out
        assert "statistics".lower() in out.lower() or "статистика" in out
        assert "Chats" in out or "chats" in out.lower()

    def test_stats_shows_vec_missing(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        create_schema(conn)
        conn.close()

        handler = MemoryCommandHandler(archive_paths=paths, retriever=_FakeRetriever())
        out = handler.handle_stats()
        # vectors=-1 → показываем что не подключён.
        assert "sqlite-vec" in out or "не подключ" in out.lower()


# ---------------------------------------------------------------------------
# Утилиты форматирования.
# ---------------------------------------------------------------------------


class TestEscapeMd:
    @pytest.mark.parametrize(
        "raw, expected_contains",
        [
            ("hello", "hello"),
            ("a_b", "a\\_b"),
            ("a*b*c", "a\\*b\\*c"),
            ("(test)", "\\(test\\)"),
            ("price 1.5", "1\\.5"),
        ],
    )
    def test_escape_reserved(self, raw: str, expected_contains: str) -> None:
        assert expected_contains in _escape_md(raw)

    def test_plain_text_unchanged(self) -> None:
        assert _escape_md("hello world") == "hello world"

    def test_empty(self) -> None:
        assert _escape_md("") == ""


class TestTruncate:
    def test_below_limit(self) -> None:
        assert _truncate("short", 100) == "short"

    def test_above_limit_has_ellipsis(self) -> None:
        out = _truncate("x" * 100, 20)
        assert len(out) == 20
        assert out.endswith("…")

    def test_newlines_stripped(self) -> None:
        assert "\n" not in _truncate("a\nb\nc", 100)


class TestShortDate:
    def test_formats_iso_date(self) -> None:
        assert _short_date(datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc)) == "2026-04-01"

    def test_naive_treated_as_utc(self) -> None:
        # Не падает и даёт корректную дату.
        out = _short_date(datetime(2026, 4, 1, 12))
        assert out == "2026-04-01"


class TestFormatBytes:
    @pytest.mark.parametrize(
        "n, substring",
        [
            (0, "0 B"),
            (512, "512 B"),
            (2048, "2.0 KB"),
            (5 * 1024 * 1024, "5.0 MB"),
            (3 * 1024**3, "3.0 GB"),
        ],
    )
    def test_sizes(self, n: int, substring: str) -> None:
        assert _format_bytes(n) == substring


class TestMemoryStatsDataclass:
    def test_fields(self) -> None:
        s = MemoryStats(chats=1, messages=2, chunks=3, vectors=4, db_size_bytes=5)
        assert s.chats == 1 and s.vectors == 4

    def test_frozen(self) -> None:
        s = MemoryStats(chats=1, messages=2, chunks=3, vectors=4, db_size_bytes=5)
        with pytest.raises(Exception):
            s.chats = 99  # type: ignore[misc]


class TestFormatStatsRender:
    def test_vectors_present(self) -> None:
        out = _format_stats(
            MemoryStats(chats=2, messages=200, chunks=40, vectors=40, db_size_bytes=1024)
        )
        assert "40" in out
        # Не должно быть сообщения про отсутствие vec.
        assert "не подключ" not in out.lower()

    def test_vectors_missing(self) -> None:
        out = _format_stats(MemoryStats(chats=0, messages=0, chunks=0, vectors=-1, db_size_bytes=0))
        assert "не подключ" in out.lower() or "sqlite-vec" in out
