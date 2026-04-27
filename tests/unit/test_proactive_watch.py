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
from datetime import datetime, timezone
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
async def test_capture_manual_baseline_persists_state_and_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    # _fetch_openclaw_cron_jobs → subprocess `openclaw cron list` (до 20s) — мокаем
    monkeypatch.setattr(
        proactive_watch_module,
        "_fetch_openclaw_cron_jobs",
        AsyncMock(return_value=[]),
    )

    result = await service.capture(manual=True, persist_memory=True, notify=False)

    assert result["baseline_created"] is True
    assert result["wrote_memory"] is True
    assert "manual_snapshot" in result["digest"]
    assert calls
    assert "watch=manual_snapshot" in calls[0]
    assert service.get_status()["last_snapshot"]["primary_model"] == "openai-codex/gpt-5.4"


@pytest.mark.asyncio
async def test_capture_route_change_triggers_notifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    # _fetch_openclaw_cron_jobs → subprocess `openclaw cron list` (до 20s) — мокаем
    monkeypatch.setattr(
        proactive_watch_module,
        "_fetch_openclaw_cron_jobs",
        AsyncMock(return_value=[]),
    )
    notifier = AsyncMock()

    first = await service.capture(manual=False, persist_memory=True, notify=True, notifier=notifier)
    second = await service.capture(
        manual=False, persist_memory=True, notify=True, notifier=notifier
    )

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


def test_default_state_path_uses_openclaw_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default state-path должен жить в per-account `~/.openclaw`, а не в shared repo."""
    monkeypatch.setattr(proactive_watch_module.Path, "home", classmethod(lambda cls: tmp_path))

    service = ProactiveWatchService(alert_cooldown_sec=120)

    assert (
        service.state_path
        == tmp_path / ".openclaw" / "krab_runtime_state" / "proactive_watch_state.json"
    )


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
async def test_capture_gateway_transition_syncs_inbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`gateway_down -> gateway_recovered` должен закрывать и watch_alert, и proactive trace."""
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
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)
    # ProactiveWatchService.capture → _check_and_trace_cron_executions → _fetch_openclaw_cron_jobs
    # запускает реальный `openclaw cron list --json --all` subprocess. Без mock'а реальные cron items
    # из ~/.openclaw/krab_runtime_state/ утекают в test inbox и ломают assertions на пустоту.
    monkeypatch.setattr(
        proactive_watch_module, "_fetch_openclaw_cron_jobs", AsyncMock(return_value=[])
    )

    await service.capture(manual=False, persist_memory=True, notify=False)
    down = await service.capture(manual=False, persist_memory=True, notify=False)
    up = await service.capture(manual=False, persist_memory=True, notify=False)

    assert down["reason"] == "gateway_down"
    # watch_alert items are resolved; only proactive_action traces remain open.
    open_non_proactive = [
        item
        for item in inbox.list_items(limit=20)
        if item["kind"] != "proactive_action" and item["status"] in {"open", "acked"}
    ]
    assert open_non_proactive == [], (
        f"Expected no open watch_alert items; found: {open_non_proactive}"
    )
    done_items = inbox.list_items(status="done", kind="watch_alert", limit=5)
    assert up["reason"] == "gateway_recovered"
    assert done_items
    assert done_items[0]["dedupe_key"] == "watch:gateway_down"
    proactive_done = inbox.list_items(status="done", kind="proactive_action", limit=5)
    assert proactive_done
    assert proactive_done[0]["dedupe_key"] == "proactive:watch_trigger:gateway_down"
    assert proactive_done[0]["metadata"]["recovered_reason"] == "gateway_recovered"


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
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)
    # ProactiveWatchService.capture → _check_and_trace_cron_executions → _fetch_openclaw_cron_jobs
    # запускает реальный `openclaw cron list --json --all` subprocess. Без mock'а реальные cron items
    # из ~/.openclaw/krab_runtime_state/ утекают в test inbox и ломают assertions на пустоту.
    monkeypatch.setattr(
        proactive_watch_module, "_fetch_openclaw_cron_jobs", AsyncMock(return_value=[])
    )

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
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)
    # ProactiveWatchService.capture → _check_and_trace_cron_executions → _fetch_openclaw_cron_jobs
    # запускает реальный `openclaw cron list --json --all` subprocess. Без mock'а реальные cron items
    # из ~/.openclaw/krab_runtime_state/ утекают в test inbox и ломают assertions на пустоту.
    monkeypatch.setattr(
        proactive_watch_module, "_fetch_openclaw_cron_jobs", AsyncMock(return_value=[])
    )

    await service.capture(manual=False, persist_memory=False, notify=False)
    result = await service.capture(manual=False, persist_memory=False, notify=False)

    assert result["reason"] == "route_model_changed"
    proactive_items = inbox.list_items(kind="proactive_action", limit=10)
    assert proactive_items == [], (
        f"Expected no inbox items for route_model_changed; got: {proactive_items}"
    )


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
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)
    # ProactiveWatchService.capture → _check_and_trace_cron_executions → _fetch_openclaw_cron_jobs
    # запускает реальный `openclaw cron list --json --all` subprocess. Без mock'а реальные cron items
    # из ~/.openclaw/krab_runtime_state/ утекают в test inbox и ломают assertions на пустоту.
    monkeypatch.setattr(
        proactive_watch_module, "_fetch_openclaw_cron_jobs", AsyncMock(return_value=[])
    )

    await service.capture(manual=False, persist_memory=False, notify=False)
    result = await service.capture(manual=False, persist_memory=False, notify=False)

    assert result["reason"] == "gateway_down"
    proactive_items = inbox.list_items(kind="proactive_action", limit=5)
    assert len(proactive_items) == 1
    assert proactive_items[0]["metadata"]["reason"] == "gateway_down"


