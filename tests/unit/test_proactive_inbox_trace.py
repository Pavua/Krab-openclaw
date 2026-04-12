# -*- coding: utf-8 -*-
"""
Phase 2.3 — Owner-visible inbox trace for all proactive actions.

Tests that autonomous/proactive actions (reminder delivery, proactive_watch
triggers) write a kind="proactive_action" trace to the owner inbox.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.core.proactive_watch as proactive_watch_module
import src.core.scheduler as scheduler_module
from src.core.inbox_service import InboxService
from src.core.proactive_watch import ProactiveWatchService, ProactiveWatchSnapshot
from src.core.scheduler import KrabScheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(**overrides):
    payload = {
        "ts_utc": "2026-03-24T10:00:00+00:00",
        "gateway_ok": True,
        "primary_model": "openai-codex/gpt-5.4",
        "route_channel": "openclaw_cloud",
        "route_provider": "google-gemini-cli",
        "route_model": "google-gemini-cli/gemini-3.1-pro-preview",
        "route_status": "ok",
        "route_reason": "openclaw_response_ok",
        "scheduler_enabled": True,
        "scheduler_started": True,
        "scheduler_pending": 0,
        "scheduler_next_due_at": "",
        "memory_count": 5,
        "macos_available": True,
        "macos_frontmost_app": "Terminal",
        "macos_frontmost_window": "krab",
        "reminder_lists_count": 1,
        "note_folders_count": 2,
        "calendars_count": 3,
    }
    payload.update(overrides)
    return ProactiveWatchSnapshot(**payload)


# ---------------------------------------------------------------------------
# Test 1: reminder delivery writes kind="proactive_action" to inbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_fire_writes_proactive_action_trace(tmp_path: Path) -> None:
    """
    When a scheduler reminder is delivered via the sender callback,
    _fire_reminder must upsert a kind='proactive_action' inbox item.
    """
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    delivered = asyncio.Event()

    async def _sender(chat_id: str, text: str) -> None:
        delivered.set()

    original_inbox = scheduler_module.inbox_service
    scheduler_module.inbox_service = inbox
    scheduler.start()
    scheduler.bind_sender(_sender)
    try:
        reminder_id = scheduler.add_reminder(
            chat_id="-10099999",
            text="купить молоко",
            due_at=datetime.now().astimezone() + timedelta(seconds=0.05),
        )
        await asyncio.wait_for(delivered.wait(), timeout=2.0)
        # Allow the post-send inbox writes to complete
        await asyncio.sleep(0.05)

        # A proactive_action trace must exist
        proactive_items = inbox.list_items(kind="proactive_action", limit=10)
        assert proactive_items, (
            "Expected at least one proactive_action inbox item after reminder delivery"
        )
        item = proactive_items[0]
        assert item["kind"] == "proactive_action"
        assert item["metadata"]["action_type"] == "reminder_fired"
        assert item["metadata"]["reminder_id"] == reminder_id
    finally:
        scheduler.stop()
        scheduler_module.inbox_service = original_inbox


# ---------------------------------------------------------------------------
# Test 2: proactive_watch trigger writes kind="proactive_action" to inbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_watch_trigger_writes_proactive_action_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    When proactive_watch.capture() detects an actionable state change (gateway_down,
    gateway_recovered, scheduler_backlog_*), it must upsert a kind='proactive_action'
    inbox item in addition to the existing report_watch_transition call.

    Note: memory-only transitions (route_model_changed, frontmost_app_changed, etc.)
    do NOT create inbox items — they only update memory/digest. Use gateway_down to
    verify the proactive_action trace mechanism.
    """
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    service = ProactiveWatchService(state_path=tmp_path / "state.json", alert_cooldown_sec=60)

    snapshots = [
        _snapshot(gateway_ok=True),
        _snapshot(ts_utc="2026-03-24T10:05:00+00:00", gateway_ok=False),  # → gateway_down
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(
        proactive_watch_module,
        "append_workspace_memory_entry",
        lambda text, **kwargs: True,
    )

    original_inbox = proactive_watch_module.inbox_service
    proactive_watch_module.inbox_service = inbox
    try:
        # First capture: baseline, no reason
        first = await service.capture(manual=False, persist_memory=True, notify=False)
        assert first["reason"] == ""

        # Second capture: gateway went down -> actionable reason
        second = await service.capture(manual=False, persist_memory=True, notify=False)
        assert second["reason"] == "gateway_down"

        # Actionable reasons must create proactive_action inbox items
        proactive_items = inbox.list_items(kind="proactive_action", limit=10)
        assert proactive_items, (
            "Expected at least one proactive_action inbox item from watch trigger"
        )
        item = proactive_items[0]
        assert item["kind"] == "proactive_action"
        assert item["metadata"]["action_type"] == "watch_trigger"
        assert item["metadata"]["reason"] == "gateway_down"
    finally:
        proactive_watch_module.inbox_service = original_inbox


# ---------------------------------------------------------------------------
# Test 3: upsert_item called with correct kind via mock (unit-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_fire_calls_upsert_item_with_proactive_action_kind(
    tmp_path: Path,
) -> None:
    """
    Mocks inbox_service.upsert_item and verifies _fire_reminder calls it
    with kind='proactive_action'.
    """
    inbox_mock = MagicMock()
    inbox_mock.upsert_item = MagicMock(return_value={"ok": True, "created": True, "item": {}})
    inbox_mock.build_identity = MagicMock(return_value=MagicMock())
    inbox_mock.resolve_reminder = MagicMock(return_value={"ok": True})

    scheduler = KrabScheduler(storage_path=tmp_path / "reminders.json")
    delivered = asyncio.Event()

    async def _sender(chat_id: str, text: str) -> None:
        delivered.set()

    original_inbox = scheduler_module.inbox_service
    scheduler_module.inbox_service = inbox_mock
    scheduler.start()
    scheduler.bind_sender(_sender)
    try:
        scheduler.add_reminder(
            chat_id="-10011111",
            text="test reminder",
            due_at=datetime.now().astimezone() + timedelta(seconds=0.05),
        )
        await asyncio.wait_for(delivered.wait(), timeout=2.0)
        await asyncio.sleep(0.05)

        # Find all upsert_item calls with kind="proactive_action"
        proactive_calls = [
            call
            for call in inbox_mock.upsert_item.call_args_list
            if call.kwargs.get("kind") == "proactive_action"
        ]
        assert proactive_calls, "upsert_item was not called with kind='proactive_action'"
        call_kwargs = proactive_calls[0].kwargs
        assert call_kwargs["kind"] == "proactive_action"
        assert "reminder_fired" in call_kwargs["dedupe_key"]
    finally:
        scheduler.stop()
        scheduler_module.inbox_service = original_inbox
