#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cleanup_old_inbox_items.py — безопасная чистка устаревших inbox items.

Что это:
- утилита для архивирования реально залежавшихся open/acked items;
- по умолчанию работает консервативно и трогает только старые owner_request;
- не вмешивается в relay/proactive/system items без явного указания.

Зачем нужно:
- старые незакрытые owner_request засоряют health-lite и handoff summary;
- ручная чистка по жёстко вшитым message_id быстро устаревает;
- нужен повторно используемый инструмент для truth-maintenance runtime state.

Связь с системой:
- читает persisted state через `InboxService`;
- использует `filter_by_age()` и `bulk_update_status()` вместо прямого JSON-редактирования;
- подходит и для dry-run проверки, и для реального применения.

Примеры:
    python scripts/cleanup_old_inbox_items.py --dry-run
    python scripts/cleanup_old_inbox_items.py
    python scripts/cleanup_old_inbox_items.py --older-than-days 1 --kind relay_request --dry-run
    python scripts/cleanup_old_inbox_items.py --message-id 10897 --message-id 10848
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# Добавляем корень репозитория в путь, чтобы запускать скрипт напрямую.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.core.inbox_service import InboxService  # noqa: E402


_DEFAULT_KIND = "owner_request"
_DEFAULT_STATUS = "open"
_DEFAULT_OLDER_THAN_DAYS = 3
_DEFAULT_NOTE = "archived during stale inbox cleanup"
_DEFAULT_ACTOR = "system-cleanup"


def build_cutoff_iso(*, older_than_days: int, now: datetime | None = None) -> str:
    """Возвращает ISO cutoff для фильтрации stale items."""
    effective_now = now or datetime.now(timezone.utc)
    return (effective_now - timedelta(days=max(0, int(older_than_days)))).isoformat(timespec="seconds")


def _normalize_set(values: Iterable[str] | None) -> set[str]:
    """Нормализует CLI-список значений в компактный set строк."""
    return {str(value or "").strip() for value in values or [] if str(value or "").strip()}


def select_stale_items(
    service: InboxService,
    *,
    older_than_days: int,
    kind: str,
    status: str,
    message_ids: Iterable[str] | None = None,
    item_ids: Iterable[str] | None = None,
    limit: int = 100,
) -> list[dict]:
    """Возвращает stale items, удовлетворяющие консервативным фильтрам."""
    cutoff_iso = build_cutoff_iso(older_than_days=older_than_days)
    candidates = service.filter_by_age(
        older_than_date=cutoff_iso,
        kind=str(kind or "").strip().lower(),
        status=str(status or "").strip().lower(),
        limit=max(1, int(limit or 100)),
    )
    target_message_ids = _normalize_set(message_ids)
    target_item_ids = _normalize_set(item_ids)

    selected: list[dict] = []
    for item in candidates:
        metadata = item.get("metadata") or {}
        item_id = str(item.get("item_id") or "").strip()
        message_id = str(metadata.get("message_id") or "").strip()

        # Если пользователь явно ограничил selection, ничего лишнего не закрываем.
        if target_item_ids and item_id not in target_item_ids:
            continue
        if target_message_ids and message_id not in target_message_ids:
            continue
        selected.append(item)
    return selected


def print_item_summary(item: dict, prefix: str = "  ") -> None:
    """Печатает компактную строку item-а для dry-run / apply отчёта."""
    metadata = item.get("metadata") or {}
    print(
        f"{prefix}[{item['item_id']}] "
        f"kind={item['kind']} status={item['status']} "
        f"created={item.get('created_at_utc', '?')} "
        f"message_id={metadata.get('message_id', '-')} "
        f"title={item['title']!r}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Разбирает аргументы CLI."""
    parser = argparse.ArgumentParser(description="Архивировать устаревшие inbox items")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать найденные stale items, без изменений",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=_DEFAULT_OLDER_THAN_DAYS,
        help=f"Минимальный возраст item-а в днях (по умолчанию: {_DEFAULT_OLDER_THAN_DAYS})",
    )
    parser.add_argument(
        "--kind",
        default=_DEFAULT_KIND,
        help=f"Тип inbox item-а для cleanup (по умолчанию: {_DEFAULT_KIND})",
    )
    parser.add_argument(
        "--status",
        default=_DEFAULT_STATUS,
        help=f"Статус для выборки (по умолчанию: {_DEFAULT_STATUS})",
    )
    parser.add_argument(
        "--message-id",
        action="append",
        default=[],
        help="Опционально ограничить cleanup конкретным Telegram message_id; можно повторять флаг",
    )
    parser.add_argument(
        "--item-id",
        action="append",
        default=[],
        help="Опционально ограничить cleanup конкретным inbox item_id; можно повторять флаг",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Максимум кандидатов для анализа (по умолчанию: 100)",
    )
    parser.add_argument(
        "--actor",
        default=_DEFAULT_ACTOR,
        help=f"Actor для workflow trail (по умолчанию: {_DEFAULT_ACTOR})",
    )
    parser.add_argument(
        "--note",
        default=_DEFAULT_NOTE,
        help="Resolution note для archived items",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    service = InboxService()
    print(f"📂 Inbox state: {service.state_path}")

    stale_items = select_stale_items(
        service,
        older_than_days=args.older_than_days,
        kind=args.kind,
        status=args.status,
        message_ids=args.message_id,
        item_ids=args.item_id,
        limit=args.limit,
    )

    if not stale_items:
        print("✅ Подходящие stale items не найдены — ничего делать не нужно.")
        return 0

    print(
        f"\n{'[DRY-RUN] ' if args.dry_run else ''}"
        f"Найдено {len(stale_items)} stale items для архивирования:"
    )
    for item in stale_items:
        print_item_summary(item)

    if args.dry_run:
        print("\n[DRY-RUN] Изменения НЕ применены. Запустите без --dry-run для применения.")
        return 0

    item_ids = [item["item_id"] for item in stale_items]
    result = service.bulk_update_status(
        item_ids=item_ids,
        status="cancelled",
        actor=args.actor,
        note=args.note,
    )
    if not result.get("ok"):
        print(f"\n❌ Ошибка при bulk_update_status: {result.get('error')}")
        for detail in result.get("details") or []:
            print(f"  {detail}")
        return 1

    print(
        f"\n✅ Архивировано: {result['success_count']} items "
        f"(ошибок: {result['error_count']})"
    )

    remaining = select_stale_items(
        service,
        older_than_days=args.older_than_days,
        kind=args.kind,
        status=args.status,
        message_ids=args.message_id,
        item_ids=args.item_id,
        limit=args.limit,
    )
    if remaining:
        print(f"\n⚠️ После cleanup осталось {len(remaining)} stale items:")
        for item in remaining:
            print_item_summary(item)
        return 1

    print("✅ Верификация пройдена: целевые stale items закрыты.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