@pytest.mark.asyncio
async def test_recovery_reason_closes_existing_proactive_action_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`gateway_recovered` не должен плодить новый open trace, а обязан закрыть `gateway_down`."""
    service = ProactiveWatchService(state_path=tmp_path / "watch.json", alert_cooldown_sec=0)
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    snapshots = [
        _snapshot(),
        _snapshot(ts_utc="2026-03-12T05:01:00+00:00", gateway_ok=False),
        _snapshot(ts_utc="2026-03-12T05:02:00+00:00", gateway_ok=True),
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)
    # ProactiveWatchService.capture → _check_and_trace_cron_executions → _fetch_openclaw_cron_jobs
    # запускает реальный `openclaw cron list --json --all` subprocess. Без mock'а реальные cron items
    # из ~/.openclaw/krab_runtime_state/ утекают в test inbox и ломают assertions на пустоту.
    monkeypatch.setattr(
        proactive_watch_module, "_fetch_openclaw_cron_jobs", AsyncMock(return_value=[])
    )

    await service.capture(manual=False, persist_memory=False, notify=False)
    await service.capture(manual=False, persist_memory=False, notify=False)
    await service.capture(manual=False, persist_memory=False, notify=False)

    open_proactive = inbox.list_items(status="open", kind="proactive_action", limit=10)
    done_proactive = inbox.list_items(status="done", kind="proactive_action", limit=10)

    assert open_proactive == []
    assert done_proactive
    assert done_proactive[0]["dedupe_key"] == "proactive:watch_trigger:gateway_down"
    assert done_proactive[0]["metadata"]["recovered_reason"] == "gateway_recovered"


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
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)
    # ProactiveWatchService.capture → _check_and_trace_cron_executions → _fetch_openclaw_cron_jobs
    # запускает реальный `openclaw cron list --json --all` subprocess. Без mock'а реальные cron items
    # из ~/.openclaw/krab_runtime_state/ утекают в test inbox и ломают assertions на пустоту.
    monkeypatch.setattr(
        proactive_watch_module, "_fetch_openclaw_cron_jobs", AsyncMock(return_value=[])
    )

    await service.capture(manual=False, persist_memory=False, notify=True, notifier=_notifier)
    await service.capture(
        manual=False, persist_memory=False, notify=True, notifier=_notifier
    )  # first gateway_down
    await service.capture(
        manual=False, persist_memory=False, notify=True, notifier=_notifier
    )  # second gateway_down

    # Notifier должен быть вызван только один раз (первый gateway_down в cooldown)
    assert len(notify_calls) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Error Digest
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_error_digest_empty_inbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Пустой inbox → digest пишется с total=0, нет ошибок."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    result = await service.run_error_digest()

    assert result["ok"] is True
    assert result["total"] == 0
    # Убеждаемся, что digest-item был добавлен в inbox
    items = inbox.list_items(kind="proactive_action", limit=10)
    assert len(items) == 1
    assert items[0]["title"].startswith("Error Digest (6h)")
    assert items[0]["metadata"]["action_type"] == "error_digest"


@pytest.mark.asyncio
async def test_run_error_digest_counts_open_warnings_and_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Открытые warning/error items попадают в сводку; info — нет."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    # Добавляем тестовые items
    for i, sev in enumerate(["error", "warning", "warning", "info"]):
        inbox.upsert_item(
            dedupe_key=f"test:item:{i}",
            kind="proactive_action",
            source="test",
            title=f"Item {i}",
            body="body",
            severity=sev,
            status="open",
            identity=inbox.build_identity(
                channel_id="test", team_id="test", trace_id=f"t{i}", approval_scope="owner"
            ),
            metadata={},
        )

    result = await service.run_error_digest()

    assert result["ok"] is True
    assert result["total"] == 3  # error + warning + warning (info excluded)
    assert result["counts"]["error"] == 1
    assert result["counts"]["warning"] == 2

    # Проверяем тело digest-item
    digest_items = [
        it
        for it in inbox.list_items(kind="proactive_action", limit=20)
        if it["metadata"].get("action_type") == "error_digest"
    ]
    assert len(digest_items) == 1
    assert "3" in digest_items[0]["body"]


@pytest.mark.asyncio
async def test_run_error_digest_does_not_include_closed_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Закрытые (done) items не должны попадать в ErrorDigest."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    inbox.upsert_item(
        dedupe_key="test:done:error",
        kind="proactive_action",
        source="test",
        title="Old error",
        body="old",
        severity="error",
        status="open",
        identity=inbox.build_identity(
            channel_id="test", team_id="test", trace_id="td", approval_scope="owner"
        ),
        metadata={},
    )
    # Закрываем item
    inbox.set_status_by_dedupe(
        "test:done:error",
        status="done",
        actor="test",
        note="resolved",
        event_action="resolved",
        metadata_updates={},
    )

    result = await service.run_error_digest()

    assert result["ok"] is True
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_run_alert_checks_returns_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_alert_checks должен вернуть словарь с ключами inbox_critical и swarm_job_stalled."""
    import src.core.proactive_watch as pw_mod
    import src.core.swarm_scheduler as ss_mod
    from src.core.swarm_scheduler import SwarmScheduler

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    scheduler = SwarmScheduler(state_path=tmp_path / "jobs.json")

    monkeypatch.setattr(pw_mod, "inbox_service", inbox)
    monkeypatch.setattr(ss_mod, "swarm_scheduler", scheduler)

    result = await service.run_alert_checks()

    assert "inbox_critical" in result
    assert "swarm_job_stalled" in result


