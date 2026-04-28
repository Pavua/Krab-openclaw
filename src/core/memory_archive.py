"""
SQLite-схема для Memory Layer (Track E), DDL-only слой.

В этом модуле нет бизнес-логики retrieval'а — только:
  1. Создание схемы в чистой SQLite БД.
  2. Версионирование схемы через `meta.schema_version`.
  3. Открытие подключения с правильными PRAGMA (WAL, foreign_keys, etc.).
  4. Выставление file-level permissions на archive.db и директорию.

Индексация, парсинг JSON-экспорта и инкрементальные insert'ы — в отдельных
модулях Phase 1 / Phase 4 (`bootstrap_memory.py`, `memory_indexer_worker.py`).

Таблицы:
  - `meta`            — key-value метаданные (schema_version, created_at, owner_id)
  - `chats`           — справочник чатов (chat_id PK)
  - `messages`        — сырые сообщения (text УЖЕ redacted после PII scrubber)
  - `chunks`          — группированные разговорные нити
  - `chunk_messages`  — many-to-many между chunks и messages (для будущего)
  - `indexer_state`   — watermark инкрементальной индексации (last processed msg)

FTS5:
  - `messages_fts`    — FTS5 content-less table, индексирует `chunks.text`

Векторный слой (`vec_chunks`) НЕ создаётся здесь — это требует `sqlite-vec`
extension, которую мы загрузим в Phase 2 (retrieval). DDL vec-таблицы
выносится в отдельную функцию `create_vec_table()` с graceful fallback.

Тестируем на `:memory:` — без реальной файловой системы.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Константы.
# ---------------------------------------------------------------------------

#: Бампается при breaking-change схемы. Миграции пишем в тот момент как
#: реальная БД появится (пока схема ещё не production).
ARCHIVE_SCHEMA_VERSION = 1

DEFAULT_ARCHIVE_DIR = Path("~/.openclaw/krab_memory").expanduser()
DEFAULT_ARCHIVE_PATH = DEFAULT_ARCHIVE_DIR / "archive.db"


# ---------------------------------------------------------------------------
# DDL statements.
# ---------------------------------------------------------------------------

# Используем многострочные f-free SQL, чтобы легко grep'ать и перекладывать
# в миграционные файлы позже.

_DDL_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
"""

_DDL_CHATS = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id         TEXT PRIMARY KEY,          -- Telegram chat_id как строка
    title           TEXT,
    chat_type       TEXT,                      -- 'private' | 'group' | 'supergroup' | 'channel'
    last_indexed_at TEXT,                      -- ISO-8601 UTC
    message_count   INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
"""

_DDL_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    message_id     TEXT NOT NULL,              -- Telegram message_id (str)
    chat_id        TEXT NOT NULL,
    sender_id      TEXT,
    timestamp      TEXT NOT NULL,              -- ISO-8601 UTC
    text_redacted  TEXT NOT NULL,              -- уже после PII scrubber
    reply_to_id    TEXT,                       -- message_id родителя, если есть
    PRIMARY KEY (chat_id, message_id),
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
) WITHOUT ROWID;
"""

_DDL_MESSAGES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);",
]

_DDL_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    -- INTEGER PRIMARY KEY становится алиасом rowid; это обязательно
    -- для FTS5 external content (content_rowid='id').
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id       TEXT NOT NULL UNIQUE,       -- stable hash(chat_id + start_msg_id)
    chat_id        TEXT NOT NULL,
    start_ts       TEXT NOT NULL,              -- ISO-8601 UTC
    end_ts         TEXT NOT NULL,
    message_count  INTEGER NOT NULL,
    char_len       INTEGER NOT NULL,
    text_redacted  TEXT NOT NULL,              -- объединённый текст chunk'а
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);
"""

_DDL_CHUNKS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_chunks_chat_ts ON chunks(chat_id, start_ts);",
    "CREATE INDEX IF NOT EXISTS idx_chunks_chunk_id ON chunks(chunk_id);",
]

_DDL_CHUNK_MESSAGES = """
CREATE TABLE IF NOT EXISTS chunk_messages (
    chunk_id    TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    PRIMARY KEY (chunk_id, message_id),
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id, message_id)
        REFERENCES messages(chat_id, message_id) ON DELETE CASCADE
) WITHOUT ROWID;
"""

# FTS5 с external content: индексируется `chunks.text_redacted`, но сами
# документы храним в таблице `chunks` — это экономит место и упрощает обновления.
_DDL_MESSAGES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text_redacted,
    content='chunks',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
"""

