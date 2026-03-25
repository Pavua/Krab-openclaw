#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Одноразовый скрипт для архивирования старых inbox items.

Что делает:
- Находит owner_request items с конкретными message_id (10897, 10848)
- Устанавливает им статус "cancelled" через InboxService.bulk_update_status
- Записывает actor="system-cleanup" и resolution note в workflow events
- Поддерживает --dry-run режим для безопасной проверки перед применением

Запуск:
    python scripts/cleanup_old_inbox_items.py --dry-run   # только показать что будет
    python scripts/cleanup_old_inbox_items.py             # применить изменения
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Добавляем корень репозитория в путь
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.core.inbox_service import InboxService  # noqa: E402


_RESOLUTION_NOTE = "archived during inbox cleanup migration"
_ACTOR = "system-cleanup"
_TARGET_MESSAGE_IDS = {"10897", "10848"}


def find_stale_owner_requests(service: InboxService) -> list[dict]:
    """Возвращает owner_request items, которые нужно архивировать."""
    # Загружаем все owner_request items (open + acked)
    candidates = service.list_items(kind="owner_request", limit=200)
    stale = []
    for item in candidates:
        metadata = item.get("metadata") or {}
        message_id = str(metadata.get("message_id") or "").strip()
        if message_id in _TARGET_MESSAGE_IDS:
            stale.append(item)
    return stale


def print_item_summary(item: dict, prefix: str = "  ") -> None:
    metadata = item.get("metadata") or {}
    print(
        f"{prefix}[{item['item_id']}] "
        f"kind={item['kind']} status={item['status']} "
        f"message_id={metadata.get('message_id', '?')} "
        f"title={item['title']!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Архивировать старые owner_request inbox items")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать что будет сделано, без изменений",
    )
    args = parser.parse_args()

    service = InboxService()
    print(f"📂 Inbox state: {service.state_path}")

    stale_items = find_stale_owner_requests(service)

    if not stale_items:
        print("✅ Стейл owner_request items не найдены — ничего делать не нужно.")
        return 0

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}Найдено {len(stale_items)} items для архивирования:")
    for item in stale_items:
        print_item_summary(item)

    if args.dry_run:
        print("\n[DRY-RUN] Изменения НЕ применены. Запустите без --dry-run для применения.")
        return 0

    item_ids = [item["item_id"] for item in stale_items]
    result = service.bulk_update_status(
        item_ids=item_ids,
        status="cancelled",
        actor=_ACTOR,
        note=_RESOLUTION_NOTE,
    )

    if not result.get("ok"):
        print(f"\n❌ Ошибка при bulk_update_status: {result.get('error')}")
        if result.get("details"):
            for detail in result["details"]:
                print(f"  {detail}")
        return 1

    print(
        f"\n✅ Архивировано: {result['success_count']} items "
        f"(ошибок: {result['error_count']})"
    )

    # Верифицируем: архивированные items не должны появляться в open filter
    remaining = find_stale_owner_requests(service)
    remaining_open = [i for i in remaining if i.get("status") in {"open", "acked"}]
    if remaining_open:
        print(f"\n⚠️  Внимание: {len(remaining_open)} items всё ещё open после архивирования:")
        for item in remaining_open:
            print_item_summary(item)
        return 1

    print("✅ Верификация пройдена: все целевые items закрыты.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
