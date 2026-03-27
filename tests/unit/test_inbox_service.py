# -*- coding: utf-8 -*-
"""
Тесты persisted inbox / escalation foundation.

Покрываем:
1) upsert и summary работают детерминированно;
2) reminder lifecycle отражается как pending -> done/cancelled;
3) watch escalation открывается и закрывается без дублей;
4) default state-path живёт в per-account `~/.openclaw`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.core.inbox_service as inbox_service_module
from src.core.inbox_service import InboxService


def test_default_state_path_uses_openclaw_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Inbox-state должен жить в per-account `~/.openclaw`, а не в shared repo."""
    monkeypatch.setattr(inbox_service_module.Path, "home", classmethod(lambda cls: tmp_path))

    service = InboxService()

    assert service.state_path == tmp_path / ".openclaw" / "krab_runtime_state" / "inbox_state.json"


def test_upsert_item_persists_and_updates_summary(tmp_path: Path) -> None:
    """Новый open item должен появляться в summary и обновляться по dedupe_key."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    first = service.upsert_item(
        dedupe_key="watch:gateway_down",
        kind="watch_alert",
        source="proactive-watch",
        title="Gateway недоступен",
        body="gateway down",
        severity="error",
    )
    second = service.upsert_item(
        dedupe_key="watch:gateway_down",
        kind="watch_alert",
        source="proactive-watch",
        title="Gateway недоступен",
        body="gateway down again",
        severity="error",
    )

    summary = service.get_summary()

    assert first["created"] is True
    assert second["created"] is False
    assert summary["open_items"] == 1
    assert summary["attention_items"] == 1
    assert summary["open_escalations"] == 1
    assert summary["latest_open_items"][0]["body"] == "gateway down again"


def test_reminder_lifecycle_moves_item_from_open_to_done(tmp_path: Path) -> None:
    """Reminder должен открывать pending item и закрываться после выполнения."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    service.upsert_reminder(
        reminder_id="abc123",
        chat_id="-100777",
        text="проверить поставку",
        due_at_iso="2026-03-12T10:00:00+00:00",
    )
    open_summary = service.get_summary()
    closed = service.resolve_reminder("abc123", status="done")
    done_items = service.list_items(status="done", kind="reminder", limit=5)

    assert open_summary["open_items"] == 1
    assert open_summary["pending_reminders"] == 1
    assert closed["ok"] is True
    assert done_items
    assert done_items[0]["metadata"]["reminder_id"] == "abc123"
    assert done_items[0]["status"] == "done"


def test_watch_escalation_opens_and_recovers_without_duplicate_items(tmp_path: Path) -> None:
    """`gateway_down -> gateway_recovered` должен открывать и закрывать один и тот же escalation item."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    opened = service.report_watch_transition(
        reason="gateway_down",
        digest="Gateway OFF",
        snapshot={"gateway_ok": False},
    )
    recovered = service.report_watch_transition(
        reason="gateway_recovered",
        digest="Gateway ON",
        snapshot={"gateway_ok": True},
    )
    open_items = service.list_items(status="open", kind="watch_alert", limit=5)
    done_items = service.list_items(status="done", kind="watch_alert", limit=5)

    assert opened["ok"] is True
    assert opened["item"]["identity"]["trace_id"].startswith("watch:")
    assert recovered["ok"] is True
    assert open_items == []
    assert len(done_items) == 1
    assert done_items[0]["dedupe_key"] == "watch:gateway_down"


def test_owner_task_and_approval_request_appear_in_summary(tmp_path: Path) -> None:
    """Owner-task и approval-request должны попадать в summary и корректно закрываться."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    task = service.upsert_owner_task(
        title="Проверить transport regression",
        body="Нужен прогон reserve-safe E2E после restart.",
        task_key="transport-regression",
        source="owner-ui",
    )
    approval = service.upsert_approval_request(
        title="Разрешить платный provider",
        body="Нужен платный cloud route для production smoke.",
        request_key="cloud-paid-route",
        approval_scope="money",
        requested_action="enable_paid_cloud_route",
        metadata={"impact": "cost"},
    )
    summary = service.get_summary()
    approved = service.resolve_approval(approval["item"]["item_id"], approved=True)

    assert task["item"]["kind"] == "owner_task"
    assert approval["item"]["kind"] == "approval_request"
    assert summary["pending_owner_tasks"] == 1
    assert summary["pending_approvals"] == 1
    assert approval["item"]["identity"]["approval_scope"] == "money"
    assert approved["item"]["status"] == "approved"