_DDL_INDEXER_STATE = """
CREATE TABLE IF NOT EXISTS indexer_state (
    chat_id         TEXT PRIMARY KEY,
    last_message_id TEXT NOT NULL,
    last_processed_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
) WITHOUT ROWID;
"""

# C7 Memory Phase 2: metadata о текущей embedding-модели для vec_chunks.
# Позволяет _ensure_connection() понять, что модель поменялась, и
# автоматически упасть в FTS-only режим пока не будет rebuild_all().
# Ключи: "model_name", "model_dim", "indexed_at".
_DDL_VEC_CHUNKS_META = """
CREATE TABLE IF NOT EXISTS vec_chunks_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
"""

# Feature A: Successful Response Retrieval Boost.
# Sidecar-таблица учёта positive/negative реакций на конкретные ответы Краба.
# Ключ — (chat_id, message_id) ответа Краба; positive_count/negative_count
# обновляются при каждой реакции/ack/удалении. Используется retrieval'ом
# (memory_hybrid_reranker) для буста чанков, содержащих "удачные" ответы.
_DDL_RESPONSE_FEEDBACK = """
CREATE TABLE IF NOT EXISTS response_feedback (
    chat_id         TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    positive_count  INTEGER NOT NULL DEFAULT 0,
    negative_count  INTEGER NOT NULL DEFAULT 0,
    last_updated_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
) WITHOUT ROWID;
"""

_DDL_RESPONSE_FEEDBACK_INDEXES = [
    # Индекс по message_id ускоряет lookup при boost-применении (JOIN с chunk_messages).
    "CREATE INDEX IF NOT EXISTS idx_response_feedback_message ON response_feedback(message_id);",
]


# ---------------------------------------------------------------------------
# Публичный API.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchivePaths:
    """Пути к БД и директории. `default()` возвращает канонический путь."""

    db: Path
    dir: Path

    @classmethod
    def default(cls) -> "ArchivePaths":
        return cls(db=DEFAULT_ARCHIVE_PATH, dir=DEFAULT_ARCHIVE_DIR)

    @classmethod
    def under(cls, directory: Path) -> "ArchivePaths":
        """Кастомный путь (для тестов и нестандартных сетапов)."""
        directory = Path(directory)
        return cls(db=directory / "archive.db", dir=directory)