@pytest.mark.asyncio
async def test_inbox_critical_alert_triggers_above_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """При >5 open error items должен создаваться inbox alert inbox_critical."""
    import src.core.proactive_watch as pw_mod

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(pw_mod, "inbox_service", inbox)

    # Создаём 6 error items
    for i in range(6):
        inbox.upsert_item(
            dedupe_key=f"test:err:{i}",
            kind="proactive_action",
            source="test",
            title=f"Error {i}",
            body="body",
            severity="error",
            status="open",
            identity=inbox.build_identity(
                channel_id="sys", team_id="owner", trace_id=f"e{i}", approval_scope="owner"
            ),
            metadata={},
        )

    triggered = await service._check_inbox_critical()

    assert triggered is True
    alert_items = [
        it
        for it in inbox.list_items(kind="proactive_action", limit=20)
        if (it.get("metadata") or {}).get("action_type") == "inbox_critical_alert"
    ]
    assert len(alert_items) == 1
    assert alert_items[0]["metadata"]["error_count"] == 6
    assert alert_items[0]["severity"] == "error"


@pytest.mark.asyncio
async def test_inbox_critical_alert_not_triggered_at_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """При ровно 5 или менее open error items алерт НЕ должен срабатывать."""
    import src.core.proactive_watch as pw_mod

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(pw_mod, "inbox_service", inbox)

    # Создаём ровно 5 error items
    for i in range(5):
        inbox.upsert_item(
            dedupe_key=f"test:err5:{i}",
            kind="proactive_action",
            source="test",
            title=f"Error {i}",
            body="body",
            severity="error",
            status="open",
            identity=inbox.build_identity(
                channel_id="sys", team_id="owner", trace_id=f"e5:{i}", approval_scope="owner"
            ),
            metadata={},
        )

    triggered = await service._check_inbox_critical()

    assert triggered is False
    alert_items = [
        it
        for it in inbox.list_items(kind="proactive_action", limit=20)
        if (it.get("metadata") or {}).get("action_type") == "inbox_critical_alert"
    ]
    assert alert_items == []


