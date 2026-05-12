#!/usr/bin/env python3
"""
krab_memory_prune_orphans.py — Wave 90 + Wave 161: pruning orphan-чанков из archive.db.

Цель: чаты, которые давно неактивны (Krab покинул группу, чат архивирован, контакт
удалён), оставляют после себя messages/chunks/vec_chunks bloat. Скрипт находит
такие chat_id и (опционально) удаляет их данные.

Эвристика orphan по умолчанию — **last-message-age threshold**:
    Если самое свежее сообщение в чате старше `--threshold-days` дней,
    чат считается кандидатом на pruning.

last_indexed_at из таблицы `chats` НЕ годится — он показывает время bootstrap'а,
а не активность чата (см. Wave 90 investigation).

Wave 161 — batched DELETE:
    Wave 90 ran single `DELETE ... WHERE chat_id IN (...)` inside one transaction;
    on prod это блокировало 2 часа (193K messages + 21K chunks + CASCADE + vec_chunks).
    Теперь удаляем per chat_id, commit'имся каждые `COMMIT_EVERY` чатов, и
    останавливаемся по `--max-batch-time-sec`. VACUUM выключен по умолчанию.

Использование:
    venv/bin/python scripts/krab_memory_prune_orphans.py             # dry-run, JSON
    venv/bin/python scripts/krab_memory_prune_orphans.py --threshold-days 365
    venv/bin/python scripts/krab_memory_prune_orphans.py --commit-each-chat
    venv/bin/python scripts/krab_memory_prune_orphans.py --commit-each-chat --vacuum
    venv/bin/python scripts/krab_memory_prune_orphans.py --apply  # = --commit-each-chat (compat)

Safety:
    --commit-each-chat создаёт backup `archive.db.pre-prune-<ts>` ПЕРЕД любым DELETE.
    KRAB_MEMORY_PRUNE_APPLY=1 (env) эквивалентно --commit-each-chat (для plist).
    Прерывание по timeout сохраняет progress (последний commit).

State:
    Persist последнего audit в `~/.openclaw/krab_runtime_state/memory_prune_state.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()
DEFAULT_STATE_FILE = Path("~/.openclaw/krab_runtime_state/memory_prune_state.json").expanduser()
DEFAULT_THRESHOLD_DAYS = 180
DEFAULT_MAX_BATCH_TIME_SEC = 1800  # 30 минут hard cap
COMMIT_EVERY_N_CHATS = 10  # commit прогресса каждые N чатов


# ---------------------------------------------------------------------------
# Pure helpers (тестируемые отдельно).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PruneOutcome:
    """Низкоуровневый результат apply_prune: сколько успели до timeout."""

    deleted_messages: int
    deleted_chunks: int
    processed_chats: int
    remaining_chats: list[str] = field(default_factory=list)
    timed_out: bool = False
    elapsed_sec: float = 0.0


@dataclass(frozen=True)
class OrphanReport:
    """Результат dry-run/apply."""

    total_chats: int
    accessible: int
    orphan_candidates: int
    would_delete_messages: int
    would_delete_chunks: int
    would_save_mb: float
    threshold_days: int
    applied: bool
    backup_path: str | None
    audit_ts: str
    # Wave 161 — batched run telemetry.
    processed_chats: int = 0
    remaining_chats: int = 0
    timed_out: bool = False
    elapsed_sec: float = 0.0
    max_batch_time_sec: int = 0
    vacuumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_utc() -> datetime:
    """Wrapper для подмены в тестах."""

    return datetime.now(timezone.utc)


def detect_orphan_chats(
    conn: sqlite3.Connection,
    threshold_days: int,
    *,
    now_fn: Callable[[], datetime] = _now_utc,
) -> tuple[list[str], list[str]]:
    """Возвращает `(orphan_chat_ids, accessible_chat_ids)`.

    Orphan = самое свежее сообщение чата старше `threshold_days`.
    Чаты без единого сообщения тоже считаются orphan'ами.
    """

    cutoff = (now_fn() - timedelta(days=threshold_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Все chat_id из chats + те, что только в messages (на всякий случай).
    cur = conn.execute(
        """
        SELECT DISTINCT chat_id FROM (
            SELECT chat_id FROM chats
            UNION
            SELECT chat_id FROM messages
        )
        """
    )
    all_chats = [row[0] for row in cur.fetchall()]

    orphans: list[str] = []
    accessible: list[str] = []
    for chat_id in all_chats:
        row = conn.execute(
            "SELECT MAX(timestamp) FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        max_ts = row[0] if row else None
        if max_ts is None or max_ts < cutoff:
            orphans.append(chat_id)
        else:
            accessible.append(chat_id)
    return orphans, accessible


def estimate_savings(
    conn: sqlite3.Connection, orphan_chat_ids: list[str]
) -> tuple[int, int, float]:
    """Считает сколько messages/chunks удалится и приблизительный объём."""

    if not orphan_chat_ids:
        return 0, 0, 0.0

    placeholders = ",".join("?" for _ in orphan_chat_ids)
    msg_count_row = conn.execute(
        f"SELECT COUNT(*) FROM messages WHERE chat_id IN ({placeholders})",
        orphan_chat_ids,
    ).fetchone()
    chunk_count_row = conn.execute(
        f"SELECT COUNT(*) FROM chunks WHERE chat_id IN ({placeholders})",
        orphan_chat_ids,
    ).fetchone()
    msg_count = int(msg_count_row[0]) if msg_count_row else 0
    chunk_count = int(chunk_count_row[0]) if chunk_count_row else 0

    # Грубая оценка: ~600 байт на message + ~3 КБ на chunk (text+vector).
    bytes_estimate = msg_count * 600 + chunk_count * 3072
    return msg_count, chunk_count, round(bytes_estimate / (1024 * 1024), 2)


def make_backup(db_path: Path, *, now_fn: Callable[[], datetime] = _now_utc) -> Path:
    """Копирует archive.db в archive.db.pre-prune-<ts>. Возвращает путь."""

    ts = now_fn().strftime("%Y%m%d_%H%M%S")
    target = db_path.with_name(f"{db_path.name}.pre-prune-{ts}")
    shutil.copy2(db_path, target)
    return target


def apply_prune(
    conn: sqlite3.Connection,
    orphan_chat_ids: list[str],
    *,
    max_batch_time_sec: int = DEFAULT_MAX_BATCH_TIME_SEC,
    commit_every: int = COMMIT_EVERY_N_CHATS,
    monotonic_fn: Callable[[], float] = time.monotonic,
    progress_fn: Callable[[int, str, int, int], None] | None = None,
) -> PruneOutcome:
    """Удаляет данные orphan-чатов **per chat_id**, commit'ясь каждые `commit_every`.

    Wave 161: одна большая транзакция блокировала прод на 2 часа. Теперь:
      - DELETE per chat_id (vec_chunks → chunks → messages → chats).
      - `conn.commit()` каждые `commit_every` чатов = progress visible/recoverable.
      - Hard timeout `max_batch_time_sec` — оставшиеся чаты возвращаем как
        `remaining_chats` для следующего запуска. Прогресс сохранён последним
        commit'ом.
      - FK CASCADE мы НЕ используем — явные DELETE дают предсказуемый порядок
        и понятный rowcount per table.

    Returns:
        `PruneOutcome` с deleted counters, processed/remaining chats и timeout flag.
    """

    if not orphan_chat_ids:
        return PruneOutcome(0, 0, 0, [], False, 0.0)

    started = monotonic_fn()
    conn.execute("PRAGMA foreign_keys = ON")
    # Открытие транзакции отложим до первого DELETE per-chat (autocommit chunks).

    total_deleted_msgs = 0
    total_deleted_chunks = 0
    processed = 0
    remaining: list[str] = []

    for idx, chat_id in enumerate(orphan_chat_ids):
        if monotonic_fn() - started >= max_batch_time_sec:
            # Не хватило времени — оставшиеся пушаем в remaining_chats.
            remaining = list(orphan_chat_ids[idx:])
            break

        # Сначала собираем chunks.id для vec_chunks cleanup в текущем чате.
        chunk_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM chunks WHERE chat_id = ?",
                (chat_id,),
            ).fetchall()
        ]
        msg_count_row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        chat_msg_count = int(msg_count_row[0]) if msg_count_row else 0

        if chunk_ids:
            # vec_chunks (vec0) cleanup. В тестах extension не загружен — swallow.
            id_ph = ",".join("?" for _ in chunk_ids)
            try:
                conn.execute(
                    f"DELETE FROM vec_chunks WHERE rowid IN ({id_ph})",
                    chunk_ids,
                )
            except sqlite3.OperationalError:
                pass

        # Порядок важен: chunks/messages → chats (FK target).
        conn.execute("DELETE FROM chunks WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))

        total_deleted_msgs += chat_msg_count
        total_deleted_chunks += len(chunk_ids)
        processed += 1

        if progress_fn is not None:
            progress_fn(processed, chat_id, total_deleted_msgs, total_deleted_chunks)

        # Commit прогресса каждые N чатов — durable savepoint.
        if processed % commit_every == 0:
            conn.commit()

    # Финальный commit для остатка.
    conn.commit()

    elapsed = monotonic_fn() - started
    return PruneOutcome(
        deleted_messages=total_deleted_msgs,
        deleted_chunks=total_deleted_chunks,
        processed_chats=processed,
        remaining_chats=remaining,
        timed_out=bool(remaining),
        elapsed_sec=round(elapsed, 3),
    )


def persist_state(state_path: Path, report: OrphanReport) -> None:
    """Сохраняет последний отчёт в JSON."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(state_path)


