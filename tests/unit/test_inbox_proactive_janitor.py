# -*- coding: utf-8 -*-
"""
Tests для `InboxService.sweep_acked_proactive_actions`.

Wave 8-A + Wave 9-C janitor: переводит items из `acked` → `done`, если они
старше порога и kind входит в `_AUTO_SWEEP_KINDS` allowlist
(``proactive_action`` + ``owner_request``). Safety net против накопления
stale_processing items.

Проверяем:
- старые `acked` proactive_action sweep'ятся в `done` (Wave 8-A);
- старые `acked` owner_request sweep'ятся в `done` (Wave 9-C);
- свежие (< 1ч) `acked` НЕ трогаются;
- approval_request / reminder / owner_task НЕ трогаются (явный human review);
- janitor идемпотентен (повторный вызов = noop);
- dry_run режим не мутирует state.
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
    status: str,
    activity_at: datetime,
    severity: str = "info",
) -> dict:
    """Собирает persisted JSON inbox item-а с заданным last_action_at_utc."""
    iso = activity_at.isoformat(timespec="seconds")
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
        metadata={"last_action_at_utc": iso},
    )
    return item.to_dict()


def _seed(path: Path, items: list[dict]) -> None:
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _service(tmp_path: Path, items: list[dict]) -> InboxService:
    state_path = tmp_path / "inbox.json"
    _seed(state_path, items)
    return InboxService(state_path=state_path)


def test_janitor_sweeps_old_acked_proactive(tmp_path: Path) -> None:
    """Старые `acked` proactive_action item-ы переходят в `done`."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=4)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="old1", kind="proactive_action", status="acked", activity_at=old
            ),
            _make_item(
                item_id="old2", kind="proactive_action", status="acked", activity_at=old
            ),
        ],
    )

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    assert result["matched"] == 2
    assert result["swept"] == 2
    assert result["dry_run"] is False
    items_after = service.list_items(status="all", limit=100)
    assert all(it["status"] == "done" for it in items_after)
    # Workflow event записан
    for it in items_after:
        events = it["metadata"].get("workflow_events") or []
        assert any(e.get("action") == "janitor_sweep" for e in events)


def test_janitor_doesnt_touch_recent(tmp_path: Path) -> None:
    """Свежие (< threshold) `acked` items остаются `acked`."""
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=5)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="fresh", kind="proactive_action", status="acked", activity_at=recent
            ),
        ],
    )

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    assert result["matched"] == 0
    assert result["swept"] == 0
    items_after = service.list_items(status="all", limit=100)
    assert items_after[0]["status"] == "acked"


def test_janitor_doesnt_touch_other_kinds(tmp_path: Path) -> None:
    """approval_request / reminder / owner_task НЕ трогаются (требуют human review)."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=4)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="appr", kind="approval_request", status="acked", activity_at=old
            ),
            _make_item(
                item_id="rem", kind="reminder", status="acked", activity_at=old
            ),
            _make_item(
                item_id="otask", kind="owner_task", status="acked", activity_at=old
            ),
            _make_item(
                item_id="prox", kind="proactive_action", status="acked", activity_at=old
            ),
            _make_item(
                item_id="oreq", kind="owner_request", status="acked", activity_at=old
            ),
        ],
    )

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    # Sweep'нуты только allowlist: proactive_action + owner_request
    assert result["matched"] == 2
    assert result["swept"] == 2
    items_after = {it["item_id"]: it["status"] for it in service.list_items(status="all", limit=100)}
    assert items_after["appr"] == "acked"
    assert items_after["rem"] == "acked"
    assert items_after["otask"] == "acked"
    assert items_after["prox"] == "done"
    assert items_after["oreq"] == "done"


def test_janitor_sweeps_owner_request(tmp_path: Path) -> None:
    """Wave 9-C: старые `acked` owner_request item-ы переходят в `done`."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=2)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="oreq1", kind="owner_request", status="acked", activity_at=old
            ),
            _make_item(
                item_id="oreq2", kind="owner_request", status="acked", activity_at=old
            ),
        ],
    )

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    assert result["matched"] == 2
    assert result["swept"] == 2
    items_after = service.list_items(status="all", limit=100)
    assert all(it["status"] == "done" for it in items_after)
    for it in items_after:
        events = it["metadata"].get("workflow_events") or []
        assert any(e.get("action") == "janitor_sweep" for e in events)


