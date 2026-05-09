#!/usr/bin/env python3
"""
Memory Consolidation (Feature L).

Periodic batch process: старые chunks с low retrieval count группируются
по chat_id + временной близости и summarize'ятся через cheap LLM в один
condensed chunk. Оригиналы НЕ удаляются — помечаются `consolidated_into`
(soft delete + audit trail).

Usage:
    venv/bin/python scripts/memory_consolidate.py --age-days 90 --dry-run
    venv/bin/python scripts/memory_consolidate.py --age-days 90 --apply

Архитектура:
  * `find_consolidation_candidates(conn, age_days, max_per_chat)` — pure SQL,
    тестируется без LLM. Возвращает список групп.
  * `summarize_group(texts, llm_call)` — функция получает callable LLM и
    список текстов, возвращает condensed-string. Default LLM noop для dry-run.
  * `apply_consolidation(conn, group, summary)` — пишет new chunk + помечает
    оригиналы. В транзакции.

Schema-расширение: добавляем optional columns в `chunks` через ALTER TABLE
(idempotent, IF NOT EXISTS-style через try/except).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

ARCHIVE_DB_DEFAULT = Path.home() / ".openclaw" / "krab_memory" / "archive.db"

#: Максимум chunks в одной группе для summarize (LLM context limit).
DEFAULT_GROUP_SIZE = 10
#: Минимум для группирования — одиночный chunk не имеет смысла консолидировать.
MIN_GROUP_SIZE = 5
#: Окно временной близости — chunks within N часов одного chat'а группируются.
TEMPORAL_PROXIMITY_HOURS = 6


@dataclass
class ConsolidationGroup:
    """Группа chunks, кандидат на схлопывание в один condensed."""

    chat_id: str
    chunk_ids: list[str]
    texts: list[str]
    start_ts: str
    end_ts: str

    @property
    def size(self) -> int:
        return len(self.chunk_ids)


def ensure_consolidation_columns(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE: добавляет consolidated_into / retrieval_count.

    SQLite не поддерживает ADD COLUMN IF NOT EXISTS до 3.35 надёжно через
    стандартный синтаксис, поэтому проверяем PRAGMA table_info вручную.
    """
    cur = conn.execute("PRAGMA table_info(chunks);")
    cols = {row[1] for row in cur.fetchall()}
    if "consolidated_into" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN consolidated_into TEXT;")
    if "retrieval_count" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN retrieval_count INTEGER NOT NULL DEFAULT 0;")
    if "validator_confirmed_at" not in cols:
        # Хук для Feature D recent-confirm boost: validator пока не пишет сюда,
        # но column нужен в schema для чистого LEFT JOIN.
        conn.execute("ALTER TABLE chunks ADD COLUMN validator_confirmed_at TEXT;")
    conn.commit()


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = ts.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def find_consolidation_candidates(
    conn: sqlite3.Connection,
    age_days: int,
    *,
    max_per_chat: int = 50,
    group_size: int = DEFAULT_GROUP_SIZE,
    min_group_size: int = MIN_GROUP_SIZE,
    proximity_hours: int = TEMPORAL_PROXIMITY_HOURS,
    now: datetime | None = None,
) -> list[ConsolidationGroup]:
    """Находит группы chunks-кандидатов.

    Критерии:
      * end_ts старше age_days дней назад;
      * consolidated_into IS NULL (не были схлопнуты ранее);
      * retrieval_count <= 2 (low usage, не часто извлекались);
      * группа: same chat_id + соседи по времени (gap <= proximity_hours).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=age_days)).isoformat(timespec="seconds")
    try:
        cur = conn.execute(
            """
            SELECT chunk_id, chat_id, start_ts, end_ts, text_redacted
            FROM chunks
            WHERE end_ts < ?
              AND (consolidated_into IS NULL OR consolidated_into = '')
              AND COALESCE(retrieval_count, 0) <= 2
            ORDER BY chat_id, start_ts;
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        # columns ещё не добавлены — нечего консолидировать.
        return []

    # Группировка: same chat_id + temporal proximity.
    groups: list[ConsolidationGroup] = []
    current: list[tuple[str, str, str, str, str]] = []
    last_chat: Optional[str] = None
    last_end: Optional[datetime] = None

    def flush() -> None:
        if len(current) >= min_group_size:
            chat_id = current[0][1]
            ids = [r[0] for r in current]
            texts = [r[4] for r in current]
            start_ts = current[0][2]
            end_ts = current[-1][3]
            groups.append(
                ConsolidationGroup(
                    chat_id=chat_id,
                    chunk_ids=ids,
                    texts=texts,
                    start_ts=start_ts,
                    end_ts=end_ts,
                )
            )

    for row in rows:
        chunk_id, chat_id, start_ts, end_ts, text = row
        start_dt = _parse_iso(start_ts)
        if last_chat != chat_id or last_end is None or start_dt is None:
            flush()
            current = []
        elif (start_dt - last_end) > timedelta(hours=proximity_hours):
            flush()
            current = []
        current.append(row)
        last_chat = chat_id
        last_end = _parse_iso(end_ts)
        if len(current) >= group_size:
            flush()
            current = []
            last_chat = None

    flush()

    # Ограничение per chat (не пытаемся в одном проходе схлопнуть весь чат).
    by_chat: dict[str, int] = {}
    capped: list[ConsolidationGroup] = []
    for g in groups:
        if by_chat.get(g.chat_id, 0) >= max_per_chat:
            continue
        capped.append(g)
        by_chat[g.chat_id] = by_chat.get(g.chat_id, 0) + 1
    return capped