def test_resolve_approval_rejects_non_approval_item(tmp_path: Path) -> None:
    """Owner-task нельзя случайно закрыть как approval-request."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    task = service.upsert_owner_task(
        title="Проверить reserve bot",
        body="Нужен smoke.",
        task_key="reserve-bot-smoke",
    )

    result = service.resolve_approval(task["item"]["item_id"], approved=True)

    assert result["ok"] is False
    assert result["error"] == "inbox_item_not_approval"


def test_incoming_owner_request_and_mention_update_summary_without_duplicates(tmp_path: Path) -> None:
    """Incoming request/mention должны попадать в отдельные summary buckets и не дублироваться по message id."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    first = service.upsert_incoming_owner_request(
        chat_id="123",
        message_id="10",
        text="Проверь transport после restart",
        sender_id="42",
        sender_username="owner",
        chat_type="private",
    )
    second = service.upsert_incoming_owner_request(
        chat_id="-100777",
        message_id="11",
        text="Краб, посмотри этот тред",
        sender_id="42",
        sender_username="owner",
        chat_type="group",
        is_reply_to_me=True,
        has_trigger=True,
    )
    repeated = service.upsert_incoming_owner_request(
        chat_id="123",
        message_id="10",
        text="Проверь transport после restart",
        sender_id="42",
        sender_username="owner",
        chat_type="private",
    )
    summary = service.get_summary()

    assert first["created"] is True
    assert second["item"]["kind"] == "owner_mention"
    assert repeated["created"] is False
    assert summary["pending_owner_requests"] == 1
    assert summary["pending_owner_mentions"] == 1


def test_summary_splits_fresh_and_acked_owner_items(tmp_path: Path) -> None:
    """Summary должен различать новые owner items и уже взятые в background processing."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    request = service.upsert_incoming_owner_request(
        chat_id="123",
        message_id="10",
        text="Первый запрос",
        sender_username="owner",
        chat_type="private",
    )
    mention = service.upsert_incoming_owner_request(
        chat_id="-100777",
        message_id="11",
        text="Второй запрос",
        sender_username="owner",
        chat_type="group",
        is_reply_to_me=True,
        has_trigger=True,
    )
    service.set_item_status(
        request["item"]["item_id"],
        status="acked",
        actor="kraab",
        note="background_processing_started",
    )
    service.set_item_status(
        mention["item"]["item_id"],
        status="acked",
        actor="kraab",
        note="background_processing_started",
    )

    summary = service.get_summary()

    assert summary["open_items"] == 2
    assert summary["fresh_open_items"] == 0
    assert summary["acked_items"] == 2
    assert summary["pending_owner_requests"] == 1
    assert summary["new_owner_requests"] == 0
    assert summary["processing_owner_requests"] == 1
    assert summary["pending_owner_mentions"] == 1
    assert summary["new_owner_mentions"] == 0
    assert summary["processing_owner_mentions"] == 1


def test_summary_marks_old_acked_items_as_stale_processing(tmp_path: Path) -> None:
    """Summary должен отдельно считать `acked` item-ы, которые реально застряли."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    request = service.upsert_incoming_owner_request(
        chat_id="123",
        message_id="10",
        text="Зависший owner request",
        sender_username="owner",
        chat_type="private",
    )
    mention = service.upsert_incoming_owner_request(
        chat_id="-100777",
        message_id="11",
        text="Свежий owner mention",
        sender_username="owner",
        chat_type="group",
        is_reply_to_me=True,
        has_trigger=True,
    )
    service.set_item_status(
        request["item"]["item_id"],
        status="acked",
        actor="kraab",
        note="background_processing_started",
    )
    service.set_item_status(
        mention["item"]["item_id"],
        status="acked",
        actor="kraab",
        note="background_processing_started",
    )

    state = service._load_state()
    stale_timestamp = (
        datetime.now(timezone.utc) - InboxService._stale_processing_after - timedelta(minutes=1)
    ).isoformat(timespec="seconds")
    for item in state["items"]:
        if item["item_id"] != request["item"]["item_id"]:
            continue
        item["updated_at_utc"] = stale_timestamp
        item.setdefault("metadata", {})["last_action_at_utc"] = stale_timestamp
    service.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = service.get_summary()

    assert summary["acked_items"] == 2
    assert summary["stale_processing_items"] == 1
    assert summary["processing_owner_requests"] == 1
    assert summary["stale_processing_owner_requests"] == 1
    assert summary["processing_owner_mentions"] == 1
    assert summary["stale_processing_owner_mentions"] == 0


