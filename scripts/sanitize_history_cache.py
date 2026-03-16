#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Санирует сохранённую chat-history от reasoning-мусора.

Что делает:
- читает `history_cache.db` напрямую, не трогая остальные cache-ключи;
- находит записи `chat_history:*`;
- вырезает старые `<think>` / `Thinking Process` хвосты из assistant-сообщений;
- сохраняет очищенную историю с тем же `expires_at`, чтобы не продлевать TTL искусственно.

Зачем это нужно:
- баг уже мог успеть отравить persisted history до фикса в `OpenClawClient`;
- даже после починки записи новых ответов старый reasoning остаётся в SQLite и
  продолжает ломать следующие диалоги;
- этот repair-скрипт даёт безопасную one-shot очистку без ручного редактирования БД.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.openclaw_client import OpenClawClient


@dataclass
class SanitizeReport:
    """Короткий итог прогона санации history_cache."""

    scanned_keys: int = 0
    rewritten_keys: int = 0
    dropped_messages: int = 0
    invalid_payloads: int = 0


def _resolve_db_path(override: str | None) -> Path:
    """Возвращает путь к history_cache.db с безопасным fallback на repo-root."""
    candidate = str(override or "").strip()
    if candidate:
        return Path(candidate).expanduser().resolve()
    return (PROJECT_ROOT / "history_cache.db").resolve()


def _load_rows(conn: sqlite3.Connection, *, chat_id: str | None) -> list[tuple[str, str, float]]:
    """Читает только chat-history ключи, при необходимости фильтруя по одному chat_id."""
    if chat_id:
        key = f"chat_history:{chat_id}"
        cursor = conn.execute(
            "SELECT key, value, expires_at FROM cache WHERE key = ?",
            (key,),
        )
    else:
        cursor = conn.execute(
            "SELECT key, value, expires_at FROM cache WHERE key LIKE 'chat_history:%'",
        )
    return [(str(row[0]), str(row[1]), float(row[2])) for row in cursor.fetchall()]


def sanitize_history_cache(db_path: Path, *, chat_id: str | None = None, dry_run: bool = False) -> SanitizeReport:
    """Санирует history cache и возвращает статистику по изменённым ключам."""
    report = SanitizeReport()
    if not db_path.exists():
        raise FileNotFoundError(f"history_cache.db не найден: {db_path}")

    with sqlite3.connect(db_path) as conn:
        rows = _load_rows(conn, chat_id=chat_id)
        report.scanned_keys = len(rows)

        for key, raw_value, expires_at in rows:
            try:
                payload = json.loads(raw_value)
            except json.JSONDecodeError:
                report.invalid_payloads += 1
                continue

            if not isinstance(payload, list):
                report.invalid_payloads += 1
                continue

            sanitized_messages, changed = OpenClawClient._sanitize_session_history(payload)
            if not changed:
                continue

            report.rewritten_keys += 1
            report.dropped_messages += max(0, len(payload) - len(sanitized_messages))

            if dry_run:
                continue

            conn.execute(
                "UPDATE cache SET value = ?, expires_at = ? WHERE key = ?",
                (json.dumps(sanitized_messages, ensure_ascii=False), expires_at, key),
            )

        if not dry_run:
            conn.commit()

    return report


def main() -> int:
    """CLI entrypoint для ручного repair history cache."""
    parser = argparse.ArgumentParser(description="Санирует reasoning-мусор в history_cache.db")
    parser.add_argument("--db-path", default="", help="Явный путь к history_cache.db")
    parser.add_argument("--chat-id", default="", help="Очистить только один chat_id")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не писать, только показать статистику")
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db_path)
    report = sanitize_history_cache(
        db_path,
        chat_id=str(args.chat_id or "").strip() or None,
        dry_run=bool(args.dry_run),
    )

    print(f"db_path={db_path}")
    print(f"scanned_keys={report.scanned_keys}")
    print(f"rewritten_keys={report.rewritten_keys}")
    print(f"dropped_messages={report.dropped_messages}")
    print(f"invalid_payloads={report.invalid_payloads}")
    print(f"dry_run={bool(args.dry_run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
