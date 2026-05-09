#!/usr/bin/env python3
"""Forget-Me Tool — privacy compliance scrubber для archive.db.

Удаляет следы конкретного пользователя или чата из локального архива
сообщений Краба. По default — dry-run; для реального удаления нужен
явный флаг ``--apply``.

Затронутые таблицы:
  * ``messages``                    — WHERE sender_id=X или chat_id=X
  * ``chunks`` + ``chunk_messages`` — chunks, которые ссылались на эти messages
  * ``messages_fts``                — FTS5 external content (чистится через
                                      DELETE/INSERT ноту над chunks)
  * ``message_media_summaries``     — vision-summary, привязанные к messages
  * ``vec_chunks`` (опционально, ``--also-vec-chunks``) — векторы удалённых chunks
  * ``response_feedback`` (опционально, ``--also-feedback``) — фидбэк
                                      по ответам Краба в этом чате/у этого юзера

Audit-log: append-only ``~/.openclaw/krab_runtime_state/forget_me_audit.log``.

Примеры:
    venv/bin/python scripts/forget_me.py --user-id 12345
    venv/bin/python scripts/forget_me.py --chat-id -1001234567890 --apply
    venv/bin/python scripts/forget_me.py --user-id 12345 --apply \\
        --also-vec-chunks --also-feedback
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Пути по умолчанию совпадают с тем, что выдаёт ArchivePaths.default(),
# но не импортируем оттуда, чтобы скрипт был независим (privacy-tool
# должен работать даже когда основной runtime сломан).
_DEFAULT_RUNTIME_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
_DEFAULT_DB_PATH = _DEFAULT_RUNTIME_DIR / "archive.db"
_DEFAULT_AUDIT_LOG = _DEFAULT_RUNTIME_DIR / "forget_me_audit.log"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Проверка наличия таблицы или virtual table в БД."""
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1;", (name,)).fetchone()
    return row is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Plan / execute
# ---------------------------------------------------------------------------


@dataclass
class ForgetPlan:
    """Что собираемся удалить (или что удалили)."""

    target_kind: str  # "user_id" | "chat_id"
    target_value: str
    affected_chats: list[str] = field(default_factory=list)
    message_count: int = 0
    chunk_ids: list[str] = field(default_factory=list)
    chunk_rowids: list[int] = field(default_factory=list)
    media_summary_count: int = 0
    vec_chunks_count: int = 0  # учитывается при --also-vec-chunks
    response_feedback_count: int = 0  # учитывается при --also-feedback

    def to_audit_dict(self, *, applied: bool, dry_run: bool) -> dict[str, Any]:
        return {
            "timestamp": _now_iso(),
            "applied": applied,
            "dry_run": dry_run,
            "target_kind": self.target_kind,
            "target_value": self.target_value,
            "affected_chats": self.affected_chats,
            "message_count": self.message_count,
            "chunks_count": len(self.chunk_ids),
            "media_summary_count": self.media_summary_count,
            "vec_chunks_count": self.vec_chunks_count,
            "response_feedback_count": self.response_feedback_count,
        }


def _collect_target_messages(
    conn: sqlite3.Connection,
    *,
    user_id: str | None,
    chat_id: str | None,
) -> list[tuple[str, str]]:
    """Вернуть список (chat_id, message_id) под удаление."""
    if user_id is not None:
        rows = conn.execute(
            "SELECT chat_id, message_id FROM messages WHERE sender_id = ?;",
            (user_id,),
        ).fetchall()
    elif chat_id is not None:
        rows = conn.execute(
            "SELECT chat_id, message_id FROM messages WHERE chat_id = ?;",
            (chat_id,),
        ).fetchall()
    else:  # pragma: no cover — argparse не пропустит
        rows = []
    return [(str(r[0]), str(r[1])) for r in rows]