@pytest.mark.asyncio
async def test_inbox_critical_dedupe_creates_single_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Два срабатывания inbox_critical → должен быть один dedupe item."""
    import src.core.proactive_watch as pw_mod

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(pw_mod, "inbox_service", inbox)

    for i in range(7):
        inbox.upsert_item(
            dedupe_key=f"test:err_d:{i}",
            kind="proactive_action",
            source="test",
            title=f"Error {i}",
            body="body",
            severity="error",
            status="open",
            identity=inbox.build_identity(
                channel_id="sys", team_id="owner", trace_id=f"ed{i}", approval_scope="owner"
            ),
            metadata={},
        )

    await service._check_inbox_critical()
    await service._check_inbox_critical()

    alert_items = [
        it
        for it in inbox.list_items(kind="proactive_action", limit=30)
        if (it.get("metadata") or {}).get("action_type") == "inbox_critical_alert"
    ]
    assert len(alert_items) == 1
    assert alert_items[0]["dedupe_key"] == "proactive:alert:inbox_critical"


@pytest.mark.asyncio
async def test_swarm_job_stalled_alert_triggers_for_overdue_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Зависшая swarm job должна создавать inbox alert swarm_job_stalled."""
    import src.core.proactive_watch as pw_mod
    from src.core.swarm_scheduler import RecurringJob, SwarmScheduler

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    scheduler = SwarmScheduler(state_path=tmp_path / "jobs.json")
    monkeypatch.setattr(pw_mod, "inbox_service", inbox)

    # Патчим singleton в модуле swarm_scheduler (ленивый импорт в _check_swarm_job_stalled)
    import src.core.swarm_scheduler as ss_mod

    monkeypatch.setattr(ss_mod, "swarm_scheduler", scheduler)

    # Добавляем job, которая последний раз запускалась 3 интервала назад
    from datetime import timedelta

    interval_sec = 3600  # 1 час
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=interval_sec * 3)).isoformat(
        timespec="seconds"
    )
    job = RecurringJob(
        job_id="test-job-stalled",
        team="traders",
        topic="BTC анализ",
        interval_sec=interval_sec,
        last_run_at=stale_ts,
        enabled=True,
    )
    scheduler._jobs["test-job-stalled"] = job

    triggered = await service._check_swarm_job_stalled()

    assert triggered is True
    alert_items = [
        it
        for it in inbox.list_items(kind="proactive_action", limit=20)
        if (it.get("metadata") or {}).get("action_type") == "swarm_job_stalled_alert"
    ]
    assert len(alert_items) == 1
    assert alert_items[0]["metadata"]["job_id"] == "test-job-stalled"
    assert alert_items[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_swarm_job_stalled_no_alert_for_fresh_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Свежая swarm job (запускалась недавно) НЕ должна создавать алерт."""
    import src.core.proactive_watch as pw_mod
    from src.core.swarm_scheduler import RecurringJob, SwarmScheduler

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    scheduler = SwarmScheduler(state_path=tmp_path / "jobs.json")
    monkeypatch.setattr(pw_mod, "inbox_service", inbox)

    import src.core.swarm_scheduler as ss_mod

    monkeypatch.setattr(ss_mod, "swarm_scheduler", scheduler)

    # Job запускалась 30 мин назад, интервал 1 час — threshold 2*3600=7200 сек
    from datetime import timedelta

    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    job = RecurringJob(
        job_id="test-job-fresh",
        team="coders",
        topic="Code review",
        interval_sec=3600,
        last_run_at=recent_ts,
        enabled=True,
    )
    scheduler._jobs["test-job-fresh"] = job

    triggered = await service._check_swarm_job_stalled()

    assert triggered is False
    alert_items = [
        it
        for it in inbox.list_items(kind="proactive_action", limit=20)
        if (it.get("metadata") or {}).get("action_type") == "swarm_job_stalled_alert"
    ]
    assert alert_items == []


@pytest.mark.asyncio
async def test_swarm_job_stalled_skips_never_run_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Job без last_run_at (ещё не запускалась) не считается зависшей."""
    import src.core.proactive_watch as pw_mod
    from src.core.swarm_scheduler import RecurringJob, SwarmScheduler

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    scheduler = SwarmScheduler(state_path=tmp_path / "jobs.json")
    monkeypatch.setattr(pw_mod, "inbox_service", inbox)

    import src.core.swarm_scheduler as ss_mod

    monkeypatch.setattr(ss_mod, "swarm_scheduler", scheduler)

    job = RecurringJob(
        job_id="test-job-new",
        team="analysts",
        topic="New task",
        interval_sec=3600,
        last_run_at="",  # ни разу не запускалась
        enabled=True,
    )
    scheduler._jobs["test-job-new"] = job

    triggered = await service._check_swarm_job_stalled()

    assert triggered is False


@pytest.mark.asyncio
async def test_run_error_digest_dedupe_same_hour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Два вызова в одном часу должны обновлять один и тот же digest-item (dedupe)."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr(proactive_watch_module, "inbox_service", inbox)

    await service.run_error_digest()
    await service.run_error_digest()

    digest_items = [
        it
        for it in inbox.list_items(kind="proactive_action", limit=20)
        if it["metadata"].get("action_type") == "error_digest"
    ]
    # Дедупликация по часу: только один item
    assert len(digest_items) == 1
