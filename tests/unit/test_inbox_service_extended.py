# -*- coding: utf-8 -*-
"""
Расширенные тесты inbox_service.

Покрываем:
1) upsert — создание, дедупликация, обновление body;
2) ack — смена статуса, persistance;
3) фильтрация по severity и status;
4) дедуп logic — повторный upsert не плодит items;
5) workflow events — trail записывается корректно;
6) attention items — escalation сигналы попадают в summary;
7) _normalize_status / _normalize_severity — валидация;
8) set_status_by_dedupe — обновление по ключу;
9) max_items cap — ротация при переполнении;
10) reminder с retries / last_error — severity и body;
11) resolve_reminder cancelled — статус cancelled;
12) escalate_item_to_owner_task — наследует trace, связывает source;
13) filter_by_age с kind/status фильтрами;
14) list_items без фильтров возвращает все статусы;
15) upsert_item сохраняет кастомный metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.core.inbox_service as inbox_service_module
from src.core.inbox_service import InboxService

# ──────────────────────────────────────────────────────────────────────────────
# 1. upsert — базовые сценарии
# ──────────────────────────────────────────────────────────────────────────────


def test_upsert_creates_new_item_with_correct_fields(tmp_path: Path) -> None:
    """Первый upsert должен создать item с нужными полями."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    result = service.upsert_item(
        dedupe_key="test:create",
        kind="watch_alert",
        source="unit-test",
        title="Тест создания",
        body="Тело item-а",
        severity="warning",
    )

    assert result["created"] is True
    item = result["item"]
    assert item["kind"] == "watch_alert"
    assert item["source"] == "unit-test"
    assert item["severity"] == "warning"
    assert item["status"] == "open"
    assert item["title"] == "Тест создания"
    assert item["body"] == "Тело item-а"
    assert item["item_id"]  # должен быть непустой UUID


def test_upsert_deduplicates_by_key_and_updates_body(tmp_path: Path) -> None:
    """Повторный upsert с тем же dedupe_key обновляет body, не плодит item."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    service.upsert_item(
        dedupe_key="dupe:key",
        kind="watch_alert",
        source="test",
        title="Заголовок",
        body="Первое тело",
        severity="info",
    )
    result2 = service.upsert_item(
        dedupe_key="dupe:key",
        kind="watch_alert",
        source="test",
        title="Заголовок",
        body="Обновлённое тело",
        severity="info",
    )

    assert result2["created"] is False
    assert result2["item"]["body"] == "Обновлённое тело"
    # В state ровно один item
    items = service.list_items()
    assert len(items) == 1


def test_upsert_persists_custom_metadata(tmp_path: Path) -> None:
    """Кастомные поля metadata должны сохраняться при upsert."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    service.upsert_item(
        dedupe_key="meta:test",
        kind="owner_task",
        source="test",
        title="Задача",
        body="Тело",
        severity="info",
        metadata={"custom_field": "значение", "numeric": 42},
    )

    items = service.list_items()
    assert items[0]["metadata"]["custom_field"] == "значение"
    assert items[0]["metadata"]["numeric"] == 42


# ──────────────────────────────────────────────────────────────────────────────
# 2. ack — смена статуса
# ──────────────────────────────────────────────────────────────────────────────


def test_set_item_status_ack_changes_status_and_persists(tmp_path: Path) -> None:
    """set_item_status acked должен менять статус и персистировать."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    item_id = service.upsert_item(
        dedupe_key="ack:test",
        kind="owner_request",
        source="test",
        title="Запрос",
        body="Тело",
        severity="info",
    )["item"]["item_id"]

    result = service.set_item_status(item_id, status="acked", actor="kraab", note="processing")

    assert result["ok"] is True
    assert result["item"]["status"] == "acked"
    # После reload статус также acked
    items = service.list_items(status="acked")
    assert len(items) == 1
    assert items[0]["item_id"] == item_id


def test_set_item_status_nonexistent_item_returns_error(tmp_path: Path) -> None:
    """set_item_status по несуществующему item_id должен вернуть ошибку."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    result = service.set_item_status("nonexistent-id", status="done", actor="test")

    assert result["ok"] is False
    assert result["error"] == "inbox_item_not_found"