def _collect_chunks_for_messages(
    conn: sqlite3.Connection,
    targets: Iterable[tuple[str, str]],
) -> tuple[list[str], list[int]]:
    """Найти chunk_id и rowid (== chunks.id), которые содержат эти messages."""
    targets_list = list(targets)
    if not targets_list:
        return [], []

    chunk_ids: set[str] = set()
    # Пакетно: 500 за раз, чтобы не переполнить SQL parameter cap.
    batch = 500
    for i in range(0, len(targets_list), batch):
        chunk = targets_list[i : i + batch]
        placeholders = ",".join("(?,?)" for _ in chunk)
        params: list[str] = []
        for cid, mid in chunk:
            params.extend([cid, mid])
        rows = conn.execute(
            f"SELECT DISTINCT chunk_id FROM chunk_messages "
            f"WHERE (chat_id, message_id) IN ({placeholders});",
            params,
        ).fetchall()
        for r in rows:
            chunk_ids.add(str(r[0]))

    if not chunk_ids:
        return [], []

    rowids: list[int] = []
    chunk_ids_list = list(chunk_ids)
    for i in range(0, len(chunk_ids_list), batch):
        chunk = chunk_ids_list[i : i + batch]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id FROM chunks WHERE chunk_id IN ({placeholders});",
            chunk,
        ).fetchall()
        rowids.extend(int(r[0]) for r in rows)

    return chunk_ids_list, rowids


def _count_media_summaries(
    conn: sqlite3.Connection,
    targets: Iterable[tuple[str, str]],
) -> int:
    """Сколько строк в message_media_summaries попадут под FK CASCADE."""
    if not _table_exists(conn, "message_media_summaries"):
        return 0
    targets_list = list(targets)
    if not targets_list:
        return 0
    total = 0
    batch = 500
    for i in range(0, len(targets_list), batch):
        chunk = targets_list[i : i + batch]
        placeholders = ",".join("(?,?)" for _ in chunk)
        params: list[str] = []
        for cid, mid in chunk:
            params.extend([cid, mid])
        row = conn.execute(
            f"SELECT COUNT(*) FROM message_media_summaries "
            f"WHERE (chat_id, message_id) IN ({placeholders});",
            params,
        ).fetchone()
        total += int(row[0]) if row else 0
    return total


def _count_response_feedback(
    conn: sqlite3.Connection,
    *,
    user_id: str | None,
    chat_id: str | None,
) -> int:
    """Подсчёт response_feedback под удаление.

    Для chat_id — все строки чата. Для user_id — JOIN через messages
    (response_feedback хранит ответы Краба, у них sender_id != user_id,
    поэтому привязка через message_id, на который Краб отвечал, не
    однозначна; здесь чистим только по chat_id-проекции, в случае user_id
    оставляем 0).
    """
    if not _table_exists(conn, "response_feedback"):
        return 0
    if chat_id is not None:
        row = conn.execute(
            "SELECT COUNT(*) FROM response_feedback WHERE chat_id = ?;",
            (chat_id,),
        ).fetchone()
        return int(row[0]) if row else 0
    return 0


