# -*- coding: utf-8 -*-
"""
Тесты для Wave 195: ``last_run`` resolution в cron_admin_router.

Покрывает цепочку источников и выбор freshest сигнала:
1. ``health_watcher.json[label].last_run_ts`` (primary)
2. ``StandardOutPath`` mtime
3. ``StandardErrorPath`` mtime
4. ``_AGENT_OUTPUT_HINTS`` файлы (per-label hardcoded hints)

Bug context: на проде ``ai.krab.quota-history`` показывал last_run=2026-05-06
в админке, хотя реально каждый час писал в ``quota_history.jsonl`` (последняя
запись 5 минут назад). Причина — код доверял либо health_watcher (без per-label
данных), либо stdout-логу с устаревшим mtime.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from src.modules.web_routers import cron_admin_router as car

# ---------------------------------------------------------------------------
# 1) _resolve_health_watcher_last_run — per-label primary signal
# ---------------------------------------------------------------------------


def test_resolve_health_watcher_last_run_from_last_runs_dict() -> None:
    """health_watcher.json содержит last_runs[label] с numeric ts."""
    ts = time.time() - 300
    state = {"last_runs": {"ai.krab.foo": {"last_run_ts": ts}}}
    out = car._resolve_health_watcher_last_run("ai.krab.foo", state)
    assert out == ts


def test_resolve_health_watcher_last_run_iso_string() -> None:
    """ISO-строки тоже парсятся."""
    state = {
        "last_runs": {
            "ai.krab.foo": {"last_run": "2026-05-13T12:00:00+00:00"},
        }
    }
    out = car._resolve_health_watcher_last_run("ai.krab.foo", state)
    assert out is not None
    assert out > 0


def test_resolve_health_watcher_last_run_missing_returns_none() -> None:
    """Без per-label данных (текущий продовый формат) — None."""
    state = {"panel_down_count": 0, "gateway_down_count": 0}
    assert car._resolve_health_watcher_last_run("ai.krab.foo", state) is None


# ---------------------------------------------------------------------------
# 2) _collect_hint_mtimes — hint-файлы агентов
# ---------------------------------------------------------------------------


def test_collect_hint_mtimes_returns_existing_only(tmp_path: Path) -> None:
    """Несуществующие файлы пропущены, существующие отсортированы по mtime desc."""
    fresh = tmp_path / "fresh.json"
    fresh.write_text("{}")
    stale = tmp_path / "stale.json"
    stale.write_text("{}")
    # делаем stale "старым"
    old_ts = time.time() - 86400
    import os as _os

    _os.utime(stale, (old_ts, old_ts))

    hints = {"ai.krab.demo": [str(fresh), str(stale), str(tmp_path / "missing.json")]}
    with patch.object(car, "_AGENT_OUTPUT_HINTS", hints):
        result = car._collect_hint_mtimes("ai.krab.demo")
    assert len(result) == 2  # missing dropped
    # freshest первый
    assert result[0][1] == str(fresh)
    assert result[1][1] == str(stale)


def test_collect_hint_mtimes_empty_when_label_unknown() -> None:
    """Если label не в HINTS — пустой список."""
    with patch.object(car, "_AGENT_OUTPUT_HINTS", {}):
        assert car._collect_hint_mtimes("ai.krab.nope") == []


# ---------------------------------------------------------------------------
# 3) _pick_freshest_last_run — orchestrator
# ---------------------------------------------------------------------------


def test_pick_freshest_prefers_hint_over_stale_log(tmp_path: Path) -> None:
    """Корневой Wave 195 bug case: stdout-лог устарел на 7 дней,
    hint-файл (quota_history.jsonl) обновлён 5 минут назад → freshest
    должен победить."""
    stale_log = tmp_path / "stdout.log"
    stale_log.write_text("old")
    old_ts = time.time() - 7 * 86400
    import os as _os

    _os.utime(stale_log, (old_ts, old_ts))

    fresh_hint = tmp_path / "quota_history.jsonl"
    fresh_hint.write_text("{}")  # mtime ~now

    plist_data = {"StandardOutPath": str(stale_log)}
    hints = {"ai.krab.quota-history": [str(fresh_hint)]}
    with patch.object(car, "_AGENT_OUTPUT_HINTS", hints):
        ts, src, path = car._pick_freshest_last_run(
            label="ai.krab.quota-history",
            health_state={},
            plist_data=plist_data,
        )
    assert src == "hint_file"
    assert path == str(fresh_hint)
    assert ts is not None
    # Свежий — в пределах последних 60s.
    assert time.time() - ts < 60


def test_pick_freshest_health_watcher_wins_when_freshest(tmp_path: Path) -> None:
    """health_watcher per-label ts свежее всех остальных → побеждает."""
    stale_hint = tmp_path / "state.json"
    stale_hint.write_text("{}")
    old_ts = time.time() - 86400
    import os as _os

    _os.utime(stale_hint, (old_ts, old_ts))

    fresh_hw_ts = time.time() - 30
    state = {"last_runs": {"ai.krab.foo": {"last_run_ts": fresh_hw_ts}}}

    with patch.object(car, "_AGENT_OUTPUT_HINTS", {"ai.krab.foo": [str(stale_hint)]}):
        ts, src, path = car._pick_freshest_last_run(
            label="ai.krab.foo",
            health_state=state,
            plist_data={},
        )
    assert src == "health_watcher"
    assert ts == fresh_hw_ts
    assert path is None  # health_watcher без файлового пути


def test_pick_freshest_stderr_log_when_only_signal(tmp_path: Path) -> None:
    """Только stderr-лог существует — он становится источником."""
    stderr_path = tmp_path / "stderr.log"
    stderr_path.write_text("err")
    plist_data = {"StandardErrorPath": str(stderr_path)}
    with patch.object(car, "_AGENT_OUTPUT_HINTS", {}):
        ts, src, _path = car._pick_freshest_last_run(
            label="ai.krab.bar",
            health_state={},
            plist_data=plist_data,
        )
    assert src == "stderr_log"
    assert ts is not None


def test_pick_freshest_all_missing_returns_none() -> None:
    """Нет ни одного сигнала → (None, None, None)."""
    with patch.object(car, "_AGENT_OUTPUT_HINTS", {}):
        ts, src, path = car._pick_freshest_last_run(
            label="ai.krab.ghost",
            health_state={},
            plist_data={},
        )
    assert ts is None
    assert src is None
    assert path is None


# ---------------------------------------------------------------------------
# 4) Integration через _enumerate_agents — поле last_run_source в payload
# ---------------------------------------------------------------------------


def test_enumerate_agents_exposes_last_run_source(tmp_path: Path) -> None:
    """JSON-ответ содержит last_run_source для debugging."""
    fake_listing = {
        "ok": True,
        "returncode": 0,
        "stdout": "PID\tStatus\tLabel\n-\t0\tai.krab.quota-history\n",
        "stderr": "",
    }
    fresh_hint = tmp_path / "quota_history.jsonl"
    fresh_hint.write_text("{}")

    with (
        patch.object(car, "_run_launchctl", return_value=fake_listing),
        patch.object(car, "_find_plist_path", return_value=None),
        patch.object(car, "_load_health_watcher_state", return_value={}),
        patch.object(car, "_AGENT_OUTPUT_HINTS", {"ai.krab.quota-history": [str(fresh_hint)]}),
    ):
        agents = car._enumerate_agents()

    quota_agent = next(a for a in agents if a["label"] == "ai.krab.quota-history")
    assert quota_agent["last_run_source"] == "hint_file"
    assert quota_agent["last_run_source_path"] == str(fresh_hint)
    assert quota_agent["last_run_iso"] is not None