# ──────────────────────────────────────────────────────────────────────────────
# 3. Фильтрация по severity и status
# ──────────────────────────────────────────────────────────────────────────────


def test_list_items_filters_by_severity_via_list_items_and_metadata(tmp_path: Path) -> None:
    """list_items должен возвращать items с нужным severity при ручном фильтре."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    service.upsert_item(
        dedupe_key="sev:error",
        kind="watch_alert",
        source="test",
        title="Error",
        body="b",
        severity="error",
    )
    service.upsert_item(
        dedupe_key="sev:info",
        kind="watch_alert",
        source="test",
        title="Info",
        body="b",
        severity="info",
    )

    # list_items не имеет severity-фильтра — проверяем через to_dict
    all_items = service.list_items()
    error_items = [i for i in all_items if i["severity"] == "error"]
    info_items = [i for i in all_items if i["severity"] == "info"]

    assert len(error_items) == 1
    assert len(info_items) == 1
    assert error_items[0]["dedupe_key"] == "sev:error"


def test_list_items_filters_closed_statuses_separately(tmp_path: Path) -> None:
    """list_items(status='done') должен возвращать только done-items, не open."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    open_id = service.upsert_item(
        dedupe_key="open:1",
        kind="watch_alert",
        source="test",
        title="Open",
        body="b",
        severity="info",
    )["item"]["item_id"]
    done_id = service.upsert_item(
        dedupe_key="done:1",
        kind="watch_alert",
        source="test",
        title="Done",
        body="b",
        severity="info",
    )["item"]["item_id"]
    service.set_item_status(done_id, status="done", actor="test")

    done_items = service.list_items(status="done")
    open_items = service.list_items(status="open")

    assert len(done_items) == 1
    assert done_items[0]["item_id"] == done_id
    assert len(open_items) == 1
    assert open_items[0]["item_id"] == open_id


def test_list_items_no_filter_returns_all_statuses(tmp_path: Path) -> None:
    """list_items() без фильтров должен возвращать items всех статусов."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    id1 = service.upsert_item(
        dedupe_key="all:1", kind="watch_alert", source="test", title="T1", body="b", severity="info"
    )["item"]["item_id"]
    id2 = service.upsert_item(
        dedupe_key="all:2", kind="watch_alert", source="test", title="T2", body="b", severity="info"
    )["item"]["item_id"]
    service.set_item_status(id2, status="done", actor="test")

    all_items = service.list_items(limit=10)

    statuses = {i["status"] for i in all_items}
    assert "open" in statuses
    assert "done" in statuses


# ──────────────────────────────────────────────────────────────────────────────
# 4. Валидация — normalize_status / normalize_severity
# ──────────────────────────────────────────────────────────────────────────────


def test_normalize_status_raises_on_invalid_value(tmp_path: Path) -> None:
    """_normalize_status должен бросать ValueError для неизвестного статуса."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    with pytest.raises(ValueError, match="inbox_invalid_status"):
        service._normalize_status("unknown_status")


def test_normalize_severity_raises_on_invalid_value(tmp_path: Path) -> None:
    """_normalize_severity должен бросать ValueError для неизвестного severity."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    with pytest.raises(ValueError, match="inbox_invalid_severity"):
        service._normalize_severity("critical")


def test_normalize_status_accepts_all_valid_statuses(tmp_path: Path) -> None:
    """_normalize_status должен принимать все допустимые статусы."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    for status in ("open", "acked", "done", "cancelled", "approved", "rejected"):
        assert service._normalize_status(status) == status


# ──────────────────────────────────────────────────────────────────────────────
# 5. Workflow events — trail записывается
# ──────────────────────────────────────────────────────────────────────────────


