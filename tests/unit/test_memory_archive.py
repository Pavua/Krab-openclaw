"""
Unit-тесты схемы SQLite для Memory Layer (DDL-only слой).

Работают на `:memory:` + tmp-файлах — не трогают реальный ~/.openclaw.

Покрывают:
  - create_schema поднимает все таблицы + индексы + FTS;
  - идемпотентность (повторный вызов — no-op);
  - meta.schema_version записан правильно;
  - FTS5 доступен для SELECT/INSERT;
  - foreign keys каскадируют удаление;
  - open_archive создаёт директорию / read_only / create_if_missing;
  - permissions на файл и директорию.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.memory_archive import (
    ARCHIVE_SCHEMA_VERSION,
    ArchivePaths,
    create_schema,
    enforce_archive_permissions,
    get_schema_version,
    list_tables,
    open_archive,
)

# ---------------------------------------------------------------------------
# :memory: для unit-логики.
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn() -> sqlite3.Connection:
    """Свежий in-memory SQLite со схемой."""
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Структура схемы.
# ---------------------------------------------------------------------------

class TestSchemaStructure:
    def test_all_expected_tables_created(self, mem_conn: sqlite3.Connection) -> None:
        tables = set(list_tables(mem_conn))
        expected = {
            "meta",
            "chats",
            "messages",
            "chunks",
            "chunk_messages",
            "messages_fts",
            "indexer_state",
        }
        missing = expected - tables
        assert not missing, f"missing tables: {missing}"

    def test_schema_version_recorded(self, mem_conn: sqlite3.Connection) -> None:
        version = get_schema_version(mem_conn)
        assert version == ARCHIVE_SCHEMA_VERSION

    def test_schema_version_on_empty_db(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            assert get_schema_version(conn) is None
        finally:
            conn.close()

    def test_chats_columns(self, mem_conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in mem_conn.execute("PRAGMA table_info(chats);")}
        assert {
            "chat_id",
            "title",
            "chat_type",
            "last_indexed_at",
            "message_count",
        } <= cols

    def test_messages_composite_primary_key(
        self, mem_conn: sqlite3.Connection
    ) -> None:
        # Primary key = (chat_id, message_id). Попытка вставить дубликат падает.
        mem_conn.execute(
            "INSERT INTO chats(chat_id, title) VALUES (?, ?);", ("-100", "t")
        )
        mem_conn.execute(
            """
            INSERT INTO messages
                (message_id, chat_id, timestamp, text_redacted)
            VALUES (?, ?, ?, ?);
            """,
            ("1", "-100", "2026-04-15T12:00:00Z", "hello"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            mem_conn.execute(
                """
                INSERT INTO messages
                    (message_id, chat_id, timestamp, text_redacted)
                VALUES (?, ?, ?, ?);
                """,
                ("1", "-100", "2026-04-15T12:00:01Z", "dup"),
            )


# ---------------------------------------------------------------------------
# Идемпотентность.
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_create_schema_is_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            create_schema(conn)
            create_schema(conn)
            create_schema(conn)
            # Все таблицы на месте, ничего не упало.
            assert get_schema_version(conn) == ARCHIVE_SCHEMA_VERSION
            assert "messages" in list_tables(conn)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# FTS5.
# ---------------------------------------------------------------------------

class TestFTS5:
    def test_fts_insert_and_search(self, mem_conn: sqlite3.Connection) -> None:
        """Смоук — FTS индекс принимает вставки и отвечает на MATCH."""
        mem_conn.execute(
            "INSERT INTO chats(chat_id, title) VALUES (?, ?);", ("-100", "dev")
        )
        mem_conn.execute(
            """
            INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                               message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "c1",
                "-100",
                "2026-04-15T12:00:00Z",
                "2026-04-15T12:05:00Z",
                3,
                50,
                "привет мир hello world krab memory",
            ),
        )
        # Поскольку external content FTS — синхронизация руками:
        mem_conn.execute(
            "INSERT INTO messages_fts(rowid, text_redacted) "
            "SELECT rowid, text_redacted FROM chunks WHERE chunk_id='c1';"
        )
        results = mem_conn.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'krab';"
        ).fetchall()
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Foreign keys cascade.
# ---------------------------------------------------------------------------