def test_workflow_snapshot_exposes_trace_index_and_approval_history(tmp_path: Path) -> None:
    """Workflow snapshot должен собирать компактные buckets и traceable approval history."""
    service = InboxService(state_path=tmp_path / "inbox.json")

    approval = service.upsert_approval_request(
        title="Разрешить cloud route",
        body="Нужен production smoke.",
        request_key="cloud-route",
        approval_scope="money",
        requested_action="enable_paid_cloud_route",
    )["item"]
    service.resolve_approval(approval["item_id"], approved=True)
    service.upsert_owner_task(
        title="Проверить reserve delivery",
        body="Нужен round-trip после restart.",
        task_key="reserve-delivery",
    )
    service.upsert_incoming_owner_request(
        chat_id="123",
        message_id="77",
        text="Проверь handoff truth",
        sender_username="owner",
        chat_type="private",
    )
    workflow = service.get_workflow_snapshot(limit_per_bucket=3, trace_limit=6)

    assert workflow["summary"]["pending_owner_tasks"] == 1
    assert workflow["summary"]["pending_owner_requests"] == 1
    assert workflow["approval_history"][0]["status"] == "approved"
    assert workflow["approval_history"][0]["identity"]["approval_scope"] == "money"
    assert workflow["recent_approval_decisions"][0]["metadata"]["approval_decision"] == "approved"
    assert workflow["recent_owner_actions"][0]["action"] == "approved"
    assert workflow["pending_owner_tasks"][0]["metadata"]["task_key"] == "reserve-delivery"
    assert workflow["incoming_owner_requests"][0]["metadata"]["message_id"] == "77"
    assert workflow["trace_index"]
    assert workflow["trace_index"][0]["trace_id"]


def test_record_incoming_owner_reply_persists_reply_and_recent_activity(tmp_path: Path) -> None:
    """Reply trail должен связывать owner request с фактом ответа и recent activity."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    service.upsert_incoming_owner_request(
        chat_id="321",
        message_id="99",
        text="Проверь runtime handoff",
        sender_username="owner",
        chat_type="private",
    )

    result = service.record_incoming_owner_reply(
        chat_id="321",
        message_id="99",
        response_text="Handoff truth синхронизирован.",
        delivery_mode="edit_and_reply",
        reply_message_ids=["501", "502"],
        note="llm_response_delivered",
    )
    workflow = service.get_workflow_snapshot(limit_per_bucket=3, trace_limit=6)

    assert result["ok"] is True
    assert result["item"]["status"] == "done"
    assert result["item"]["metadata"]["reply_delivery_mode"] == "edit_and_reply"
    assert result["item"]["metadata"]["reply_message_ids"] == ["501", "502"]
    assert workflow["recent_replied_requests"][0]["metadata"]["reply_excerpt"] == "Handoff truth синхронизирован."
    assert workflow["recent_activity"][0]["action"] == "reply_sent"
    assert workflow["recent_activity"][0]["note"] == "llm_response_delivered"


def test_record_relay_delivery_closes_relay_request_and_persists_delivery_metadata(tmp_path: Path) -> None:
    """Успешный relay в Saved Messages должен закрывать relay_request как выполненный."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    service.upsert_item(
        dedupe_key="relay:312322764:11402",
        kind="relay_request",
        source="telegram-userbot",
        title="📨 Relay от @p0lrd",
        body="Relay body",
        severity="warning",
        status="open",
        identity=service.build_identity(
            channel_id="312322764",
            team_id="owner",
            trace_id="relay:test",
            approval_scope="owner",
        ),
        metadata={
            "chat_id": "312322764",
            "message_id": "11402",
            "sender_username": "p0lrd",
        },
    )

    result = service.record_relay_delivery(
        chat_id="312322764",
        message_id="11402",
        notification_text="Relay доставлен владельцу",
        delivery_mode="saved_messages",
        delivered_to_chat_id="6435872621",
        relay_message_ids=["555"],
        actor="kraab",
        note="relay_owner_notified",
    )
    relay_items = service.list_items(kind="relay_request", limit=5)

    assert result["ok"] is True
    assert result["item"]["status"] == "done"
    assert relay_items[0]["metadata"]["relay_delivery_mode"] == "saved_messages"
    assert relay_items[0]["metadata"]["relay_target_chat_id"] == "6435872621"
    assert relay_items[0]["metadata"]["relay_message_ids"] == ["555"]
    assert relay_items[0]["metadata"]["resolution_note"] == "relay_owner_notified"
    assert relay_items[0]["metadata"]["workflow_events"][0]["action"] == "relay_sent"


