# -*- coding: utf-8 -*-
"""
Wave 44-N-watch: тесты proactive_watch dreaming health monitor.

Покрываем:
1) dreaming в error state → reason=dreaming_error;
2) healthy dreaming → нет reason;
3) stale diary (events grow, mtime > 24h) → dreaming_error;
4) cooldown: 3 ошибки в окне cooldown → 1 alert;
5) files-fallback path работает без gateway client.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.core.proactive_watch as proactive_watch_module
from src.core.proactive_watch import ProactiveWatchService, ProactiveWatchSnapshot


def _snapshot(*, dreaming_status=None, ts="2026-05-09T05:00:00+00:00", **overrides):
    payload = {
        "ts_utc": ts,
        "gateway_ok": True,
        "primary_model": "google/gemini-3-pro-preview",
        "route_channel": "openclaw_cloud",
        "route_provider": "google",
        "route_model": "google/gemini-3-pro-preview",
        "route_status": "ok",
        "route_reason": "ok",
        "scheduler_enabled": True,
        "scheduler_started": True,
        "scheduler_pending": 0,
        "scheduler_next_due_at": "",
        "memory_count": 10,
        "macos_available": True,
        "macos_frontmost_app": "Chrome",
        "macos_frontmost_window": "tab",
        "reminder_lists_count": 1,
        "note_folders_count": 1,
        "calendars_count": 1,
        "dreaming_status": dreaming_status,
    }
    payload.update(overrides)
    return ProactiveWatchSnapshot(**payload)


# ---------------------------------------------------------------- detect_reason


def test_detect_dreaming_error_triggers_reason():
    prev = _snapshot(dreaming_status={"enabled": True, "events_count": 100, "error": None})
    cur = _snapshot(
        dreaming_status={"enabled": True, "events_count": 105, "error": "ingestion_failed"}
    )
    assert ProactiveWatchService._detect_reason(prev, cur) == "dreaming_error"


def test_detect_dreaming_healthy_no_reason():
    prev = _snapshot(dreaming_status={"enabled": True, "events_count": 100, "error": None})
    cur = _snapshot(
        dreaming_status={
            "enabled": True,
            "events_count": 110,
            "error": None,
            "last_event_mtime": time.time(),
        }
    )
    assert ProactiveWatchService._detect_reason(prev, cur) == ""


def test_detect_stale_diary_with_growing_events():
    """events растут, mtime старше 24h → dreaming_error (ingestion stuck)."""
    stale_mtime = time.time() - (25 * 3600)
    prev = _snapshot(
        dreaming_status={"enabled": True, "events_count": 100, "last_event_mtime": stale_mtime}
    )
    cur = _snapshot(
        dreaming_status={
            "enabled": True,
            "events_count": 150,  # выросло
            "last_event_mtime": stale_mtime,  # но mtime не двигался
            "error": None,
        }
    )
    assert ProactiveWatchService._detect_reason(prev, cur) == "dreaming_error"


def test_detect_fresh_diary_growing_events_no_alert():
    """events растут, mtime свежий → всё ок."""
    fresh = time.time() - 60
    prev = _snapshot(
        dreaming_status={"enabled": True, "events_count": 100, "last_event_mtime": fresh - 60}
    )
    cur = _snapshot(
        dreaming_status={
            "enabled": True,
            "events_count": 150,
            "last_event_mtime": fresh,
            "error": None,
        }
    )
    assert ProactiveWatchService._detect_reason(prev, cur) == ""


# ---------------------------------------------------------------- cooldown


@pytest.mark.asyncio
async def test_dreaming_error_cooldown_dedupe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """3 dreaming_error события за < cooldown окно → notifier вызван 1 раз."""
    service = ProactiveWatchService(state_path=tmp_path / "state.json", alert_cooldown_sec=1800)
    # Используем real-time timestamps, иначе datetime.now() vs ts_utc=2026 ломает cooldown.
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshots = [
        _snapshot(
            ts=now_iso, dreaming_status={"enabled": True, "events_count": 100, "error": None}
        ),
        _snapshot(
            ts=now_iso,
            dreaming_status={"enabled": True, "events_count": 105, "error": "ingestion_failed"},
        ),
        _snapshot(
            ts=now_iso,
            dreaming_status={"enabled": True, "events_count": 110, "error": "ingestion_failed_2"},
        ),
        _snapshot(
            ts=now_iso,
            dreaming_status={"enabled": True, "events_count": 115, "error": "ingestion_failed_3"},
        ),
    ]

    async def _collect():
        return snapshots.pop(0)

    monkeypatch.setattr(service, "collect_snapshot", _collect)
    monkeypatch.setattr(
        proactive_watch_module, "append_workspace_memory_entry", lambda text, **kwargs: True
    )
    monkeypatch.setattr(
        proactive_watch_module, "_fetch_openclaw_cron_jobs", AsyncMock(return_value=[])
    )

    notifier = AsyncMock()
    # baseline
    await service.capture(manual=False, persist_memory=True, notify=True, notifier=notifier)
    # error 1 — alert ok
    r1 = await service.capture(manual=False, persist_memory=True, notify=True, notifier=notifier)
    # error 2 — внутри cooldown
    r2 = await service.capture(manual=False, persist_memory=True, notify=True, notifier=notifier)
    # error 3 — внутри cooldown
    r3 = await service.capture(manual=False, persist_memory=True, notify=True, notifier=notifier)

    assert r1["reason"] == "dreaming_error"
    assert r1["alerted"] is True
    assert r2["reason"] == "dreaming_error"
    # cooldown активен по same reason
    assert r2["alerted"] is False
    assert r3["alerted"] is False
    assert notifier.await_count == 1


# ---------------------------------------------------------------- files fallback


@pytest.mark.asyncio
async def test_read_dreaming_status_files_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Когда gateway client недоступен — читаем events.jsonl + recall.json напрямую."""
    events_path = tmp_path / "events.jsonl"
    recall_path = tmp_path / "short-term-recall.json"
    events_path.write_text("\n".join(['{"a":1}', '{"a":2}', '{"a":3}']) + "\n", encoding="utf-8")
    recall_path.write_text(json.dumps({"entries": [{"id": "x"}, {"id": "y"}]}), encoding="utf-8")
    monkeypatch.setattr(proactive_watch_module, "_DREAMING_EVENTS_PATH", events_path)
    monkeypatch.setattr(proactive_watch_module, "_DREAMING_RECALL_PATH", recall_path)

    # Гарантируем отсутствие RPC.
    monkeypatch.setattr(
        proactive_watch_module.openclaw_client, "doctor_memory_status", None, raising=False
    )

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    status = await service._read_dreaming_status()

    assert status is not None
    assert status["events_count"] == 3
    assert status["recall_entries"] == 2
    assert status["error"] is None
    assert isinstance(status["last_event_mtime"], float)