class TestForeignKeyCascade:
    def test_delete_chat_cascades_to_messages_and_chunks(
        self, mem_conn: sqlite3.Connection
    ) -> None:
        mem_conn.execute(
            "INSERT INTO chats(chat_id, title) VALUES (?, ?);", ("-100", "dev")
        )
        mem_conn.execute(
            """
            INSERT INTO messages(message_id, chat_id, timestamp, text_redacted)
            VALUES (?, ?, ?, ?);
            """,
            ("m1", "-100", "2026-04-15T12:00:00Z", "x"),
        )
        mem_conn.execute(
            """
            INSERT INTO chunks(chunk_id, chat_id, start_ts, end_ts,
                               message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            ("c1", "-100", "t0", "t1", 1, 1, "x"),
        )
        mem_conn.commit()

        # Удаляем чат → всё связанное должно исчезнуть.
        mem_conn.execute("DELETE FROM chats WHERE chat_id = '-100';")
        mem_conn.commit()

        assert (
            mem_conn.execute("SELECT COUNT(*) FROM messages;").fetchone()[0] == 0
        )
        assert mem_conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# open_archive + ArchivePaths.
# ---------------------------------------------------------------------------

class TestOpenArchive:
    def test_open_creates_file(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "memdir")
        assert not paths.db.exists()

        conn = open_archive(paths)
        try:
            create_schema(conn)
        finally:
            conn.close()

        assert paths.db.exists()
        assert paths.dir.exists()

    def test_read_only_refuses_creation(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "absent")
        # DB ещё нет → в read-only открытие должно упасть на этапе подключения.
        with pytest.raises((sqlite3.OperationalError, FileNotFoundError)):
            conn = open_archive(paths, read_only=True, create_if_missing=False)
            conn.close()

    def test_missing_file_without_create_raises(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "nodir")
        with pytest.raises(FileNotFoundError):
            open_archive(paths, create_if_missing=False)

    def test_default_paths_are_under_openclaw(self) -> None:
        defaults = ArchivePaths.default()
        assert defaults.db.name == "archive.db"
        assert "krab_memory" in str(defaults.dir)
        assert str(defaults.dir).endswith("krab_memory")


# ---------------------------------------------------------------------------
# Permissions.
# ---------------------------------------------------------------------------

class TestPermissions:
    def test_enforce_sets_600_and_700(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        try:
            create_schema(conn)
        finally:
            conn.close()

        enforce_archive_permissions(paths)

        db_mode = paths.db.stat().st_mode & 0o777
        dir_mode = paths.dir.stat().st_mode & 0o777
        assert db_mode == 0o600, f"expected 600, got {db_mode:o}"
        assert dir_mode == 0o700, f"expected 700, got {dir_mode:o}"

    def test_enforce_is_idempotent(self, tmp_path: Path) -> None:
        paths = ArchivePaths.under(tmp_path / "mem")
        conn = open_archive(paths)
        try:
            create_schema(conn)
        finally:
            conn.close()

        # Вызываем дважды — должно работать.
        enforce_archive_permissions(paths)
        enforce_archive_permissions(paths)

        db_mode = paths.db.stat().st_mode & 0o777
        assert db_mode == 0o600


# ---------------------------------------------------------------------------
# Indexes.
# ---------------------------------------------------------------------------

class TestIndexes:
    def test_expected_indexes_exist(self, mem_conn: sqlite3.Connection) -> None:
        rows = mem_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_%';"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "idx_messages_chat_ts" in names
        assert "idx_messages_sender" in names
        assert "idx_chunks_chat_ts" in names


# ---------------------------------------------------------------------------
# Meta round-trip.
# ---------------------------------------------------------------------------

class TestMeta:
    def test_created_at_populated(self, mem_conn: sqlite3.Connection) -> None:
        row = mem_conn.execute(
            "SELECT value FROM meta WHERE key='created_at';"
        ).fetchone()
        assert row is not None
        # ISO-8601 UTC с суффиксом Z.
        assert row[0].endswith("Z")


# ---------------------------------------------------------------------------
# Session 11: error paths + read-only mode + malformed meta version.
# ---------------------------------------------------------------------------


class TestCreateSchemaRollback:
    """
    При sqlite3.Error в середине create_schema должен прийти rollback+raise.
    """

    def test_rollback_on_ddl_error(self) -> None:
        """
        Оборачиваем sqlite3.Connection в класс, чей cursor().execute падает
        на известном DDL-шаге. После rollback исключение пробрасывается наружу.
        """

        class FailingCursor:
            def __init__(self, real_cur: sqlite3.Cursor) -> None:
                self._real = real_cur

            def execute(self, sql: str, *args, **kwargs):
                # Ломаемся на DDL таблицы chunks — после meta уже создана,
                # но BEGIN транзакции требует rollback.
                if "CREATE TABLE IF NOT EXISTS chunks" in sql:
                    raise sqlite3.Error("simulated DDL error")
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name: str):
                return getattr(self._real, name)

        class WrappedConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real
                self.rollback_called = False

            def cursor(self) -> FailingCursor:
                return FailingCursor(self._real.cursor())

            def rollback(self) -> None:
                self.rollback_called = True
                return self._real.rollback()

            def commit(self) -> None:
                return self._real.commit()

            def __getattr__(self, name: str):
                return getattr(self._real, name)

        real_conn = sqlite3.connect(":memory:")
        try:
            wrapped = WrappedConn(real_conn)
            with pytest.raises(sqlite3.Error, match="simulated DDL error"):
                create_schema(wrapped)  # type: ignore[arg-type]
            assert wrapped.rollback_called, "rollback должен быть вызван"
        finally:
            real_conn.close()


class TestReadOnlyOpen:
    def test_read_only_on_existing_db(self, tmp_path: Path) -> None:
        """
        open_archive(read_only=True) должен использовать `file:...?mode=ro` URI
        и успешно открыться на уже существующей БД.
        """
        paths = ArchivePaths.under(tmp_path / "ro")
        # Сначала создадим БД.
        conn = open_archive(paths)
        try:
            create_schema(conn)
        finally:
            conn.close()

        # Теперь открываем в read-only — запись должна провалиться.
        ro_conn = open_archive(paths, read_only=True, create_if_missing=False)
        try:
            with pytest.raises(sqlite3.OperationalError):
                ro_conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('test', 'x');"
                )
        finally:
            ro_conn.close()


class TestSchemaVersionParsing:
    """
    get_schema_version устойчив к мусору в meta.value и отсутствующей строке.
    """

    def test_none_when_row_missing(self, mem_conn: sqlite3.Connection) -> None:
        # Удалим строку schema_version — get_schema_version должен вернуть None.
        mem_conn.execute("DELETE FROM meta WHERE key='schema_version';")
        mem_conn.commit()
        assert get_schema_version(mem_conn) is None

    def test_none_when_value_not_int(self, mem_conn: sqlite3.Connection) -> None:
        # Перезапишем value на мусор — parse int должен упасть в ValueError.
        mem_conn.execute(
            "UPDATE meta SET value='not-a-number' WHERE key='schema_version';"
        )
        mem_conn.commit()
        assert get_schema_version(mem_conn) is None

    def test_none_when_value_typeerror(self, mem_conn: sqlite3.Connection) -> None:
        """
        Путь TypeError — подсовываем значение, которое int() отказывается
        парсить не по ValueError, а по TypeError (float с '.5' → ValueError,
        а вот bytes-like не валидный текст → часть того же ветвления).

        Надёжный способ: подменим fetchone так, чтобы вернуть объект, у которого
        row[0] — это не-строка, не-bytes, не-число (например, список).
        """

        class WrappedConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def execute(self, sql: str, *args, **kwargs):
                real_res = self._real.execute(sql, *args, **kwargs)
                if "schema_version" in sql:
                    class FakeResult:
                        def fetchone(self):  # noqa: PLR6301
                            return ([],)  # список → TypeError в int()
                    return FakeResult()
                return real_res

            def __getattr__(self, name: str):
                return getattr(self._real, name)

        wrapped = WrappedConn(mem_conn)
        assert get_schema_version(wrapped) is None  # type: ignore[arg-type]


class TestEnforcePermissionsEdges:
    def test_enforce_on_missing_paths_noop(self, tmp_path: Path) -> None:
        """
        Если dir/db ещё не созданы, enforce не должен падать на OSError —
        просто пропустить chmod без исключения.
        """
        paths = ArchivePaths.under(tmp_path / "never_created")
        # ни директория, ни файл не существуют — функция должна тихо пройти.
        enforce_archive_permissions(paths)
        # И dir, и db по-прежнему отсутствуют.
        assert not paths.dir.exists()
        assert not paths.db.exists()
