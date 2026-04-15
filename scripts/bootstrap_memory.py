#!/usr/bin/env python3
"""
bootstrap_memory.py — one-shot CLI для Phase 1 ingestion Memory Layer.

Назначение
==========
Прогоняет Telegram Desktop JSON-экспорт через pipeline:
  1. Парсинг экспорта (multi-chat или single-chat формат);
  2. Фильтр чатов через ``MemoryWhitelist`` (privacy-by-default);
  3. Нормализация текста (поддержка array/string вариантов Telegram);
  4. Редакция PII через ``PIIRedactor`` (карты Luhn, API-ключи, emails и т.д.);
  5. Группировка сообщений в ``Chunk``-и через ``chunk_messages``;
  6. Insert в ``archive.db`` (chats/messages/chunks/chunk_messages) + FTS5 index;
  7. Применение ``enforce_archive_permissions()`` после записи.

Скрипт идемпотентен: повторный запуск НЕ дублирует данные. Для чанков
используется стратегия "delete then insert" per chat (MVP для Phase 1).
Для сообщений и чатов используется INSERT OR IGNORE.

НЕ трогает:
  - Model2Vec эмбеддинги (отдельный pass Phase 2);
  - sqlite-vec vector-таблицы (тоже Phase 2);
  - инкрементальную watermark-логику (Phase 4).

Использование
=============
::

    python scripts/bootstrap_memory.py [OPTIONS]

Опции::

    --export PATH      Путь к Telegram Export JSON.
    --db PATH          Путь к archive.db.
    --dry-run          Не пишет в БД, только stats + первые 10 chunks.
    --limit N          Только первые N сообщений (smoke-test).
    --whitelist PATH   Путь к whitelist.json.
    --allow-all        Игнорировать whitelist (DEV ONLY).
    -v, --verbose      Подробный лог по каждому batch.

Exit-коды::

    0  успех
    1  I/O ошибка (файл не найден / permission denied)
    2  ошибка валидации входных данных
    3  runtime error (sqlite / unexpected)

Пример
------
::

    python scripts/bootstrap_memory.py \
        --export ~/Downloads/tg_export/result.json \
        --db ~/.openclaw/krab_memory/archive.db \
        --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Добавляем project root в sys.path чтобы импорт src.* работал при прямом
# запуске скрипта (а не через `python -m`). Это важно т.к. скрипт может
# запускаться пользователем вручную.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from structlog import get_logger  # noqa: E402

from src.core.memory_archive import (  # noqa: E402
    ArchivePaths,
    create_schema,
    enforce_archive_permissions,
    open_archive,
)
from src.core.memory_chunking import Chunk, Message, chunk_messages  # noqa: E402
from src.core.memory_pii_redactor import PIIRedactor, RedactionStats  # noqa: E402
from src.core.memory_whitelist import MemoryWhitelist  # noqa: E402

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Константы.
# ---------------------------------------------------------------------------

#: Размер batch'а для транзакционных insert'ов (chunks).
DEFAULT_BATCH_SIZE = 1024

#: Сообщения Telegram с type="service" не индексируем (joins, pins, create).
_SKIP_MESSAGE_TYPES = frozenset({"service"})

#: Маппинг Telegram-export chat.type → нормализованный chat_type в БД.
_CHAT_TYPE_MAP = {
    "personal_chat": "private",
    "bot_chat": "private",
    "private_group": "group",
    "public_group": "group",
    "private_supergroup": "supergroup",
    "public_supergroup": "supergroup",
    "private_channel": "channel",
    "public_channel": "channel",
}

#: Дефолтный путь экспорта (согласно Phase 1 plan).
_DEFAULT_EXPORT = Path(
    "~/Downloads/tg_export_for_krab/p0lrd_whitelist/result.json"
).expanduser()


# ---------------------------------------------------------------------------
# Модели результатов.
# ---------------------------------------------------------------------------

@dataclass
class BootstrapStats:
    """Агрегированная статистика одного прогона."""

    #: Сколько сообщений прочитали из экспорта (до любых фильтров).
    messages_read: int = 0
    #: Сколько прошло фильтры (service/empty-text/whitelist) и пошло в pipeline.
    messages_processed: int = 0
    #: Сколько сообщений пропущено, по причинам.
    messages_skipped: dict[str, int] = field(default_factory=dict)
    #: Сколько chunks создано.
    chunks_created: int = 0
    #: Сколько chats проиндексировано (allow).
    chats_indexed: int = 0
    #: Сколько chats отфильтровано (deny/no_match).
    chats_skipped: int = 0
    #: Статистика PII-редакций (по категориям).
    pii_stats: RedactionStats = field(default_factory=RedactionStats)
    #: dry_run=True → первые 10 отредактированных chunks для превью.
    preview_chunks: list[dict[str, Any]] = field(default_factory=list)

    def bump_skipped(self, reason: str) -> None:
        """Инкрементирует счётчик skip-причины."""
        self.messages_skipped[reason] = self.messages_skipped.get(reason, 0) + 1

    def merge_pii(self, other: RedactionStats) -> None:
        """Мердж PII-статистики из одного сообщения."""
        self.pii_stats = self.pii_stats.merged_with(other)

    def as_dict(self) -> dict[str, Any]:
        """Сериализация для финального отчёта."""
        return {
            "messages_read": self.messages_read,
            "messages_processed": self.messages_processed,
            "messages_skipped": dict(self.messages_skipped),
            "chunks_created": self.chunks_created,
            "chats_indexed": self.chats_indexed,
            "chats_skipped": self.chats_skipped,
            "pii_redactions": dict(self.pii_stats.counts),
            "pii_total": self.pii_stats.total,
        }


# ---------------------------------------------------------------------------
# Текстовая нормализация.
# ---------------------------------------------------------------------------

def extract_text(msg: dict[str, Any]) -> str:
    """
    Нормализует поле ``text`` Telegram-сообщения в плоскую строку.

    Telegram Desktop export хранит текст двумя способами:

    * plain string — "привет мир";
    * массив — ``["смотри ", {"type": "code", "text": "foo()"}, " в модуле"]``.

    Массив встречается когда в сообщении есть форматирование
    (code/bold/italic/link/email/mention/...). Во втором случае нужно
    собрать текст back в строку, сохраняя порядок.

    Если ``text`` пустая строка или пустой массив — возвращаем "".
    Если ``text`` отсутствует, но есть ``text_entities`` — fallback на них.
    """
    raw = msg.get("text")

    # Быстрый path: обычная строка.
    if isinstance(raw, str):
        return raw

    # Массив сегментов.
    if isinstance(raw, list):
        parts: list[str] = []
        for seg in raw:
            if isinstance(seg, str):
                parts.append(seg)
            elif isinstance(seg, dict):
                # Сегмент с форматированием — берём .text (гарантированно есть).
                parts.append(str(seg.get("text", "")))
            # Иные типы (None, int) — игнорируем.
        return "".join(parts)

    # Fallback на text_entities (встречается редко, но покрываем).
    entities = msg.get("text_entities")
    if isinstance(entities, list):
        return "".join(str(e.get("text", "")) for e in entities if isinstance(e, dict))

    return ""


# ---------------------------------------------------------------------------
# Парсинг экспорта.
# ---------------------------------------------------------------------------

def detect_export_format(data: dict[str, Any]) -> str:
    """
    Возвращает "multi" | "single" | "unknown".

    Эвристика:
      * multi: есть ключ ``chats`` с вложенным ``list``;
      * single: есть ``messages`` на top-level.
    """
    if isinstance(data.get("chats"), dict) and isinstance(
        data["chats"].get("list"), list
    ):
        return "multi"
    if isinstance(data.get("messages"), list):
        return "single"
    return "unknown"


def iter_chats(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Нормализованный итератор чатов из экспорта (любой формат).

    Yields:
        dict каждого чата (c ключами name/type/id/messages).
    """
    fmt = detect_export_format(data)

    if fmt == "multi":
        yield from data["chats"]["list"]
    elif fmt == "single":
        yield data
    else:
        # Неизвестный формат — без yield, caller получит пустую итерацию.
        # Exit code 2 выставляется в run_bootstrap по итогам.
        return