def _noop_summarizer(texts: list[str]) -> str:
    """Default summarizer для dry-run: первые 200 символов конкатенации."""
    joined = " | ".join(texts)
    return f"[noop-summary] {joined[:200]}"


def summarize_group(texts: list[str], llm_call: Callable[[list[str]], str] | None = None) -> str:
    """Вызывает LLM (или noop) для summarize группы текстов."""
    if llm_call is None:
        return _noop_summarizer(texts)
    return llm_call(texts)


def _new_chunk_id(group: ConsolidationGroup) -> str:
    """Стабильный id для нового condensed chunk'а."""
    return f"consolidated:{group.chat_id}:{group.start_ts}:{group.size}"


def apply_consolidation(conn: sqlite3.Connection, group: ConsolidationGroup, summary: str) -> str:
    """Пишет new condensed chunk + помечает оригиналы. Транзакционно.

    Возвращает chunk_id нового chunk'а.
    """
    new_id = _new_chunk_id(group)
    try:
        conn.execute("BEGIN;")
        conn.execute(
            """
            INSERT INTO chunks
                (chunk_id, chat_id, start_ts, end_ts, message_count, char_len, text_redacted)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                new_id,
                group.chat_id,
                group.start_ts,
                group.end_ts,
                group.size,
                len(summary),
                summary,
            ),
        )
        placeholders = ",".join("?" * len(group.chunk_ids))
        conn.execute(
            f"UPDATE chunks SET consolidated_into = ? WHERE chunk_id IN ({placeholders});",
            [new_id, *group.chunk_ids],
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return new_id


def run(
    db_path: Path,
    age_days: int,
    *,
    dry_run: bool = True,
    llm_call: Callable[[list[str]], str] | None = None,
    output: Callable[[str], None] | None = None,
) -> dict:
    """Основной entry point: возвращает summary-словарь для CLI/тестов."""
    out = output or print
    if not db_path.exists():
        out(f"archive.db not found: {db_path}")
        return {"db_missing": True, "groups": 0}

    conn = sqlite3.connect(str(db_path))
    try:
        ensure_consolidation_columns(conn)
        groups = find_consolidation_candidates(conn, age_days)
        out(f"found {len(groups)} consolidation groups (age>={age_days}d)")
        applied = 0
        chunks_compressed = 0
        for group in groups:
            chunks_compressed += group.size
            if dry_run:
                out(
                    f"  [dry] chat={group.chat_id} "
                    f"chunks={group.size} "
                    f"window={group.start_ts}..{group.end_ts}"
                )
                continue
            summary = summarize_group(group.texts, llm_call=llm_call)
            new_id = apply_consolidation(conn, group, summary)
            applied += 1
            out(f"  [apply] new={new_id} merged={group.size} chat={group.chat_id}")
        return {
            "db_missing": False,
            "groups": len(groups),
            "applied": applied,
            "chunks_compressed": chunks_compressed,
            "dry_run": dry_run,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Memory consolidation (Feature L).")
    parser.add_argument("--db", type=Path, default=ARCHIVE_DB_DEFAULT)
    parser.add_argument("--age-days", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", dest="dry_run", action="store_false")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    result = run(args.db, args.age_days, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
