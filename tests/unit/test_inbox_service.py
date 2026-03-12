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