def _parse_timestamp(msg: dict[str, Any]) -> datetime | None:
    """
    Парсит timestamp сообщения. Приоритет: ``date_unixtime`` (наиболее точный).

    Возвращает aware datetime в UTC. None — если оба поля отсутствуют/битые.
    """
    ts_raw = msg.get("date_unixtime")
    if ts_raw:
        try:
            return datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        except (TypeError, ValueError):
            pass

    # Fallback на ISO-строку (локальное время в экспорте Desktop, но для MVP
    # интерпретируем как naive UTC — для точного retrieval'а всё равно важна
    # относительная последовательность, а не absolute wall clock).
    date_raw = msg.get("date")
    if isinstance(date_raw, str):
        try:
            return datetime.fromisoformat(date_raw).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def _chat_title(chat: dict[str, Any]) -> str:
    """Возвращает отображаемое имя чата (fallback на str(id))."""
    name = chat.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"chat_{chat.get('id', 'unknown')}"


def _chat_type(chat: dict[str, Any]) -> str:
    """Нормализует chat.type в один из: private/group/supergroup/channel."""
    raw = str(chat.get("type") or "").strip().lower()
    return _CHAT_TYPE_MAP.get(raw, raw or "unknown")


def _is_message_empty(msg: dict[str, Any]) -> tuple[bool, str]:
    """
    Проверяет, нужно ли скипнуть сообщение.

    Returns:
        (skip?, reason) — причина нужна для stats.
    """
    msg_type = msg.get("type")
    if msg_type in _SKIP_MESSAGE_TYPES:
        return True, "service_message"

    text = extract_text(msg)
    if not text.strip():
        return True, "empty_text"

    # Обязательный id — без него нельзя PRIMARY KEY-ить.
    if msg.get("id") is None:
        return True, "missing_id"

    return False, ""


