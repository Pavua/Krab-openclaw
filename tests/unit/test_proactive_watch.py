# -*- coding: utf-8 -*-
"""
Тесты proactive watch и owner-digest слоя.

Покрываем:
1) baseline/manual snapshot сохраняется и пишет digest в общую память;
2) значимое изменение route/gateway поднимает reason и может вызвать notifier;
3) persisted status читается без запуска реального userbot/runtime;
4) default state-path и legacy fallback корректны для multi-account режима.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.core.proactive_watch as proactive_watch_module
from src.core.inbox_service import InboxService
from src.core.proactive_watch import ProactiveWatchService, ProactiveWatchSnapshot


def _snapshot(**overrides):
    payload = {
        "ts_utc": "2026-03-12T05:00:00+00:00",
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
        "memory_count": 12,
        "macos_available": True,
        "macos_frontmost_app": "Google Chrome",
        "macos_frontmost_window": "Krab Web Panel V2",
        "reminder_lists_count": 1,
        "note_folders_count": 3,
        "calendars_count": 4,
    }
    payload.update(overrides)
    return ProactiveWatchSnapshot(**payload)


@pytest.mark.asyncio
async def test_capture_manual_baseline_persists_state_and_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ручной snapshot должен писать state и one-line digest в shared memory."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json", alert_cooldown_sec=60)
    monkeypatch.setattr(service, "collect_snapshot", AsyncMock(return_value=_snapshot()))
    # append_workspace_memory_entry синхронный; используем обычный lambda для фикса аргументов.
    calls: list[str] = []
    monkeypatch.setattr(
        proactive_watch_module,
        "append_workspace_memory_entry",
        lambda text, **kwargs: calls.append(text) or True,
    )

    result = await service.capture(manual=True, persist_memory=True, notify=False)

    assert result["baseline_created"] is True
    assert result["wrote_memory"] is True
    assert "manual_snapshot" in result["digest"]
    assert calls
    assert "watch=manual_snapshot" in calls[0]
    assert service.get_status()["last_snapshot"]["primary_model"] == "openai-codex/gpt-5.4"


@pytest.mark.asyncio
async def test_capture_route_change_triggers_notifier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Изменение route model после baseline должно поднимать reason и notifier."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json", alert_cooldown_sec=60)
    snapshots = [
        _snapshot(route_model="google-gemini-cli/gemini-3.1-pro-preview"),
        _snapshot(
            ts_utc="2026-03-12T05:05:00+00:00",
            route_provider="openai-codex",
            route_model="openai-codex/gpt-5.4",
        ),
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True)
    notifier = AsyncMock()

    first = await service.capture(manual=False, persist_memory=True, notify=True, notifier=notifier)
    second = await service.capture(manual=False, persist_memory=True, notify=True, notifier=notifier)

    assert first["reason"] == ""
    assert first["alerted"] is False
    assert second["reason"] == "route_model_changed"
    assert second["alerted"] is True
    notifier.assert_awaited_once()


def test_get_status_reads_persisted_fields(tmp_path: Path) -> None:
    """Persisted status должен отдаваться без запуска background-loop."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json", alert_cooldown_sec=120)
    service._save_state(
        {
            "last_snapshot": _snapshot().__dict__,
            "last_reason": "gateway_recovered",
            "last_digest_ts": "2026-03-12T05:00:00+00:00",
            "last_alert_ts": "2026-03-12T05:00:00+00:00",
            "last_alerted_reason": "gateway_recovered",
        }
    )

    status = service.get_status()

    assert status["last_reason"] == "gateway_recovered"
    assert status["last_alerted_reason"] == "gateway_recovered"
    assert status["last_snapshot"]["macos_frontmost_app"] == "Google Chrome"


def test_default_state_path_uses_openclaw_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default state-path должен жить в per-account `~/.openclaw`, а не в shared repo."""
    monkeypatch.setattr(proactive_watch_module.Path, "home", classmethod(lambda cls: tmp_path))

    service = ProactiveWatchService(alert_cooldown_sec=120)

    assert service.state_path == tmp_path / ".openclaw" / "krab_runtime_state" / "proactive_watch_state.json"


def test_get_status_reads_legacy_state_as_fallback(tmp_path: Path) -> None:
    """Во время миграции status должен уметь читать старый repo-level state как fallback."""
    service = ProactiveWatchService(state_path=tmp_path / "new_state.json", alert_cooldown_sec=120)
    service.legacy_state_path = tmp_path / "legacy_state.json"
    service.legacy_state_path.write_text(
        json.dumps(
            {
                "last_snapshot": _snapshot().__dict__,
                "last_reason": "gateway_recovered",
                "last_digest_ts": "2026-03-12T05:00:00+00:00",
                "last_alert_ts": "2026-03-12T05:00:00+00:00",
                "last_alerted_reason": "gateway_recovered",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    status = service.get_status()

    assert status["last_reason"] == "gateway_recovered"
    assert status["last_snapshot"]["primary_model"] == "openai-codex/gpt-5.4"


@pytest.mark.asyncio
async def test_capture_gateway_transition_syncs_inbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`gateway_down -> gateway_recovered` должен открывать и закрывать escalation item в inbox."""
    service = ProactiveWatchService(state_path=tmp_path / "watch_state.json", alert_cooldown_sec=60)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    snapshots = [
        _snapshot(),
        _snapshot(ts_utc="2026-03-12T05:05:00+00:00", gateway_ok=False),
        _snapshot(ts_utc="2026-03-12T05:10:00+00:00", gateway_ok=True),
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True)
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    await service.capture(manual=False, persist_memory=True, notify=False)
    down = await service.capture(manual=False, persist_memory=True, notify=False)
    up = await service.capture(manual=False, persist_memory=True, notify=False)

    assert down["reason"] == "gateway_down"
    # watch_alert items are resolved; only proactive_action traces remain open.
    open_non_proactive = [
        item for item in inbox.list_items(limit=20)
        if item["kind"] != "proactive_action" and item["status"] in {"open", "acked"}
    ]
    assert open_non_proactive == [], f"Expected no open watch_alert items; found: {open_non_proactive}"
    done_items = inbox.list_items(status="done", kind="watch_alert", limit=5)
    assert up["reason"] == "gateway_recovered"
    assert done_items
    assert done_items[0]["dedupe_key"] == "watch:gateway_down"