def test_workflow_events_trail_appended_on_status_change(tmp_path: Path) -> None:
    """Каждая смена статуса должна добавлять событие в workflow_events."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    item_id = service.upsert_item(
        dedupe_key="trail:test",
        kind="owner_task",
        source="test",
        title="Задача",
        body="Тело",
        severity="info",
    )["item"]["item_id"]

    service.set_item_status(item_id, status="acked", actor="kraab", note="начало")
    result = service.set_item_status(item_id, status="done", actor="owner-ui", note="завершено")

    events = result["item"]["metadata"]["workflow_events"]
    assert len(events) >= 1
    # Последний (самый свежий) — done
    latest = events[0]
    assert latest["action"] == "done"
    assert latest["actor"] == "owner-ui"
    assert latest["note"] == "завершено"


def test_append_workflow_event_caps_at_max_events(tmp_path: Path) -> None:
    """workflow_events не должен расти бесконечно — ограничение max_events=12."""
    metadata: dict = {}
    for i in range(20):
        metadata = InboxService._append_workflow_event(
            metadata,
            action=f"step_{i}",
            actor="test",
            status="open",
            max_events=5,
        )

    assert len(metadata["workflow_events"]) == 5
    # Первым стоит самое свежее событие
    assert metadata["workflow_events"][0]["action"] == "step_19"


# ──────────────────────────────────────────────────────────────────────────────
# 6. Attention items — summary
# ──────────────────────────────────────────────────────────────────────────────


def test_summary_attention_items_counts_error_and_warning(tmp_path: Path) -> None:
    """summary['attention_items'] должен включать error и warning severity."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    service.upsert_item(
        dedupe_key="att:error",
        kind="watch_alert",
        source="test",
        title="Error Alert",
        body="b",
        severity="error",
    )
    service.upsert_item(
        dedupe_key="att:warning",
        kind="watch_alert",
        source="test",
        title="Warning Alert",
        body="b",
        severity="warning",
    )
    service.upsert_item(
        dedupe_key="att:info",
        kind="watch_alert",
        source="test",
        title="Info Alert",
        body="b",
        severity="info",
    )

    summary = service.get_summary()

    # error + warning = 2 attention items
    assert summary["attention_items"] >= 2


# ──────────────────────────────────────────────────────────────────────────────
# 7. set_status_by_dedupe
# ──────────────────────────────────────────────────────────────────────────────


def test_set_status_by_dedupe_updates_item(tmp_path: Path) -> None:
    """set_status_by_dedupe должен находить item по dedupe_key и обновлять статус."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    service.upsert_item(
        dedupe_key="dedupe:update",
        kind="watch_alert",
        source="test",
        title="T",
        body="b",
        severity="info",
    )

    result = service.set_status_by_dedupe("dedupe:update", status="done", actor="system", note="ok")

    assert result["ok"] is True
    assert result["item"]["status"] == "done"
    assert result["item"]["metadata"]["resolved_by"] == "system"
    assert result["item"]["metadata"]["resolution_note"] == "ok"


def test_set_status_by_dedupe_returns_error_for_missing_key(tmp_path: Path) -> None:
    """set_status_by_dedupe по несуществующему ключу должен вернуть ошибку."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    result = service.set_status_by_dedupe("nonexistent:key", status="done")

    assert result["ok"] is False
    assert result["error"] == "inbox_item_not_found"


# ──────────────────────────────────────────────────────────────────────────────
# 8. max_items cap
# ──────────────────────────────────────────────────────────────────────────────


def test_max_items_cap_rotates_oldest_items(tmp_path: Path) -> None:
    """При превышении max_items старые items должны вытесняться.

    Конструктор применяет max(20, max_items), поэтому используем 25 items с лимитом 20.
    """
    service = InboxService(state_path=tmp_path / "inbox.json", max_items=20)

    for i in range(25):
        service.upsert_item(
            dedupe_key=f"cap:item:{i}",
            kind="watch_alert",
            source="test",
            title=f"Item {i}",
            body=f"b{i}",
            severity="info",
        )

    items = service.list_items(limit=30)
    assert len(items) <= 20


# ──────────────────────────────────────────────────────────────────────────────
# 9. Reminder с retries / last_error
# ──────────────────────────────────────────────────────────────────────────────