def build_plan(
    conn: sqlite3.Connection,
    *,
    user_id: str | None,
    chat_id: str | None,
    also_vec_chunks: bool,
    also_feedback: bool,
) -> ForgetPlan:
    """Собрать план удаления, ничего не меняя в БД."""
    if (user_id is None) == (chat_id is None):
        raise ValueError("Нужен ровно один из --user-id / --chat-id")

    target_kind = "user_id" if user_id is not None else "chat_id"
    target_value = user_id if user_id is not None else chat_id  # type: ignore[assignment]
    plan = ForgetPlan(target_kind=target_kind, target_value=str(target_value))

    if not _table_exists(conn, "messages"):
        # БД пустая или ещё не инициализирована — план просто пустой.
        return plan

    targets = _collect_target_messages(conn, user_id=user_id, chat_id=chat_id)
    plan.message_count = len(targets)
    plan.affected_chats = sorted({cid for cid, _ in targets})

    if targets:
        chunk_ids, rowids = _collect_chunks_for_messages(conn, targets)
        plan.chunk_ids = chunk_ids
        plan.chunk_rowids = rowids
        plan.media_summary_count = _count_media_summaries(conn, targets)

    if also_vec_chunks and plan.chunk_rowids and _table_exists(conn, "vec_chunks"):
        # Считаем фактическое количество совпадений в vec_chunks
        # (могут быть rowid-ы без вектора, если не успели проэмбедить).
        batch = 500
        total = 0
        for i in range(0, len(plan.chunk_rowids), batch):
            slc = plan.chunk_rowids[i : i + batch]
            placeholders = ",".join("?" for _ in slc)
            row = conn.execute(
                f"SELECT COUNT(*) FROM vec_chunks WHERE rowid IN ({placeholders});",
                slc,
            ).fetchone()
            total += int(row[0]) if row else 0
        plan.vec_chunks_count = total

    if also_feedback:
        plan.response_feedback_count = _count_response_feedback(
            conn, user_id=user_id, chat_id=chat_id
        )

    return plan