def test_set_item_status_persists_owner_resolution_metadata(tmp_path: Path) -> None:
    """Owner-action должен оставлять resolution metadata и попадать в recent owner actions."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    task = service.upsert_owner_task(
        title="Проверить reserve-safe режим",
        body="Нужен smoke.",
        task_key="reserve-safe",
    )["item"]

    result = service.set_item_status(
        task["item_id"],
        status="done",
        actor="owner-ui",
        note="smoke подтвержден",
    )
    workflow = service.get_workflow_snapshot(limit_per_bucket=3, trace_limit=6)

    assert result["ok"] is True
    assert result["item"]["metadata"]["resolved_by"] == "owner-ui"
    assert result["item"]["metadata"]["resolution_note"] == "smoke подтвержден"
    assert workflow["recent_owner_actions"][0]["actor"] == "owner-ui"
    assert workflow["recent_owner_actions"][0]["note"] == "smoke подтвержден"


def test_escalate_owner_mention_to_followup_preserves_trace_and_links_source(tmp_path: Path) -> None:
    """Эскалация mention/request должна наследовать trace и оставлять link на исходный item."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    source = service.upsert_incoming_owner_request(
        chat_id="-100777",
        message_id="11",
        text="Краб, вынеси это в approval",
        sender_username="owner",
        chat_type="group",
        is_reply_to_me=True,
        has_trigger=True,
    )["item"]

    followup = service.escalate_item_to_approval_request(
        source_item_id=source["item_id"],
        title="Разрешить внешний API",
        body="Нужен budget approval для mention-flow.",
        request_key="mention-approval",
        source="owner-ui",
        approval_scope="money",
        requested_action="enable_external_api",
    )
    workflow = service.get_workflow_snapshot(limit_per_bucket=4, trace_limit=8)
    source_rows = service.list_items(kind="owner_mention", limit=5)

    assert followup["ok"] is True
    assert followup["item"]["identity"]["trace_id"] == source["identity"]["trace_id"]
    assert followup["item"]["metadata"]["source_item_id"] == source["item_id"]
    assert followup["item"]["metadata"]["source_kind"] == "owner_mention"
    assert source_rows[0]["metadata"]["followup_count"] == 1
    assert source_rows[0]["metadata"]["followup_latest_kind"] == "approval_request"
    assert workflow["escalated_owner_items"][0]["item_id"] == source["item_id"]
    assert workflow["linked_followups"][0]["metadata"]["source_item_id"] == source["item_id"]


# ──────────────────────────────────────────────────────────────────────────────
# Task 1.6 — bulk_update_status / filter_by_age / archive_by_kind / open filter
# ──────────────────────────────────────────────────────────────────────────────


def test_bulk_update_status_updates_multiple_items(tmp_path: Path) -> None:
    """bulk_update_status должен обновить все переданные items за один вызов."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    id1 = service.upsert_item(
        dedupe_key="bulk-1", kind="watch_alert", source="test", title="T1", body="b1", severity="info"
    )["item"]["item_id"]
    id2 = service.upsert_item(
        dedupe_key="bulk-2", kind="watch_alert", source="test", title="T2", body="b2", severity="info"
    )["item"]["item_id"]

    result = service.bulk_update_status(
        item_ids=[id1, id2], status="done", actor="test-actor", note="batch close"
    )

    assert result["ok"] is True
    assert result["success_count"] == 2
    assert result["error_count"] == 0
    done_items = service.list_items(status="done", kind="watch_alert", limit=5)
    assert len(done_items) == 2


def test_bulk_update_status_exceeds_batch_size(tmp_path: Path) -> None:
    """Превышение max_batch_size должно возвращать ошибку без обновлений."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    result = service.bulk_update_status(
        item_ids=["id1", "id2", "id3", "id4", "id5"], status="done", max_batch_size=3
    )
    assert result["ok"] is False
    assert "batch_size_exceeded" in result["error"]