# ---------------------------------------------------------------------------
# Орchestration.
# ---------------------------------------------------------------------------


def run_audit(
    db_path: Path,
    *,
    threshold_days: int,
    apply: bool,
    state_path: Path,
    now_fn: Callable[[], datetime] = _now_utc,
    max_batch_time_sec: int = DEFAULT_MAX_BATCH_TIME_SEC,
    vacuum: bool = False,
    commit_every: int = COMMIT_EVERY_N_CHATS,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> OrphanReport:
    """End-to-end: detect → estimate → (optional) apply batched → persist."""

    if not db_path.exists():
        raise FileNotFoundError(f"archive.db not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        orphans, accessible = detect_orphan_chats(conn, threshold_days, now_fn=now_fn)
        msg_count, chunk_count, mb = estimate_savings(conn, orphans)
    finally:
        conn.close()

    backup_path: Path | None = None
    outcome = PruneOutcome(0, 0, 0, [], False, 0.0)
    vacuumed = False

    if apply and orphans:
        backup_path = make_backup(db_path, now_fn=now_fn)
        conn = sqlite3.connect(str(db_path))
        try:
            outcome = apply_prune(
                conn,
                orphans,
                max_batch_time_sec=max_batch_time_sec,
                commit_every=commit_every,
                monotonic_fn=monotonic_fn,
            )
            # VACUUM — opt-in (медленная и блокирует на огромных БД).
            if vacuum and not outcome.timed_out:
                conn.execute("VACUUM")
                vacuumed = True
        finally:
            conn.close()

    report = OrphanReport(
        total_chats=len(orphans) + len(accessible),
        accessible=len(accessible),
        orphan_candidates=len(orphans),
        would_delete_messages=msg_count,
        would_delete_chunks=chunk_count,
        would_save_mb=mb,
        threshold_days=threshold_days,
        applied=bool(apply and orphans),
        backup_path=str(backup_path) if backup_path else None,
        audit_ts=now_fn().isoformat(),
        processed_chats=outcome.processed_chats,
        remaining_chats=len(outcome.remaining_chats),
        timed_out=outcome.timed_out,
        elapsed_sec=outcome.elapsed_sec,
        max_batch_time_sec=max_batch_time_sec if apply else 0,
        vacuumed=vacuumed,
    )
    persist_state(state_path, report)
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Krab archive.db orphan pruner")
    parser.add_argument("--db", type=Path, default=DEFAULT_ARCHIVE_DB)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--threshold-days", type=int, default=DEFAULT_THRESHOLD_DAYS)
    parser.add_argument(
        "--commit-each-chat",
        action="store_true",
        help="Реально удалить orphan-данные per chat_id с commit'ом прогресса (Wave 161).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Backwards-compat алиас для --commit-each-chat.",
    )
    parser.add_argument(
        "--max-batch-time-sec",
        type=int,
        default=int(
            os.environ.get("KRAB_MEMORY_PRUNE_MAX_BATCH_SEC", str(DEFAULT_MAX_BATCH_TIME_SEC))
        ),
        help=(
            "Hard timeout на apply фазу (default 1800 = 30min). "
            "При превышении оставшиеся orphan'ы сохраняются в state и "
            "обрабатываются следующим запуском."
        ),
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Запустить VACUUM после успешного prune (медленно, off by default).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    apply = args.commit_each_chat or args.apply or os.environ.get("KRAB_MEMORY_PRUNE_APPLY") == "1"
    try:
        report = run_audit(
            args.db,
            threshold_days=args.threshold_days,
            apply=apply,
            state_path=args.state,
            max_batch_time_sec=args.max_batch_time_sec,
            vacuum=args.vacuum,
        )
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