def apply_plan(
    conn: sqlite3.Connection,
    plan: ForgetPlan,
    *,
    user_id: str | None,
    chat_id: str | None,
    also_vec_chunks: bool,
    also_feedback: bool,
) -> None:
    """Выполнить удаление по плану в одной транзакции."""
    if plan.message_count == 0 and plan.response_feedback_count == 0:
        return

    # Включаем FK CASCADE — chunk_messages.message_id, message_media_summaries
    # удалятся автоматически.
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        with conn:  # transaction
            # 1) vec_chunks по rowid (если есть таблица и флаг).
            if also_vec_chunks and plan.chunk_rowids and _table_exists(conn, "vec_chunks"):
                batch = 500
                for i in range(0, len(plan.chunk_rowids), batch):
                    slc = plan.chunk_rowids[i : i + batch]
                    placeholders = ",".join("?" for _ in slc)
                    conn.execute(
                        f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders});",
                        slc,
                    )

            # 2) messages_fts: FTS5 external content требует ручного 'delete'
            # до удаления самих chunks (для корректного rebuild индекса).
            if plan.chunk_rowids and _table_exists(conn, "messages_fts"):
                batch = 500
                for i in range(0, len(plan.chunk_rowids), batch):
                    slc = plan.chunk_rowids[i : i + batch]
                    placeholders = ",".join("?" for _ in slc)
                    # 'delete' command по rowid в external-content FTS5
                    conn.executemany(
                        "INSERT INTO messages_fts(messages_fts, rowid, text_redacted) "
                        "SELECT 'delete', id, text_redacted FROM chunks WHERE id = ?;",
                        [(rid,) for rid in slc],
                    )
                    _ = placeholders  # silence linter

            # 3) chunks (CASCADE удалит chunk_messages).
            if plan.chunk_ids:
                batch = 500
                for i in range(0, len(plan.chunk_ids), batch):
                    slc = plan.chunk_ids[i : i + batch]
                    placeholders = ",".join("?" for _ in slc)
                    conn.execute(
                        f"DELETE FROM chunks WHERE chunk_id IN ({placeholders});",
                        slc,
                    )

            # 4) messages (CASCADE удалит chunk_messages, message_media_summaries
            # уже отдельная таблица — её чистим явно ниже).
            if user_id is not None:
                conn.execute("DELETE FROM messages WHERE sender_id = ?;", (user_id,))
            elif chat_id is not None:
                conn.execute("DELETE FROM messages WHERE chat_id = ?;", (chat_id,))

            # 5) message_media_summaries — нет FK на messages, чистим явно.
            if _table_exists(conn, "message_media_summaries"):
                if user_id is not None:
                    # У summaries нет sender_id; чистим только то, что
                    # уже сирота (нет соответствующего message).
                    conn.execute(
                        "DELETE FROM message_media_summaries "
                        "WHERE (chat_id, message_id) NOT IN "
                        "(SELECT chat_id, message_id FROM messages);"
                    )
                elif chat_id is not None:
                    conn.execute(
                        "DELETE FROM message_media_summaries WHERE chat_id = ?;",
                        (chat_id,),
                    )

            # 6) response_feedback (опционально).
            if also_feedback and _table_exists(conn, "response_feedback"):
                if chat_id is not None:
                    conn.execute(
                        "DELETE FROM response_feedback WHERE chat_id = ?;",
                        (chat_id,),
                    )
                # Для user_id — пропускаем (см. _count_response_feedback).
    except sqlite3.Error:
        # Откатилось через контекстный менеджер; пробрасываем выше.
        raise


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def append_audit(audit_log_path: Path, payload: dict[str, Any]) -> None:
    """Append-only JSONL запись в audit-log."""
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        fh.write("\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_report(plan: ForgetPlan, *, applied: bool) -> str:
    verb = "Удалено" if applied else "Будет удалено (dry-run)"
    lines = [
        f"=== Forget-Me Tool — {verb} ===",
        f"Target: {plan.target_kind} = {plan.target_value}",
        f"Affected chats: {len(plan.affected_chats)} "
        f"({', '.join(plan.affected_chats[:5]) or '—'}"
        + (", ..." if len(plan.affected_chats) > 5 else "")
        + ")",
        f"messages:                 {plan.message_count}",
        f"chunks:                   {len(plan.chunk_ids)}",
        f"message_media_summaries:  {plan.media_summary_count}",
        f"vec_chunks:               {plan.vec_chunks_count}",
        f"response_feedback:        {plan.response_feedback_count}",
    ]
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="forget_me.py",
        description="Удаление следов user_id / chat_id из archive.db (privacy compliance).",
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--user-id", help="Telegram sender_id (str/int) для скраба")
    target.add_argument("--chat-id", help="Telegram chat_id для скраба")

    p.add_argument(
        "--db",
        default=str(_DEFAULT_DB_PATH),
        help=f"Путь к archive.db (по умолчанию: {_DEFAULT_DB_PATH})",
    )
    p.add_argument(
        "--audit-log",
        default=str(_DEFAULT_AUDIT_LOG),
        help=f"Audit-log файл (по умолчанию: {_DEFAULT_AUDIT_LOG})",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Реально применить удаление (без флага — dry-run).",
    )
    p.add_argument(
        "--also-vec-chunks",
        action="store_true",
        help="Удалять также строки в vec_chunks (sqlite-vec).",
    )
    p.add_argument(
        "--also-feedback",
        action="store_true",
        help="Удалять также строки в response_feedback (только для --chat-id).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Ошибка: archive.db не найден по пути {db_path}", file=sys.stderr)
        return 2

    dry_run = not args.apply
    audit_log_path = Path(args.audit_log)

    conn = sqlite3.connect(str(db_path))
    try:
        plan = build_plan(
            conn,
            user_id=args.user_id,
            chat_id=args.chat_id,
            also_vec_chunks=args.also_vec_chunks,
            also_feedback=args.also_feedback,
        )

        print(_format_report(plan, applied=False))

        if dry_run:
            append_audit(
                audit_log_path,
                plan.to_audit_dict(applied=False, dry_run=True),
            )
            print("\n[dry-run] Изменения НЕ применены. Используй --apply для реального удаления.")
            return 0

        # Реальный apply
        apply_plan(
            conn,
            plan,
            user_id=args.user_id,
            chat_id=args.chat_id,
            also_vec_chunks=args.also_vec_chunks,
            also_feedback=args.also_feedback,
        )
        append_audit(
            audit_log_path,
            plan.to_audit_dict(applied=True, dry_run=False),
        )
        print("\n[applied] Удаление выполнено. Audit-log:", audit_log_path)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
