#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inbox_bulk_ack.py — CLI обёртка над `InboxService.bulk_acknowledge_stale`.

Зачем нужен:
- быстрый локальный cleanup stale `open` items без HTTP endpoint;
- удобно гонять dry-run перед реальным ack-ом.

Примеры:
    venv/bin/python scripts/inbox_bulk_ack.py --age-hours 24 --dry-run
    venv/bin/python scripts/inbox_bulk_ack.py --age-hours 24 --kind proactive_action
    venv/bin/python scripts/inbox_bulk_ack.py --age-hours 24 --severity warning --target done
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Делаем src/ импортируемым из корня репозитория.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.inbox_service import inbox_service  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bulk-acknowledge stale open inbox items.",
    )
    parser.add_argument(
        "--age-hours",
        type=int,
        default=12,
        help="Минимальный возраст items (часов). По умолчанию 12.",
    )
    parser.add_argument("--kind", default=None, help="Фильтр по kind (e.g. proactive_action).")
    parser.add_argument("--severity", default=None, help="Фильтр по severity (info/warning/error).")
    parser.add_argument(
        "--target",
        default="acked",
        choices=["acked", "done", "cancelled"],
        help="Финальный статус (по умолчанию acked).",
    )
    parser.add_argument("--actor", default="cli-bulk-ack", help="Актор для workflow event.")
    parser.add_argument("--note", default="", help="Заметка для workflow event.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Только показать кандидатов, не менять state."
    )
    parser.add_argument(
        "--json", action="store_true", help="Печатать сырой JSON-ответ вместо summary."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = inbox_service.bulk_acknowledge_stale(
            kind=args.kind,
            severity=args.severity,
            age_threshold_hours=args.age_hours,
            dry_run=args.dry_run,
            actor=args.actor,
            note=args.note,
            target_status=args.target,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    matched = result.get("matched", 0)
    acked = result.get("acked", 0)
    print(f"matched={matched}  acked={acked}  dry_run={result.get('dry_run')}")
    print(
        f"filters: kind={args.kind or '*'} severity={args.severity or '*'} "
        f"age>={args.age_hours}h target={result.get('target_status')}"
    )
    items = result.get("items") or []
    if items:
        print("--- items ---")
        for row in items[:50]:
            print(
                f"  {row.get('item_id'):<14} {row.get('kind'):<22} "
                f"{row.get('severity'):<8} {row.get('created_at_utc')}  "
                f"{(row.get('title') or '')[:60]}"
            )
        if len(items) > 50:
            print(f"  ...и ещё {len(items) - 50}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