def _build_message(
    msg: dict[str, Any],
    chat_id: str,
    redactor: PIIRedactor,
    stats: BootstrapStats,
) -> Message | None:
    """
    Строит ``Message`` для chunker: парсит timestamp, redact'ит текст,
    мёрджит PII-статистику.

    Returns:
        None если timestamp невалидный (сообщение скипается на слое выше).
    """
    ts = _parse_timestamp(msg)
    if ts is None:
        stats.bump_skipped("invalid_timestamp")
        return None

    text = extract_text(msg)
    redaction = redactor.redact(text)
    stats.merge_pii(redaction.stats)

    reply_to = msg.get("reply_to_message_id")
    from_id = msg.get("from_id")

    return Message(
        message_id=str(msg["id"]),
        chat_id=chat_id,
        timestamp=ts,
        text=redaction.text,
        sender_id=str(from_id) if from_id is not None else None,
        reply_to_message_id=str(reply_to) if reply_to is not None else None,
    )


# ---------------------------------------------------------------------------
# Chunk hashing.
# ---------------------------------------------------------------------------

def _chunk_hash(chat_id: str, start_message_id: str) -> str:
    """
    Детерминированный id chunk'а: sha256(chat_id + "\\x00" + start_msg_id)[:16].

    Используем первые 16 символов hex — это 64 bit энтропии, достаточно для
    per-chat unique. Если когда-то увидим коллизию — легко сменить на [:24].
    """
    payload = f"{chat_id}\x00{start_message_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# БД-уровень.
# ---------------------------------------------------------------------------

def _ensure_chat_row(
    conn: sqlite3.Connection, chat_id: str, title: str, chat_type: str
) -> None:
    """INSERT OR IGNORE строка чата (имя/тип обновим только при первом insert'е)."""
    conn.execute(
        "INSERT OR IGNORE INTO chats (chat_id, title, chat_type, message_count) "
        "VALUES (?, ?, ?, 0);",
        (chat_id, title, chat_type),
    )


