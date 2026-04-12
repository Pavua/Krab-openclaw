# -*- coding: utf-8 -*-
"""
Тесты фильтра status="all" в InboxService.list_items().

Session 7: status="all" должен возвращать ВСЕ items без фильтрации.
Раньше "all" случайно фильтровал всё (попадал в ветку item.status != "all").
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.inbox_service import InboxService


@pytest.fixture()
def inbox_with_items(tmp_path: Path) -> InboxService:
    """Inbox с 3 items: open, acked, done."""
    svc = InboxService(state_path=tmp_path / "inbox.json")
    svc.upsert_item(
        dedupe_key="item-open",
        kind="watch_alert",
        source="test",
        title="Open item",
        body="body1",
        severity="info",
        status="open",
    )
    svc.upsert_item(
        dedupe_key="item-acked",
        kind="watch_alert",
        source="test",
        title="Acked item",
        body="body2",
        severity="info",
        status="acked",
    )
    svc.upsert_item(
        dedupe_key="item-done",
        kind="watch_alert",
        source="test",
        title="Done item",
        body="body3",
        severity="info",
        status="done",
    )
    return svc


def test_status_all_returns_everything(inbox_with_items: InboxService) -> None:
    """status='all' должен вернуть все 3 items без фильтрации."""
    items = inbox_with_items.list_items(status="all", limit=100)
    assert len(items) == 3


def test_status_open_filters_correctly(inbox_with_items: InboxService) -> None:
    """status='open' — включает items со статусом open (из _open_statuses)."""
    items = inbox_with_items.list_items(status="open", limit=100)
    statuses = {i["status"] for i in items}
    # open filter возвращает items из _open_statuses
    assert "open" in statuses
    assert len(items) >= 1


def test_status_acked_filters_single(inbox_with_items: InboxService) -> None:
    """status='acked' — только items со статусом acked."""
    items = inbox_with_items.list_items(status="acked", limit=100)
    assert len(items) == 1
    assert items[0]["status"] == "acked"


def test_status_done_filters_single(inbox_with_items: InboxService) -> None:
    """status='done' — только items со статусом done."""
    items = inbox_with_items.list_items(status="done", limit=100)
    assert len(items) == 1
    assert items[0]["status"] == "done"


def test_empty_status_returns_all(inbox_with_items: InboxService) -> None:
    """Пустой status — возвращает все items (без фильтрации)."""
    items = inbox_with_items.list_items(status="", limit=100)
    assert len(items) == 3


def test_none_status_returns_all(inbox_with_items: InboxService) -> None:
    """status=None — тоже возвращает все items."""
    items = inbox_with_items.list_items(limit=100)
    assert len(items) == 3


def test_status_all_case_insensitive(inbox_with_items: InboxService) -> None:
    """status='ALL' (uppercase) — тоже возвращает все."""
    items = inbox_with_items.list_items(status="ALL", limit=100)
    assert len(items) == 3