@pytest.mark.asyncio
async def test_read_dreaming_status_corrupt_recall_json_marks_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events_path = tmp_path / "events.jsonl"
    recall_path = tmp_path / "short-term-recall.json"
    events_path.write_text('{"a":1}\n', encoding="utf-8")
    recall_path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(proactive_watch_module, "_DREAMING_EVENTS_PATH", events_path)
    monkeypatch.setattr(proactive_watch_module, "_DREAMING_RECALL_PATH", recall_path)
    monkeypatch.setattr(
        proactive_watch_module.openclaw_client, "doctor_memory_status", None, raising=False
    )

    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    status = await service._read_dreaming_status()

    assert status is not None
    assert status["error"] is not None
    assert "recall_corrupt" in status["error"]


@pytest.mark.asyncio
async def test_read_dreaming_status_no_files_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(proactive_watch_module, "_DREAMING_EVENTS_PATH", tmp_path / "missing.jsonl")
    monkeypatch.setattr(proactive_watch_module, "_DREAMING_RECALL_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(
        proactive_watch_module.openclaw_client, "doctor_memory_status", None, raising=False
    )
    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    assert await service._read_dreaming_status() is None


# ---------------------------------------------------------------- render


def test_render_digest_includes_dreaming_alert_line():
    snap = _snapshot(
        dreaming_status={
            "enabled": True,
            "events_count": 200,
            "error": "diary corrupt",
        }
    )
    out = ProactiveWatchService.render_digest(snap, reason="dreaming_error", manual=False)
    assert "Dreaming health alert" in out
    assert "diary corrupt" in out
