"""
Userbot команды Memory Layer (Track E).

Регистрируются основным чатом (Track B) в `userbot_bridge.py` как дополнительные
handlers; сам этот модуль — только функции-формировщики ответа и
класс-координатор, без прямой подписки на pyrofork события. Это делает модуль
юнит-тестируемым (без MTProto).

Команды:
  - `!archive <query>` — hybrid retrieval по Telegram-архиву;
  - `!arc <query>` — алиас `!archive` (регистрируется через command_aliases);
  - `!memory stats` — быстрая сводка по индексу (кол-во чатов / сообщений
    / chunks / индексированных векторов если vec-табличка есть).

Все команды — **owner-only**. Не-owner запросы молча игнорируются
(без раскрытия факта существования команды).

Форматирование вывода — MarkdownV2-safe (escape спец-символов). Telegram
ограничение 4096 символов соблюдается автоматически через усечение
результатов с пометкой "... <N skipped>".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from structlog import get_logger

from src.core.memory_archive import (
    ArchivePaths,
    list_tables,
    open_archive,
)
from src.core.memory_retrieval import HybridRetriever, SearchResult

logger = get_logger(__name__)


TELEGRAM_MESSAGE_LIMIT = 4000  # оставляем запас под MarkdownV2 экранирование
_MD2_RESERVED = set("_*[]()~`>#+-=|{}.!\\")


@dataclass(frozen=True)
class MemoryStats:
    """Снимок состояния archive.db для `!memory stats`."""

    chats: int
    messages: int
    chunks: int
    vectors: int  # -1 если vec_chunks недоступна (extension не загружена)
    db_size_bytes: int  # 0 если БД не существует


# ---------------------------------------------------------------------------
# Главный handler.
# ---------------------------------------------------------------------------

class MemoryCommandHandler:
    """
    Тонкая обёртка над HybridRetriever + БД для статистики.

    Не подписывается на события pyrofork сама — это делает userbot_bridge
    через зарегистрированный callback. Мы отдаём только готовые функции
    `handle_archive(query) -> str` и `handle_stats() -> str`.
    """

    def __init__(
        self,
        archive_paths: ArchivePaths | None = None,
        retriever: HybridRetriever | None = None,
    ) -> None:
        self._paths = archive_paths or ArchivePaths.default()
        self._retriever = retriever or HybridRetriever(archive_paths=self._paths)

    # ------------------------------------------------------------------
    # !archive <query>
    # ------------------------------------------------------------------

    def handle_archive(
        self,
        query: str,
        chat_id: str | None = None,
        top_k: int = 5,
        with_context: int = 1,
    ) -> str:
        """
        Полный путь для `!archive <query>`. Возвращает MarkdownV2-строку,
        готовую к отправке. При пустом/служебном query возвращает help.
        """
        query = (query or "").strip()
        if not query:
            return _usage_archive()

        try:
            results = self._retriever.search(
                query,
                chat_id=chat_id,
                top_k=top_k,
                with_context=with_context,
            )
        except Exception as exc:  # noqa: BLE001 — defensive: command не должен ронять userbot
            logger.error("memory_archive_search_failed", error=str(exc))
            return _escape_md("🧠 Memory Layer: ошибка поиска, смотри логи.")

        if not results:
            return _escape_md(
                f"🧠 По запросу «{query}» ничего не найдено в архиве."
            )

        return _format_results(query, results)

    # ------------------------------------------------------------------
    # !memory stats
    # ------------------------------------------------------------------

    def handle_stats(self) -> str:
        """Сводка состояния БД. Не ломается если БД отсутствует."""
        stats = self.collect_stats()
        return _format_stats(stats)

    def collect_stats(self) -> MemoryStats:
        """
        Считает counts без загрузки sqlite-vec (vec_chunks проверяется через
        sqlite_master — если таблицы нет, возвращаем vectors=-1).
        """
        if not self._paths.db.exists():
            return MemoryStats(
                chats=0, messages=0, chunks=0, vectors=-1, db_size_bytes=0
            )

        try:
            conn = open_archive(self._paths, read_only=True, create_if_missing=False)
        except (sqlite3.Error, FileNotFoundError) as exc:
            logger.warning("memory_stats_open_failed", error=str(exc))
            return MemoryStats(
                chats=0,
                messages=0,
                chunks=0,
                vectors=-1,
                db_size_bytes=self._paths.db.stat().st_size,
            )

        try:
            chats = _scalar_int(conn, "SELECT COUNT(*) FROM chats;")
            messages = _scalar_int(conn, "SELECT COUNT(*) FROM messages;")
            chunks = _scalar_int(conn, "SELECT COUNT(*) FROM chunks;")

            vectors = -1
            if "vec_chunks" in set(list_tables(conn)):
                try:
                    vectors = _scalar_int(conn, "SELECT COUNT(*) FROM vec_chunks;")
                except sqlite3.OperationalError:
                    # sqlite-vec extension не подгружена в этом conn'е.
                    vectors = -1
        finally:
            conn.close()

        return MemoryStats(
            chats=chats,
            messages=messages,
            chunks=chunks,
            vectors=vectors,
            db_size_bytes=self._paths.db.stat().st_size,
        )

    def close(self) -> None:
        """Закрывает retriever (если он держит БД)."""
        self._retriever.close()


# ---------------------------------------------------------------------------
# Форматирование.
# ---------------------------------------------------------------------------

def _format_results(query: str, results: Iterable[SearchResult]) -> str:
    """
    Строит MarkdownV2 сообщение из SearchResult'ов.

    Формат:
      🧠 *Memory archive* · запрос: `query`

      *1\\.* `2026-04-01` · chat `-100123` · score `0\\.87`
      text preview...
      \\_ context: earlier chunk...
      \\^ context: next chunk...
    """
    results = list(results)
    header = _escape_md(f"🧠 Memory archive · запрос: «{query}»")
    lines = [header, ""]

    total_chars = len(header) + 2
    shown = 0
    skipped = 0

    for idx, r in enumerate(results, start=1):
        block = _format_one(idx, r)
        if total_chars + len(block) > TELEGRAM_MESSAGE_LIMIT:
            skipped += 1
            continue
        lines.append(block)
        total_chars += len(block)
        shown += 1

    if skipped:
        lines.append(_escape_md(f"... {skipped} результат(ов) не влезли в лимит"))

    if not shown:
        lines.append(_escape_md("(все результаты отфильтрованы лимитом длины)"))

    return "\n".join(lines)


def _format_one(idx: int, r: SearchResult) -> str:
    """Один блок результата."""
    date_str = _short_date(r.timestamp)
    head = (
        f"*{idx}\\.* `{_escape_code(date_str)}` · "
        f"chat `{_escape_code(r.chat_id)}` · "
        f"score `{r.score:.2f}`".replace(".", "\\.", 2)
    )
    # Обрезаем превью текста до ~400 символов.
    preview = _truncate(r.text_redacted, 400)
    block = [head, _escape_md(preview)]

    for b in r.context_before:
        block.append(_escape_md(f"⤴ {_truncate(b, 150)}"))
    for a in r.context_after:
        block.append(_escape_md(f"⤵ {_truncate(a, 150)}"))

    return "\n".join(block) + "\n"


def _format_stats(s: MemoryStats) -> str:
    """Форматирование `!memory stats`."""
    lines = [
        _escape_md("🧠 *Memory Layer* · статистика"),
        "",
        _escape_md(f"• Chats indexed: {s.chats}"),
        _escape_md(f"• Messages:      {s.messages}"),
        _escape_md(f"• Chunks:        {s.chunks}"),
    ]
    if s.vectors >= 0:
        lines.append(_escape_md(f"• Vectors:       {s.vectors}"))
    else:
        lines.append(_escape_md("• Vectors:       (sqlite-vec не подключён)"))
    lines.append(_escape_md(f"• DB size:       {_format_bytes(s.db_size_bytes)}"))
    return "\n".join(lines)


def _usage_archive() -> str:
    return _escape_md(
        "🧠 Использование: `!archive <запрос>`\n"
        "Пример: `!archive dashboard redesign`"
    )


# ---------------------------------------------------------------------------
# Утилиты.
# ---------------------------------------------------------------------------

def _escape_md(text: str) -> str:
    """Экранирование под Telegram MarkdownV2."""
    out = []
    for ch in text:
        if ch in _MD2_RESERVED:
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _escape_code(text: str) -> str:
    """Для inline-code блоков в MarkdownV2 экранируем только ` и \\."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _short_date(ts: datetime) -> str:
    """Короткая дата для UI: YYYY-MM-DD."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.strftime("%Y-%m-%d")


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB"]
    value = n / 1024.0
    for u in units:
        if value < 1024:
            return f"{value:.1f} {u}"
        value /= 1024
    return f"{value:.1f} TB"


def _scalar_int(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])