def test_janitor_doesnt_touch_approval(tmp_path: Path) -> None:
    """approval_request требует explicit human ack — janitor НЕ trogает."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=7)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="appr_old",
                kind="approval_request",
                status="acked",
                activity_at=old,
            ),
        ],
    )

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    assert result["matched"] == 0
    assert result["swept"] == 0
    items_after = service.list_items(status="all", limit=100)
    assert items_after[0]["status"] == "acked"


def test_janitor_doesnt_touch_reminder(tmp_path: Path) -> None:
    """reminder — user-scheduled, sweep = data loss. НЕ трогаем."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=3)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="rem_old", kind="reminder", status="acked", activity_at=old
            ),
        ],
    )

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    assert result["matched"] == 0
    assert result["swept"] == 0
    items_after = service.list_items(status="all", limit=100)
    assert items_after[0]["status"] == "acked"


def test_janitor_explicit_kind_outside_allowlist_falls_back(tmp_path: Path) -> None:
    """
    Защита от ошибочного вызова: если kind задан явно но НЕ в allowlist,
    janitor использует весь allowlist (а не пытается sweep'нуть запрещённый kind).
    """
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=4)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="appr",
                kind="approval_request",
                status="acked",
                activity_at=old,
            ),
            _make_item(
                item_id="prox",
                kind="proactive_action",
                status="acked",
                activity_at=old,
            ),
        ],
    )

    # Попытка sweep'нуть `approval_request` явно → fallback на allowlist
    result = service.sweep_acked_proactive_actions(
        kind="approval_request", age_threshold_minutes=60
    )

    items_after = {it["item_id"]: it["status"] for it in service.list_items(status="all", limit=100)}
    # approval_request НЕ задели, proactive_action sweep'нут (allowlist использован)
    assert items_after["appr"] == "acked"
    assert items_after["prox"] == "done"
    assert result["swept"] == 1


def test_janitor_doesnt_touch_open_or_done(tmp_path: Path) -> None:
    """`open` и `done` items не считаются кандидатами."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=4)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="open1", kind="proactive_action", status="open", activity_at=old
            ),
            _make_item(
                item_id="done1", kind="proactive_action", status="done", activity_at=old
            ),
        ],
    )

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    assert result["matched"] == 0
    assert result["swept"] == 0


def test_janitor_idempotent(tmp_path: Path) -> None:
    """Повторный вызов janitor не делает ничего (все уже done)."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=4)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="x", kind="proactive_action", status="acked", activity_at=old
            ),
        ],
    )

    first = service.sweep_acked_proactive_actions(age_threshold_minutes=60)
    second = service.sweep_acked_proactive_actions(age_threshold_minutes=60)

    assert first["swept"] == 1
    assert second["swept"] == 0
    assert second["matched"] == 0


def test_janitor_dry_run_does_not_mutate(tmp_path: Path) -> None:
    """dry_run режим не меняет persisted state."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=4)
    service = _service(
        tmp_path,
        [
            _make_item(
                item_id="a", kind="proactive_action", status="acked", activity_at=old
            ),
        ],
    )
    raw_before = (tmp_path / "inbox.json").read_text(encoding="utf-8")

    result = service.sweep_acked_proactive_actions(age_threshold_minutes=60, dry_run=True)

    assert result["dry_run"] is True
    assert result["matched"] == 1
    assert result["swept"] == 0
    # state нетронут
    raw_after = (tmp_path / "inbox.json").read_text(encoding="utf-8")
    assert raw_before == raw_after


def test_proactive_cron_marks_done_on_success(tmp_path: Path) -> None:
    """
    Контракт: успешный cron создаёт item с status=done сразу
    (через `proactive_watch._check_and_trace_cron_executions`).

    Этот тест документирует/защищает текущий behaviour: cron success path в
    `proactive_watch.py:380` ставит `item_status = "done" if is_ok else "open"`.
    Если кто-то изменит это на `acked` — janitor подберёт через 1ч, но мы
    хотим, чтобы первичный путь оставался корректным.
    """
    from src.core import proactive_watch as pw_module

    # Просто проверяем константу в коде через импорт исходника.
    src = Path(pw_module.__file__).read_text(encoding="utf-8")
    # Проверяем что есть строка с item_status = "done" if is_ok else "open"
    assert 'item_status = "done" if is_ok else "open"' in src, (
        "Cron success path должен ставить status=done для is_ok, "
        "иначе items накапливаются как acked/open"
    )
