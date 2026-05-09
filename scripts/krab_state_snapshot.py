#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 49-F: CLI-helper для управления state snapshots.

Примеры:
  python scripts/krab_state_snapshot.py --snapshot
  python scripts/krab_state_snapshot.py --list
  python scripts/krab_state_snapshot.py --restore 20260510T120000Z --confirm
  python scripts/krab_state_snapshot.py --cleanup
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Добавляем корень репозитория в sys.path для импорта src.*
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.state_snapshots import state_snapshot_manager  # noqa: E402


def _fmt_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / 1024 / 1024:.2f} MB"


def cmd_snapshot() -> int:
    result = state_snapshot_manager.snapshot_now(reason="cli")
    print(f"Snapshot создан: {result['path']}")
    print(f"  Скопировано: {len(result['copied'])} файлов")
    print(f"  Пропущено:   {len(result['skipped'])} (отсутствуют)")
    print(f"  Размер:      {_fmt_size(result['total_bytes'])}")
    if result["copied"]:
        print("  Файлы:")
        for entry in result["copied"]:
            print(f"    - {entry['file']}: {_fmt_size(entry['bytes'])}")
    return 0


def cmd_list() -> int:
    rows = state_snapshot_manager.list_snapshots()
    if not rows:
        print("Snapshots отсутствуют.")
        return 0
    print(f"Найдено snapshots: {len(rows)}")
    print(f"{'TIMESTAMP':<22} {'FILES':>5} {'SIZE':>10}  PATH")
    print("-" * 80)
    for r in rows:
        print(
            f"{r['timestamp']:<22} {r['file_count']:>5} {_fmt_size(r['total_bytes']):>10}  "
            f"{r['path']}"
        )
    return 0


def cmd_restore(timestamp: str, confirm: bool) -> int:
    if not confirm:
        print(
            "ВНИМАНИЕ: restore перезапишет текущие state-файлы. "
            "Перезапустите команду с --confirm для подтверждения.",
            file=sys.stderr,
        )
        return 2
    try:
        result = state_snapshot_manager.restore(timestamp)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    print(f"Restore завершён из snapshot {result['timestamp']}")
    print(f"  Восстановлено: {len(result['restored'])}")
    print(f"  Пропущено:     {len(result['skipped'])}")
    print(f"  Pre-restore backup текущего состояния: {result['pre_restore_backup']}")
    if result["restored"]:
        print("  Файлы:")
        for f in result["restored"]:
            print(f"    - {f}")
    return 0


def cmd_cleanup() -> int:
    deleted = state_snapshot_manager.cleanup_old()
    print(f"Cleanup: удалено {deleted} snapshot-директорий.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Управление periodic snapshots критичных state-файлов Krab."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true", help="Создать snapshot прямо сейчас.")
    group.add_argument("--list", action="store_true", help="Список snapshots (новые первыми).")
    group.add_argument(
        "--restore",
        metavar="TIMESTAMP",
        help="Восстановить state из snapshot (требует --confirm).",
    )
    group.add_argument("--cleanup", action="store_true", help="Применить retention-policy.")
    parser.add_argument(
        "--confirm", action="store_true", help="Подтверждение для разрушительных операций."
    )
    parser.add_argument(
        "--json", action="store_true", help="Вывод в JSON (для --list / --snapshot)."
    )

    args = parser.parse_args(argv)

    if args.json and args.list:
        rows = state_snapshot_manager.list_snapshots()
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if args.json and args.snapshot:
        result = state_snapshot_manager.snapshot_now(reason="cli")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.snapshot:
        return cmd_snapshot()
    if args.list:
        return cmd_list()
    if args.restore:
        return cmd_restore(args.restore, args.confirm)
    if args.cleanup:
        return cmd_cleanup()
    return 1


if __name__ == "__main__":
    # Маркер для логов CLI-вызова.
    _ = datetime.now(timezone.utc).isoformat()
    sys.exit(main())
