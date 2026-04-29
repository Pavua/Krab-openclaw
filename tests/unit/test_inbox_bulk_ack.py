# -*- coding: utf-8 -*-
"""
Tests для `InboxService.bulk_acknowledge_stale`.

Проверяем, что bulk-ack:
- в dry_run режиме не меняет persisted state;
- корректно фильтрует по `age_threshold_hours`;
- корректно фильтрует по `severity`;
- корректно фильтрует по `kind`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.core.inbox_service import InboxIdentity, InboxItem, InboxService


def _make_item(
    *,
    item_id: str,
    kind: str,
    severity: str,
    created_at: datetime,
    status: str = "open",
) -> dict:
    """Собирает persisted JSON inbox item-а с заданным created_at."""
    iso = created_at.isoformat(timespec="seconds")
    item = InboxItem(
        item_id=item_id,
        dedupe_key=f"{kind}:{item_id}",
        kind=kind,
        source="test",
        status=status,
        severity=severity,
        title=f"item {item_id}",
        body="body",
        created_at_utc=iso,
        updated_at_utc=iso,
        identity=InboxIdentity(operator_id="op", account_id="acc"),
        metadata={},
    )
    return item.to_dict()


def _seed_state(path: Path, items: list[dict]) -> None:
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_service_with_items(tmp_path: Path, items: list[dict]) -> InboxService:
    state_path = tmp_path / "inbox.json"
    _seed_state(state_path, items)
    return InboxService(state_path=state_path)


def test_dry_run_does_not_mutate_state(tmp_path: Path) -> None:
    """dry_run возвращает кандидатов и не меняет persisted state."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=24)
    service = _build_service_with_items(
        tmp_path,
        [
            _make_item(item_id="aaa", kind="proactive_action", severity="info", created_at=old),
            _make_item(item_id="bbb", kind="proactive_action", severity="info", created_at=old),
        ],
    )
    raw_before = (tmp_path / "inbox.json").read_text(encoding="utf-8")

    result = service.bulk_acknowledge_stale(age_threshold_hours=12, dry_run=True)

    assert result["dry_run"] is True
    assert result["matched"] == 2
    assert result["acked"] == 0
    assert len(result["items"]) == 2
    # state не изменился
    assert (tmp_path / "inbox.json").read_text(encoding="utf-8") == raw_before
    # все items остались open
    items = service.list_items(status="open")
    assert {row["item_id"] for row in items} == {"aaa", "bbb"}


def test_age_threshold_filters_younger_items(tmp_path: Path) -> None:
    """age_threshold отсекает свежие items, оставляя старше N часов."""
    now = datetime.now(timezone.utc)
    fresh = now - timedelta(hours=1)
    old = now - timedelta(hours=20)
    service = _build_service_with_items(
        tmp_path,
        [
            _make_item(item_id="fresh", kind="proactive_action", severity="info", created_at=fresh),
            _make_item(item_id="old1", kind="proactive_action", severity="info", created_at=old),
            _make_item(item_id="old2", kind="proactive_action", severity="info", created_at=old),
        ],
    )

    result = service.bulk_acknowledge_stale(age_threshold_hours=12, dry_run=False)

    assert result["matched"] == 2
    assert result["acked"] == 2
    matched_ids = {row["item_id"] for row in result["items"]}
    assert matched_ids == {"old1", "old2"}
    # fresh остался open, остальные — acked
    open_items = {
        row["item_id"]
        for row in service.list_items(status="all", limit=50)
        if row["status"] == "open"
    }
    assert "fresh" in open_items
    assert "old1" not in open_items
    assert "old2" not in open_items


def test_severity_filter_only_matches_requested_severity(tmp_path: Path) -> None:
    """severity-фильтр оставляет только items с указанной severity."""
    old = datetime.now(timezone.utc) - timedelta(hours=24)
    service = _build_service_with_items(
        tmp_path,
        [
            _make_item(
                item_id="warn1", kind="proactive_action", severity="warning", created_at=old
            ),
            _make_item(
                item_id="warn2", kind="proactive_action", severity="warning", created_at=old
            ),
            _make_item(item_id="info1", kind="proactive_action", severity="info", created_at=old),
        ],
    )

    result = service.bulk_acknowledge_stale(
        severity="warning", age_threshold_hours=12, dry_run=False
    )

    assert result["matched"] == 2
    assert result["acked"] == 2
    assert {row["item_id"] for row in result["items"]} == {"warn1", "warn2"}
    # info1 остался open
    open_items = {
        row["item_id"]
        for row in service.list_items(status="all", limit=50)
        if row["status"] == "open"
    }
    assert open_items == {"info1"}


def test_kind_filter_only_matches_requested_kind(tmp_path: Path) -> None:
    """kind-фильтр оставляет только items с указанным kind."""
    old = datetime.now(timezone.utc) - timedelta(hours=24)
    service = _build_service_with_items(
        tmp_path,
        [
            _make_item(item_id="pa1", kind="proactive_action", severity="info", created_at=old),
            _make_item(item_id="pa2", kind="proactive_action", severity="info", created_at=old),
            _make_item(item_id="rem1", kind="reminder", severity="info", created_at=old),
            _make_item(item_id="task1", kind="owner_task", severity="info", created_at=old),
        ],
    )

    result = service.bulk_acknowledge_stale(
        kind="proactive_action", age_threshold_hours=12, dry_run=False
    )

    assert result["matched"] == 2
    assert result["acked"] == 2
    assert {row["item_id"] for row in result["items"]} == {"pa1", "pa2"}
    open_items = {
        row["item_id"]
        for row in service.list_items(status="all", limit=50)
        if row["status"] == "open"
    }
    assert open_items == {"rem1", "task1"}


def test_target_status_done_writes_resolution_metadata(tmp_path: Path) -> None:
    """target_status='done' переводит items в closed-статус с resolution metadata."""
    old = datetime.now(timezone.utc) - timedelta(hours=24)
    service = _build_service_with_items(
        tmp_path,
        [_make_item(item_id="x1", kind="proactive_action", severity="info", created_at=old)],
    )

    result = service.bulk_acknowledge_stale(
        age_threshold_hours=12, dry_run=False, target_status="done", note="manual cleanup"
    )

    assert result["acked"] == 1
    item = result["items"][0]
    assert item["status"] == "done"
    assert item["metadata"].get("resolved_by") == "system-cleanup"
    assert item["metadata"].get("resolution_note") == "manual cleanup"
