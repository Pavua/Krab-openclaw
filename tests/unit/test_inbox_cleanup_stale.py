# -*- coding: utf-8 -*-
"""
Wave 34-C: Тесты для InboxService.cleanup_stale_open_items.

Проверяем:
1. cleanup без старых items → 0 archived
2. cleanup 5 старых info_alert → все 5 archived, status=cancelled
3. cleanup НЕ трогает критичные kinds (escalation, owner_request, owner_task)
4. cleanup НЕ трогает items ≤ max_age_days
5. dry_run=True → counts return, но _save_items не вызывается
6. by_kind статистика корректна при смешанных kinds
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.inbox_service import InboxIdentity, InboxItem, InboxService

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_item(
    *,
    item_id: str,
    kind: str,
    status: str = "open",
    age_days: float = 0,
) -> dict:
    """Собирает persisted JSON inbox item-а с заданным возрастом."""
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    iso = created.isoformat(timespec="seconds")
    item = InboxItem(
        item_id=item_id,
        dedupe_key=f"{kind}:{item_id}",
        kind=kind,
        source="test",
        status=status,
        severity="info",
        title=f"item {item_id}",
        body="test body",
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


def _build_service(tmp_path: Path, items: list[dict]) -> InboxService:
    state_path = tmp_path / "inbox_cleanup_test.json"
    _seed_state(state_path, items)
    return InboxService(state_path=state_path)


# ---------------------------------------------------------------------------
# Тест 1: нет старых items → 0 archived
# ---------------------------------------------------------------------------


def test_no_old_items_returns_zero_archived(tmp_path: Path) -> None:
    """cleanup при отсутствии старых items возвращает archived_count=0."""
    service = _build_service(
        tmp_path,
        [
            _make_item(item_id="new1", kind="info_alert", age_days=1),
            _make_item(item_id="new2", kind="weekly_digest", age_days=3),
        ],
    )

    result = service.cleanup_stale_open_items(max_age_days=7)

    assert result["archived_count"] == 0
    assert result["kept_count"] == 2
    assert result["by_kind"] == {}
    assert result["dry_run"] is False


# ---------------------------------------------------------------------------
# Тест 2: 5 старых info_alert → все 5 архивируются
# ---------------------------------------------------------------------------


def test_old_info_alerts_archived(tmp_path: Path) -> None:
    """5 старых open info_alert становятся status=cancelled."""
    state_path = tmp_path / "inbox.json"
    items = [_make_item(item_id=f"old{i}", kind="info_alert", age_days=10) for i in range(5)]
    _seed_state(state_path, items)
    service = InboxService(state_path=state_path)

    result = service.cleanup_stale_open_items(max_age_days=7)

    assert result["archived_count"] == 5
    assert result["kept_count"] == 0
    assert result["by_kind"] == {"info_alert": 5}

    # Проверяем что статус реально записан в файл
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    for item_dict in saved["items"]:
        assert item_dict["status"] == "cancelled", f"item {item_dict['item_id']} не archived"
        assert "auto_cleanup" in item_dict["metadata"].get("archive_reason", "")


# ---------------------------------------------------------------------------
# Тест 3: критичные kinds не трогаются
# ---------------------------------------------------------------------------


def test_critical_kinds_not_touched(tmp_path: Path) -> None:
    """escalation, owner_request, owner_task, approval_request, watch_alert остаются open."""
    critical_kinds = [
        "escalation",
        "owner_request",
        "owner_task",
        "approval_request",
        "watch_alert",
        "vpn_alert",
    ]
    items = [
        _make_item(item_id=f"crit{i}", kind=k, age_days=30)
        for i, k in enumerate(critical_kinds)
    ]
    service = _build_service(tmp_path, items)

    result = service.cleanup_stale_open_items(max_age_days=7)

    assert result["archived_count"] == 0
    assert result["kept_count"] == len(critical_kinds)
    assert result["by_kind"] == {}


# ---------------------------------------------------------------------------
# Тест 4: items ≤ max_age_days не трогаются
# ---------------------------------------------------------------------------


def test_fresh_items_not_archived(tmp_path: Path) -> None:
    """Items моложе max_age_days остаются open."""
    service = _build_service(
        tmp_path,
        [
            # Ровно на границе — НЕ должен архивироваться (< cutoff, не <=)
            _make_item(item_id="borderline", kind="info_alert", age_days=6.9),
            # Свежий
            _make_item(item_id="fresh", kind="weekly_digest", age_days=1),
        ],
    )

    result = service.cleanup_stale_open_items(max_age_days=7)

    assert result["archived_count"] == 0
    assert result["kept_count"] == 2


# ---------------------------------------------------------------------------
# Тест 5: dry_run — counts возвращаются, файл не изменяется
# ---------------------------------------------------------------------------


def test_dry_run_does_not_save(tmp_path: Path) -> None:
    """dry_run=True возвращает archived_count>0 но не пишет в файл."""
    state_path = tmp_path / "inbox.json"
    items = [_make_item(item_id=f"old{i}", kind="proactive_alert", age_days=14) for i in range(3)]
    _seed_state(state_path, items)
    service = InboxService(state_path=state_path)

    raw_before = state_path.read_text(encoding="utf-8")

    result = service.cleanup_stale_open_items(max_age_days=7, dry_run=True)

    assert result["dry_run"] is True
    assert result["archived_count"] == 3
    # Файл не должен измениться
    assert state_path.read_text(encoding="utf-8") == raw_before


# ---------------------------------------------------------------------------
# Тест 6: by_kind статистика при смешанных kinds
# ---------------------------------------------------------------------------


def test_by_kind_statistics_mixed(tmp_path: Path) -> None:
    """by_kind содержит корректные счётчики при нескольких kinds."""
    service = _build_service(
        tmp_path,
        [
            # Старые безопасные — будут archived
            _make_item(item_id="ia1", kind="info_alert", age_days=10),
            _make_item(item_id="ia2", kind="info_alert", age_days=12),
            _make_item(item_id="wd1", kind="weekly_digest", age_days=8),
            _make_item(item_id="ca1", kind="cron_acked", age_days=9),
            # Критичный — останется
            _make_item(item_id="or1", kind="owner_request", age_days=30),
            # Свежий безопасный — останется
            _make_item(item_id="new1", kind="auto_notification", age_days=2),
        ],
    )

    result = service.cleanup_stale_open_items(max_age_days=7)

    assert result["archived_count"] == 4
    assert result["kept_count"] == 2  # owner_request + свежий auto_notification
    assert result["by_kind"]["info_alert"] == 2
    assert result["by_kind"]["weekly_digest"] == 1
    assert result["by_kind"]["cron_acked"] == 1
    assert "owner_request" not in result["by_kind"]
    assert "auto_notification" not in result["by_kind"]


# ---------------------------------------------------------------------------
# Тест 7: не-open статусы не трогаются
# ---------------------------------------------------------------------------


def test_non_open_statuses_not_touched(tmp_path: Path) -> None:
    """done/cancelled/acked items не включаются в cleanup даже при правильном kind."""
    service = _build_service(
        tmp_path,
        [
            _make_item(item_id="done1", kind="info_alert", status="done", age_days=20),
            _make_item(item_id="acked1", kind="weekly_digest", status="acked", age_days=20),
            _make_item(item_id="open1", kind="info_alert", status="open", age_days=20),
        ],
    )

    result = service.cleanup_stale_open_items(max_age_days=7)

    # Только open item должен быть archived
    assert result["archived_count"] == 1
    assert result["kept_count"] == 2