def _insert_message(conn: sqlite3.Connection, msg: Message) -> None:
    """INSERT OR IGNORE сообщение (PK: chat_id + message_id)."""
    conn.execute(
        "INSERT OR IGNORE INTO messages "
        "(message_id, chat_id, sender_id, timestamp, text_redacted, reply_to_id) "
        "VALUES (?, ?, ?, ?, ?, ?);",
        (
            msg.message_id,
            msg.chat_id,
            msg.sender_id,
            msg.timestamp.replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            msg.text,
            msg.reply_to_message_id,
        ),
    )


def _purge_chunks_for_chat(conn: sqlite3.Connection, chat_id: str) -> None:
    """
    Удаляет все chunks (+ cascade chunk_messages и FTS-строки) для чата.

    MVP-подход идемпотентности: при re-run мы полностью переcтраиваем chunking.
    FTS5 внешней контент-таблицы удаляется автоматически через ON DELETE CASCADE
    (в schema есть FK). Для явности делаем DELETE FROM messages_fts по rowid.
    """
    # Сначала узнаём rowid chunks этого чата, чтобы почистить FTS.
    rows = conn.execute(
        "SELECT id FROM chunks WHERE chat_id = ?;", (chat_id,)
    ).fetchall()
    if rows:
        placeholders = ",".join("?" for _ in rows)
        ids = [r[0] for r in rows]
        # FTS5 external-content: DELETE через 'delete' command.
        for rid in ids:
            conn.execute(
                "INSERT INTO messages_fts(messages_fts, rowid, text_redacted) "
                "VALUES ('delete', ?, ?);",
                (rid, ""),  # body игнорируется при 'delete'
            )
        conn.execute(f"DELETE FROM chunks WHERE id IN ({placeholders});", ids)
    # chunk_messages чистится каскадом через FK. Для гарантии:
    conn.execute("DELETE FROM chunk_messages WHERE chat_id = ?;", (chat_id,))


def _insert_chunk(
    conn: sqlite3.Connection, chunk: Chunk, chunk_id: str
) -> int:
    """
    Вставляет chunk + возвращает rowid. Далее по rowid синкаем FTS5.
    """
    assert chunk.start_timestamp is not None
    assert chunk.end_timestamp is not None

    cur = conn.execute(
        "INSERT INTO chunks "
        "(chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?);",
        (
            chunk_id,
            chunk.chat_id,
            chunk.start_timestamp.replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            chunk.end_timestamp.replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            len(chunk.messages),
            chunk.char_len,
            chunk.text,
        ),
    )
    rowid = cur.lastrowid
    assert rowid is not None
    return rowid


def _insert_chunk_messages(
    conn: sqlite3.Connection, chunk: Chunk, chunk_id: str
) -> None:
    """Many-to-many: chunk → messages."""
    rows = [(chunk_id, m.message_id, chunk.chat_id) for m in chunk.messages]
    conn.executemany(
        "INSERT OR IGNORE INTO chunk_messages (chunk_id, message_id, chat_id) "
        "VALUES (?, ?, ?);",
        rows,
    )


def _sync_fts(conn: sqlite3.Connection, rowid: int, text: str) -> None:
    """Добавляет chunk в FTS5 index."""
    conn.execute(
        "INSERT INTO messages_fts (rowid, text_redacted) VALUES (?, ?);",
        (rowid, text),
    )


def _update_chat_counters(conn: sqlite3.Connection, chat_id: str) -> None:
    """
    Проставляет ``chats.message_count`` и ``chats.last_indexed_at`` после
    того как мы закончили работу с чатом.
    """
    conn.execute(
        """
        UPDATE chats
        SET message_count = (SELECT COUNT(*) FROM messages WHERE chat_id = ?),
            last_indexed_at = ?
        WHERE chat_id = ?;
        """,
        (
            chat_id,
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            chat_id,
        ),
    )