def create_schema(conn: sqlite3.Connection) -> None:
    """
    Создаёт всю схему (таблицы + FTS + индексы) в существующем подключении.
    Идемпотентна: повторный вызов ничего не сломает (все CREATE — IF NOT EXISTS).
    Пишет/обновляет `meta.schema_version` в рамках транзакции.
    """
    cur = conn.cursor()

    # Базовые PRAGMA (безопасно вызывать много раз).
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("PRAGMA journal_mode = WAL;")

    # DDL в транзакции — если что-то упадёт, БД останется целой.
    try:
        cur.execute("BEGIN;")
        for stmt in (
            _DDL_META,
            _DDL_CHATS,
            _DDL_MESSAGES,
            *_DDL_MESSAGES_INDEXES,
            _DDL_CHUNKS,
            *_DDL_CHUNKS_INDEXES,
            _DDL_CHUNK_MESSAGES,
            _DDL_MESSAGES_FTS,
            _DDL_INDEXER_STATE,
            _DDL_VEC_CHUNKS_META,
            _DDL_RESPONSE_FEEDBACK,
            *_DDL_RESPONSE_FEEDBACK_INDEXES,
        ):
            cur.execute(stmt)

        # meta.schema_version + meta.created_at (INSERT OR IGNORE, не перетирает).
        cur.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?);",
            ("schema_version", str(ARCHIVE_SCHEMA_VERSION)),
        )
        cur.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?);",
            (
                "created_at",
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            ),
        )
        # Если версия уже была и не совпадает — это сигнал к миграции,
        # но пока только фиксируем факт в meta.
        cur.execute(
            """
            UPDATE meta SET value = ?
            WHERE key = 'schema_version' AND value != ?;
            """,
            (str(ARCHIVE_SCHEMA_VERSION), str(ARCHIVE_SCHEMA_VERSION)),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def open_archive(
    paths: ArchivePaths | None = None,
    read_only: bool = False,
    create_if_missing: bool = True,
) -> sqlite3.Connection:
    """
    Открывает (или создаёт) archive.db.

    Args:
        paths: пути. None → ArchivePaths.default().
        read_only: если True, открываем в read-only режиме (?mode=ro).
        create_if_missing: если False и файла нет — FileNotFoundError.

    Returns:
        sqlite3.Connection с включёнными foreign_keys и WAL.
    """
    paths = paths or ArchivePaths.default()

    if not paths.db.exists():
        if not create_if_missing:
            raise FileNotFoundError(f"Archive DB not found: {paths.db}")
        paths.dir.mkdir(parents=True, exist_ok=True)

    if read_only:
        uri = f"file:{paths.db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(paths.db)

    conn.execute("PRAGMA foreign_keys = ON;")
    # busy_timeout=30000 ms (30 sec) — graceful retry на concurrent writers
    # вместо immediate `database is locked` fail. Defense in depth поверх
    # WAL journal_mode (который сам по себе уменьшает write-write contention).
    # Закрывает Session 24 finding: db_lock_monitor pragma_baseline показал
    # busy_timeout=0 — ноль defensive поведения при race conditions.
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def enforce_archive_permissions(paths: ArchivePaths | None = None) -> None:
    """
    Применяет chmod 600 к БД и chmod 700 к директории.
    Защита на случай "кто-то залез в $HOME без root'а" (root'а на macOS
    всё равно не остановит, но это privacy-by-default).
    """
    paths = paths or ArchivePaths.default()
    if paths.dir.exists():
        os.chmod(paths.dir, 0o700)
    if paths.db.exists():
        os.chmod(paths.db, 0o600)


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    """Читает schema_version из meta. Возвращает None если таблицы нет."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version';").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def ensure_response_feedback_table(conn: sqlite3.Connection) -> bool:
    """Lazy CREATE TABLE для response_feedback (Feature A boost).

    Используется для БД, которые были созданы до добавления feature A —
    основной create_schema идемпотентен, но эта функция позволяет hook'ам
    feedback_tracker'а вызывать её самостоятельно без полного create_schema.

    Возвращает True при успехе, False при ошибке (graceful degradation).
    """
    try:
        cur = conn.cursor()
        cur.execute(_DDL_RESPONSE_FEEDBACK)
        for stmt in _DDL_RESPONSE_FEEDBACK_INDEXES:
            cur.execute(stmt)
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def record_response_feedback(
    conn: sqlite3.Connection,
    chat_id: str,
    message_id: str,
    *,
    positive_delta: int = 0,
    negative_delta: int = 0,
) -> bool:
    """UPSERT строки feedback'а: increments positive/negative counters.

    Args:
        chat_id: chat_id Krab-сообщения (str для совместимости со схемой).
        message_id: message_id Krab-сообщения (str).
        positive_delta: на сколько увеличить positive_count (>=0).
        negative_delta: на сколько увеличить negative_count (>=0).

    Returns:
        True при успехе, False если таблица недоступна / ошибка.
    """
    if positive_delta < 0 or negative_delta < 0:
        return False
    if positive_delta == 0 and negative_delta == 0:
        return False
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    try:
        conn.execute(
            """
            INSERT INTO response_feedback
                (chat_id, message_id, positive_count, negative_count, last_updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                positive_count = positive_count + excluded.positive_count,
                negative_count = negative_count + excluded.negative_count,
                last_updated_at = excluded.last_updated_at;
            """,
            (str(chat_id), str(message_id), int(positive_delta), int(negative_delta), now_iso),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def fetch_response_feedback_for_chunks(
    conn: sqlite3.Connection,
    chunk_ids: list[str],
) -> dict[str, tuple[int, int]]:
    """Возвращает {chunk_id: (positive_sum, negative_sum)} для заданных chunks.

    Агрегирует через JOIN chunk_messages → response_feedback. Если таблицы
    response_feedback нет / ошибка / chunks пуст — возвращает {} (graceful).
    """
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    try:
        cur = conn.execute(
            f"""
            SELECT cm.chunk_id,
                   COALESCE(SUM(rf.positive_count), 0) AS pos,
                   COALESCE(SUM(rf.negative_count), 0) AS neg
            FROM chunk_messages AS cm
            JOIN response_feedback AS rf
              ON rf.chat_id = cm.chat_id AND rf.message_id = cm.message_id
            WHERE cm.chunk_id IN ({placeholders})
            GROUP BY cm.chunk_id;
            """,
            list(chunk_ids),
        )
        return {row[0]: (int(row[1]), int(row[2])) for row in cur.fetchall()}
    except sqlite3.OperationalError:
        return {}


def list_tables(conn: sqlite3.Connection) -> list[str]:
    """Возвращает все таблицы/виртуальные таблицы (для тестов/диагностики)."""
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type IN ('table','virtual table')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name;
        """
    ).fetchall()
    return [r[0] for r in rows]