def test_bulk_update_status_rejects_missing_items(tmp_path: Path) -> None:
    """bulk_update_status должен вернуть ошибку если хотя бы один item не существует."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    result = service.bulk_update_status(item_ids=["nonexistent-id-123"], status="done")
    assert result["ok"] is False
    assert result["error"] == "items_not_found"


def test_bulk_update_status_empty_list_returns_ok(tmp_path: Path) -> None:
    """bulk_update_status с пустым списком должен вернуть ok без ошибок."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    result = service.bulk_update_status(item_ids=[], status="done")
    assert result["ok"] is True
    assert result["success_count"] == 0


def test_filter_by_age_returns_only_older_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_by_age должен возвращать только items старше cutoff даты."""
    import src.core.inbox_service as inbox_module

    service = InboxService(state_path=tmp_path / "inbox.json")

    monkeypatch.setattr(inbox_module, "_now_utc_iso", lambda: "2026-01-15T00:00:00+00:00")
    service.upsert_item(dedupe_key="old-item", kind="watch_alert", source="test", title="Old", body="old", severity="info")

    monkeypatch.setattr(inbox_module, "_now_utc_iso", lambda: "2026-03-20T00:00:00+00:00")
    service.upsert_item(dedupe_key="new-item", kind="watch_alert", source="test", title="New", body="new", severity="info")

    result = service.filter_by_age(older_than_date="2026-02-01T00:00:00+00:00")

    assert len(result) == 1
    assert result[0]["dedupe_key"] == "old-item"


def test_filter_by_age_invalid_date_returns_empty(tmp_path: Path) -> None:
    """filter_by_age с невалидной датой должен возвращать пустой список."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    service.upsert_item(dedupe_key="item-x", kind="watch_alert", source="test", title="X", body="x", severity="info")
    result = service.filter_by_age(older_than_date="not-a-valid-date")
    assert result == []


def test_archive_by_kind_cancels_matching_items(tmp_path: Path) -> None:
    """archive_by_kind должен отменить все items нужного kind, не трогая остальные."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    service.upsert_item(dedupe_key="req-1", kind="owner_request", source="test", title="R1", body="b", severity="info")
    service.upsert_item(dedupe_key="req-2", kind="owner_request", source="test", title="R2", body="b", severity="info")
    service.upsert_item(dedupe_key="alert-1", kind="watch_alert", source="test", title="A1", body="b", severity="info")

    result = service.archive_by_kind(kind="owner_request", actor="system-cleanup", note="migration test")

    assert result["ok"] is True
    assert result["archived_count"] == 2
    assert len(result["item_ids"]) == 2
    # watch_alert должен оставаться открытым
    open_alerts = service.list_items(status="open", kind="watch_alert", limit=5)
    assert len(open_alerts) == 1


def test_archive_by_kind_empty_kind_returns_error(tmp_path: Path) -> None:
    """archive_by_kind с пустым kind должен возвращать ошибку."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    result = service.archive_by_kind(kind="")
    assert result["ok"] is False


def test_list_items_open_filter_excludes_all_closed_statuses(tmp_path: Path) -> None:
    """Фильтр status='open' должен исключать done, cancelled, approved, rejected."""
    service = InboxService(state_path=tmp_path / "inbox.json")
    open_id = service.upsert_item(
        dedupe_key="open-item", kind="watch_alert", source="test", title="Open", body="b", severity="info"
    )["item"]["item_id"]
    close_id = service.upsert_item(
        dedupe_key="done-item", kind="watch_alert", source="test", title="Done", body="b", severity="info"
    )["item"]["item_id"]
    service.set_item_status(close_id, status="done", actor="test")

    open_items = service.list_items(status="open", limit=10)

    assert len(open_items) == 1
    assert open_items[0]["item_id"] == open_id