# ---------------------------------------------------------------------------
# Основной pipeline.
# ---------------------------------------------------------------------------

def _process_chat(
    chat: dict[str, Any],
    conn: sqlite3.Connection | None,
    redactor: PIIRedactor,
    whitelist: MemoryWhitelist,
    allow_all: bool,
    limit: int | None,
    verbose: bool,
    dry_run: bool,
    stats: BootstrapStats,
) -> None:
    """
    Обрабатывает один чат.

    Если conn=None — значит dry_run. В этом случае пишем preview в stats.
    """
    chat_id_raw = chat.get("id")
    if chat_id_raw is None:
        log.warning("chat_without_id", chat=chat.get("name"))
        stats.chats_skipped += 1
        return

    chat_id = str(chat_id_raw)
    title = _chat_title(chat)
    ctype = _chat_type(chat)

    # Whitelist gate (можно обойти через --allow-all).
    if allow_all:
        decision_reason = "allow_all_cli"
    else:
        decision = whitelist.is_allowed(chat_id, title)
        if not decision.allowed:
            if verbose:
                log.info(
                    "chat_skipped_by_whitelist",
                    chat_id=chat_id,
                    title=title,
                    reason=decision.reason,
                )
            stats.chats_skipped += 1
            return
        decision_reason = decision.reason

    log.info(
        "chat_processing",
        chat_id=chat_id,
        title=title,
        chat_type=ctype,
        reason=decision_reason,
    )
    stats.chats_indexed += 1

    raw_messages = chat.get("messages") or []

    # Pipeline: filter → build Message → redact → chunk.
    def _iter_messages() -> Iterator[Message]:
        processed = 0
        for raw in raw_messages:
            if not isinstance(raw, dict):
                stats.bump_skipped("non_dict_entry")
                continue
            stats.messages_read += 1

            if limit is not None and processed >= limit:
                break

            skip, reason = _is_message_empty(raw)
            if skip:
                stats.bump_skipped(reason)
                continue

            built = _build_message(raw, chat_id, redactor, stats)
            if built is None:
                continue

            stats.messages_processed += 1
            processed += 1
            yield built

    # Готовим упорядоченный список (chunker ждёт chronological ASC).
    messages_list = list(_iter_messages())
    messages_list.sort(key=lambda m: m.timestamp)

    if not messages_list:
        if verbose:
            log.info("chat_empty_after_filter", chat_id=chat_id)
        return

    # БД-операции — только в non-dry-run.
    if conn is not None and not dry_run:
        _ensure_chat_row(conn, chat_id, title, ctype)
        # Idempotency: re-chunk целиком для чата.
        _purge_chunks_for_chat(conn, chat_id)

    batch_buffer: list[tuple[Chunk, str]] = []
    batch_counter = 0

    for chunk in chunk_messages(messages_list):
        if chunk.is_empty():
            continue
        # Stable id: chat + первое сообщение.
        first_msg_id = chunk.messages[0].message_id
        chunk_id = _chunk_hash(chat_id, first_msg_id)
        stats.chunks_created += 1

        if dry_run:
            if len(stats.preview_chunks) < 10:
                stats.preview_chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "chat_id": chat_id,
                        "messages": len(chunk.messages),
                        "char_len": chunk.char_len,
                        "text_preview": chunk.text[:200],
                    }
                )
            continue

        assert conn is not None  # гарантируется условием выше
        # Вставляем сообщения chunk'а (INSERT OR IGNORE).
        for msg in chunk.messages:
            _insert_message(conn, msg)

        batch_buffer.append((chunk, chunk_id))
        if len(batch_buffer) >= DEFAULT_BATCH_SIZE:
            _flush_batch(conn, batch_buffer)
            batch_counter += 1
            if verbose:
                log.info(
                    "batch_flushed",
                    chat_id=chat_id,
                    batch=batch_counter,
                    chunks=len(batch_buffer),
                )
            batch_buffer.clear()

    # Flush остатка.
    if batch_buffer and conn is not None and not dry_run:
        _flush_batch(conn, batch_buffer)
        if verbose:
            log.info(
                "batch_flushed_tail",
                chat_id=chat_id,
                chunks=len(batch_buffer),
            )

    # Обновляем счётчики чата.
    if conn is not None and not dry_run:
        _update_chat_counters(conn, chat_id)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Гарантирует, что схема создана.

    ``memory_archive.create_schema()`` открывает явный BEGIN, что конфликтует с
    неявной транзакцией Python sqlite3 если предыдущий commit не завершён.
    Проверяем наличие ``meta`` через sqlite_master: если таблицы уже есть,
    пропускаем create_schema.
    """
    # commit() если висит неявная транзакция — чтобы create_schema не упал.
    try:
        conn.commit()
    except sqlite3.Error:
        pass

    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta';"
    ).fetchone()
    if existing is not None:
        return
    create_schema(conn)


def _flush_batch(
    conn: sqlite3.Connection, batch: list[tuple[Chunk, str]]
) -> None:
    """
    Транзакционный insert batch'а chunks + chunk_messages + FTS.

    Полагаемся на неявную транзакцию Python sqlite3 (isolation_level="DEFERRED"
    по умолчанию): первый INSERT откроет BEGIN, а ``conn.commit()`` закроет.
    Если в процессе произошёл error — откатываемся в rollback().
    """
    try:
        for chunk, chunk_id in batch:
            rowid = _insert_chunk(conn, chunk, chunk_id)
            _insert_chunk_messages(conn, chunk, chunk_id)
            _sync_fts(conn, rowid, chunk.text)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Публичный entry-point.
# ---------------------------------------------------------------------------

def run_bootstrap(
    *,
    export_path: Path,
    db_path: Path | None = None,
    whitelist_path: Path | None = None,
    allow_all: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    in_memory_conn: sqlite3.Connection | None = None,
) -> BootstrapStats:
    """
    Запускает полный bootstrap pipeline.

    Args:
        export_path: путь к Telegram Export JSON.
        db_path: путь к archive.db. None → default.
        whitelist_path: путь к whitelist.json. None → default.
        allow_all: если True, игнорируем whitelist (DEV only).
        limit: макс. сообщений на чат (smoke).
        dry_run: если True — не трогаем БД.
        verbose: подробное логирование.
        in_memory_conn: подменное подключение для тестов (обходит файловую БД).

    Returns:
        BootstrapStats с агрегированными цифрами.

    Raises:
        FileNotFoundError: если export_path не существует.
        json.JSONDecodeError: если экспорт битый.
        ValueError: если формат экспорта нераспознан.
    """
    export_path = Path(export_path).expanduser()
    if not export_path.exists():
        raise FileNotFoundError(f"Export not found: {export_path}")

    log.info("bootstrap_start", export=str(export_path), dry_run=dry_run, limit=limit)

    # Парсинг JSON — обычный load, не streaming (stdlib хватает, экспорт обычно < 1GB).
    with export_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    fmt = detect_export_format(data)
    if fmt == "unknown":
        raise ValueError(
            f"Unknown export format at {export_path}: "
            "expected single-chat (.messages) or multi-chat (.chats.list)"
        )

    log.info("export_format_detected", format=fmt)

    # Whitelist (может быть пустым).
    whitelist = MemoryWhitelist(config_path=whitelist_path)

    # PII redactor (без owner-whitelist — в bootstrap'е это не настраивается
    # через CLI; настройка — в отдельной команде !memory whitelist).
    redactor = PIIRedactor()

    # Подключение к БД (если не dry_run и не test-injected).
    conn: sqlite3.Connection | None = None
    archive_paths: ArchivePaths | None = None
    if not dry_run:
        if in_memory_conn is not None:
            conn = in_memory_conn
            _ensure_schema(conn)
        else:
            archive_paths = (
                ArchivePaths.under(db_path.parent) if db_path else ArchivePaths.default()
            )
            if db_path:
                # Кастомный точный путь: переопределяем db поле.
                archive_paths = ArchivePaths(db=db_path, dir=db_path.parent)
            conn = open_archive(archive_paths, create_if_missing=True)
            _ensure_schema(conn)

    stats = BootstrapStats()

    try:
        # Обход всех чатов.
        for chat in iter_chats(data):
            _process_chat(
                chat=chat,
                conn=conn,
                redactor=redactor,
                whitelist=whitelist,
                allow_all=allow_all,
                limit=limit,
                verbose=verbose,
                dry_run=dry_run,
                stats=stats,
            )
    finally:
        # Применяем permissions только когда писали на диск.
        if (
            conn is not None
            and not dry_run
            and archive_paths is not None
            and in_memory_conn is None
        ):
            conn.commit()
            try:
                enforce_archive_permissions(archive_paths)
            except OSError as exc:
                # Chmod на shared-volume может упасть — не критично, логируем.
                log.warning("enforce_permissions_failed", error=str(exc))
        # Подключение, созданное нами — закрываем. Test-injected — НЕ трогаем.
        if conn is not None and in_memory_conn is None:
            conn.close()

    log.info("bootstrap_done", **stats.as_dict())
    return stats


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap_memory",
        description="One-shot Memory Layer ingestion from Telegram Export JSON.",
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=_DEFAULT_EXPORT,
        help=f"Path to Telegram Export JSON (default: {_DEFAULT_EXPORT})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to archive.db (default: ~/.openclaw/krab_memory/archive.db)",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=None,
        help="Path to whitelist.json (default: ~/.openclaw/krab_memory/whitelist.json)",
    )
    parser.add_argument(
        "--allow-all",
        action="store_true",
        help="Ignore whitelist (DEV ONLY — не используй в prod).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не писать в БД; показать stats и preview первых 10 chunks.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Обработать только первые N сообщений на чат (smoke-test).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Подробный лог по каждому batch.",
    )
    return parser


def _print_report(stats: BootstrapStats, dry_run: bool) -> None:
    """Человекочитаемый отчёт для CLI."""
    print("=" * 60)
    print("Memory Layer Bootstrap — Report")
    print("=" * 60)
    print(f"Mode:                {'DRY-RUN' if dry_run else 'WRITE'}")
    print(f"Messages read:       {stats.messages_read}")
    print(f"Messages processed:  {stats.messages_processed}")
    print(f"Chats indexed:       {stats.chats_indexed}")
    print(f"Chats skipped:       {stats.chats_skipped}")
    print(f"Chunks created:      {stats.chunks_created}")
    print(f"PII redactions:      {stats.pii_stats.total}")

    if stats.messages_skipped:
        print("\nMessages skipped (by reason):")
        for reason, count in sorted(
            stats.messages_skipped.items(), key=lambda x: -x[1]
        ):
            print(f"  {reason:<24} {count}")

    if stats.pii_stats.counts:
        print("\nPII redactions (by category):")
        for cat, count in sorted(
            stats.pii_stats.counts.items(), key=lambda x: -x[1]
        ):
            print(f"  {cat:<24} {count}")

    if dry_run and stats.preview_chunks:
        print("\nFirst 10 chunks preview (redacted):")
        for i, ch in enumerate(stats.preview_chunks, 1):
            print(
                f"  [{i}] chunk_id={ch['chunk_id']} "
                f"chat={ch['chat_id']} "
                f"msgs={ch['messages']} chars={ch['char_len']}"
            )
            preview = ch["text_preview"].replace("\n", " | ")
            print(f"      text: {preview[:200]}")

    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = _build_parser().parse_args(argv)

    try:
        stats = run_bootstrap(
            export_path=args.export,
            db_path=args.db,
            whitelist_path=args.whitelist,
            allow_all=args.allow_all,
            limit=args.limit,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Malformed JSON: {exc}", file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError) as exc:
        print(f"[ERROR] Runtime error: {exc}", file=sys.stderr)
        return 3

    _print_report(stats, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