def test_upsert_reminder_with_retries_has_warning_severity(tmp_path: Path) -> None:
    """Reminder с retries > 0 должен иметь severity='warning'."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    service.upsert_reminder(
        reminder_id="retry:001",
        chat_id="123",
        text="Тест retries",
        due_at_iso="2026-05-01T10:00:00+00:00",
        retries=2,
        last_error="timeout",
    )

    items = service.list_items(kind="reminder")
    assert len(items) == 1
    assert items[0]["severity"] == "warning"
    assert "Повторные попытки" in items[0]["body"]
    assert "timeout" in items[0]["body"]


def test_resolve_reminder_with_cancelled_status(tmp_path: Path) -> None:
    """resolve_reminder(status='cancelled') должен закрывать item со статусом cancelled."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    service.upsert_reminder(
        reminder_id="cancel:001",
        chat_id="123",
        text="Отменить",
        due_at_iso="2026-05-01T10:00:00+00:00",
    )

    result = service.resolve_reminder("cancel:001", status="cancelled")

    assert result["ok"] is True
    assert result["item"]["status"] == "cancelled"


# ──────────────────────────────────────────────────────────────────────────────
# 10. escalate_item_to_owner_task
# ──────────────────────────────────────────────────────────────────────────────


def test_escalate_item_to_owner_task_inherits_trace_and_links_source(tmp_path: Path) -> None:
    """Эскалация mention в owner_task должна наследовать trace_id и связывать source."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    source = service.upsert_incoming_owner_request(
        chat_id="-100777",
        message_id="55",
        text="Краб, создай задачу",
        sender_username="owner",
        chat_type="group",
        is_reply_to_me=True,
        has_trigger=True,
    )["item"]

    result = service.escalate_item_to_owner_task(
        source_item_id=source["item_id"],
        title="Задача из mention",
        body="Детали задачи",
        task_key="mention-task",
        source="owner-ui",
    )

    assert result["ok"] is True
    task = result["item"]
    # Trace наследуется от источника
    assert task["identity"]["trace_id"] == source["identity"]["trace_id"]
    # Metadata связывает с source
    assert task["metadata"]["source_item_id"] == source["item_id"]
    assert task["metadata"]["source_kind"] == "owner_mention"


# ──────────────────────────────────────────────────────────────────────────────
# 11. filter_by_age с kind/status фильтрами
# ──────────────────────────────────────────────────────────────────────────────


def test_filter_by_age_with_kind_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_by_age с kind-фильтром должен возвращать только items нужного типа."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    monkeypatch.setattr(inbox_service_module, "_now_utc_iso", lambda: "2026-01-10T00:00:00+00:00")
    service.upsert_item(
        dedupe_key="old:alert",
        kind="watch_alert",
        source="test",
        title="Old Alert",
        body="b",
        severity="info",
    )
    service.upsert_item(
        dedupe_key="old:task",
        kind="owner_task",
        source="test",
        title="Old Task",
        body="b",
        severity="info",
    )

    result = service.filter_by_age(
        older_than_date="2026-02-01T00:00:00+00:00",
        kind="watch_alert",
    )

    assert len(result) == 1
    assert result[0]["kind"] == "watch_alert"
    assert result[0]["dedupe_key"] == "old:alert"


def test_filter_by_age_with_status_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_by_age с status-фильтром должен учитывать статус items."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    monkeypatch.setattr(inbox_service_module, "_now_utc_iso", lambda: "2026-01-05T00:00:00+00:00")
    id1 = service.upsert_item(
        dedupe_key="old:open",
        kind="watch_alert",
        source="test",
        title="Old Open",
        body="b",
        severity="info",
    )["item"]["item_id"]
    id2 = service.upsert_item(
        dedupe_key="old:done",
        kind="watch_alert",
        source="test",
        title="Old Done",
        body="b",
        severity="info",
    )["item"]["item_id"]
    service.set_item_status(id2, status="done", actor="test")

    open_results = service.filter_by_age(
        older_than_date="2026-02-01T00:00:00+00:00",
        status="open",
    )

    # Только open item должен попасть
    item_ids = [r["item_id"] for r in open_results]
    assert id1 in item_ids
    assert id2 not in item_ids