# ──────────────────────────────────────────────────────────────────────────────
# Task 2.4 — dedupe key, noise reduction, cooldown
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_down_dedupe_key_has_no_timestamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Повторный gateway_down должен обновлять существующий item, а не создавать новый."""
    service = ProactiveWatchService(state_path=tmp_path / "watch.json", alert_cooldown_sec=0)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    snapshots = [
        _snapshot(),
        _snapshot(ts_utc="2026-03-12T05:01:00+00:00", gateway_ok=False),
        _snapshot(ts_utc="2026-03-12T05:02:00+00:00", gateway_ok=False),
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True)
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    await service.capture(manual=False, persist_memory=False, notify=False)
    await service.capture(manual=False, persist_memory=False, notify=False)  # первый gateway_down
    await service.capture(manual=False, persist_memory=False, notify=False)  # второй gateway_down

    proactive_items = inbox.list_items(status="open", kind="proactive_action", limit=10)
    # Дедупликация: два gateway_down → один item с одним dedupe_key
    assert len(proactive_items) == 1
    assert proactive_items[0]["dedupe_key"] == "proactive:watch_trigger:gateway_down"


@pytest.mark.asyncio
async def test_memory_only_reason_does_not_create_inbox_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """route_model_changed (memory-only) не должен создавать inbox item."""
    service = ProactiveWatchService(state_path=tmp_path / "watch.json", alert_cooldown_sec=0)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    snapshots = [
        _snapshot(route_model="openai-codex/gpt-5.4"),
        _snapshot(route_model="google-gemini-cli/gemini-3-flash-preview"),
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True)
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    await service.capture(manual=False, persist_memory=False, notify=False)
    result = await service.capture(manual=False, persist_memory=False, notify=False)

    assert result["reason"] == "route_model_changed"
    proactive_items = inbox.list_items(kind="proactive_action", limit=10)
    assert proactive_items == [], f"Expected no inbox items for route_model_changed; got: {proactive_items}"


@pytest.mark.asyncio
async def test_actionable_reason_creates_inbox_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """gateway_down (actionable) должен создавать proactive_action inbox item."""
    service = ProactiveWatchService(state_path=tmp_path / "watch.json", alert_cooldown_sec=0)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    snapshots = [
        _snapshot(),
        _snapshot(ts_utc="2026-03-12T05:01:00+00:00", gateway_ok=False),
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True)
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    await service.capture(manual=False, persist_memory=False, notify=False)
    result = await service.capture(manual=False, persist_memory=False, notify=False)

    assert result["reason"] == "gateway_down"
    proactive_items = inbox.list_items(kind="proactive_action", limit=5)
    assert len(proactive_items) == 1
    assert proactive_items[0]["metadata"]["reason"] == "gateway_down"


@pytest.mark.asyncio
async def test_cooldown_blocks_repeat_alert_for_same_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Повторный alert для того же reason внутри cooldown не должен вызывать notifier."""
    service = ProactiveWatchService(
        state_path=tmp_path / "watch.json",
        alert_cooldown_sec=3600,  # 1 час
    )
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    snapshots = [
        _snapshot(),
        _snapshot(ts_utc="2026-03-12T05:01:00+00:00", gateway_ok=False),
        _snapshot(ts_utc="2026-03-12T05:02:00+00:00", gateway_ok=False),
    ]

    notify_calls: list[str] = []

    async def _collect():
        return snapshots.pop(0)

    async def _notifier(digest: str) -> None:
        notify_calls.append(digest)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True)
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    await service.capture(manual=False, persist_memory=False, notify=True, notifier=_notifier)
    await service.capture(manual=False, persist_memory=False, notify=True, notifier=_notifier)  # first gateway_down
    await service.capture(manual=False, persist_memory=False, notify=True, notifier=_notifier)  # second gateway_down

    # Notifier должен быть вызван только один раз (первый gateway_down в cooldown)
    assert len(notify_calls) == 1
