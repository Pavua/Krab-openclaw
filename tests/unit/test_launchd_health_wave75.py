# -*- coding: utf-8 -*-
"""Wave 75: тесты launchd health monitor.

Покрытие:
  - parse_launchctl_output: корректный парсинг табличного формата
  - filter_krab_agents: только ai.krab.* / ai.openclaw.* / com.krab.*
  - missing PID ("-") → running=0
  - exit_status > 0 экспонируется в prometheus output (alert path)
  - refresh_snapshot_sync обновляет module-level state
  - collect_metrics() выводит krab_launchd_* строки
"""

from __future__ import annotations

import pytest

from src.core import launchd_health_monitor as mod
from src.core.launchd_health_monitor import (
    LaunchdHealthMonitor,
    build_snapshot,
    filter_krab_agents,
    parse_launchctl_output,
    refresh_snapshot_sync,
)

_SAMPLE_OUTPUT = """PID\tStatus\tLabel
-\t0\tcom.apple.SafariHistoryServiceAgent
12345\t0\tai.krab.core
-\t0\tai.krab.daily-maintenance
-\t1\tai.krab.nightly-audit
33750\t0\tai.openclaw.gateway
33760\t0\tcom.krab.mcp-yung-nagato
99999\t0\tcom.unrelated.service
"""


def test_parse_launchctl_output_extracts_rows():
    rows = parse_launchctl_output(_SAMPLE_OUTPUT)
    # header пропущен, 7 валидных строк
    assert len(rows) == 7
    # Конкретная проверка ai.krab.core: pid="12345", status=0
    krab_core = next(r for r in rows if r[2] == "ai.krab.core")
    assert krab_core == ("12345", 0, "ai.krab.core")
    # Missing PID → None
    daily = next(r for r in rows if r[2] == "ai.krab.daily-maintenance")
    assert daily[0] is None


def test_parse_launchctl_output_skips_malformed():
    # Невалидный status, пустые строки, обрезанные строки
    bad = "PID\tStatus\tLabel\n-\tnotanint\tai.krab.bad\n\nincomplete\n-\t0\t\n"
    rows = parse_launchctl_output(bad)
    # "ai.krab.bad" дропается (status невалидный), пустые/обрезанные тоже
    assert rows == []


def test_filter_krab_agents_keeps_only_tracked_prefixes():
    rows = parse_launchctl_output(_SAMPLE_OUTPUT)
    filtered = filter_krab_agents(rows)
    labels = {r[2] for r in filtered}
    assert "ai.krab.core" in labels
    assert "ai.krab.daily-maintenance" in labels
    assert "ai.krab.nightly-audit" in labels
    assert "ai.openclaw.gateway" in labels
    assert "com.krab.mcp-yung-nagato" in labels
    # apple/unrelated дропнуты
    assert "com.apple.SafariHistoryServiceAgent" not in labels
    assert "com.unrelated.service" not in labels


def test_build_snapshot_marks_running_via_pid():
    rows = filter_krab_agents(parse_launchctl_output(_SAMPLE_OUTPUT))
    snap = build_snapshot(rows, now=1000.0)
    assert snap["ai.krab.core"]["pid"] == "12345"
    assert snap["ai.krab.core"]["exit_status"] == 0
    # No PID
    assert snap["ai.krab.daily-maintenance"]["pid"] is None
    # Failed
    assert snap["ai.krab.nightly-audit"]["exit_status"] == 1
    assert snap["ai.krab.nightly-audit"]["pid"] is None


def test_refresh_snapshot_sync_updates_module_state():
    # Сбросить state и убедиться что runner injection работает
    mod._SNAPSHOT.clear()
    mod._LAST_SNAPSHOT_TS[0] = 0.0

    snap = refresh_snapshot_sync(runner=lambda: _SAMPLE_OUTPUT, now_fn=lambda: 5555.0)

    assert "ai.krab.core" in snap
    assert "ai.krab.nightly-audit" in snap
    assert mod.get_last_snapshot_ts() == 5555.0
    # get_snapshot возвращает копию
    snap2 = mod.get_snapshot()
    assert snap2["ai.krab.core"]["exit_status"] == 0
    snap2["ai.krab.core"]["exit_status"] = 999
    assert mod.get_snapshot()["ai.krab.core"]["exit_status"] == 0


def test_collect_metrics_emits_launchd_lines():
    # Заполним snapshot и убедимся что collect_metrics() выводит метрики
    mod._SNAPSHOT.clear()
    refresh_snapshot_sync(runner=lambda: _SAMPLE_OUTPUT, now_fn=lambda: 1234.0)

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()

    assert "# TYPE krab_launchd_last_exit_status gauge" in text
    assert "# TYPE krab_launchd_running gauge" in text
    # Failed service → exit_status=1 (alert path)
    assert 'krab_launchd_last_exit_status{label="ai.krab.nightly-audit"} 1' in text
    # Running service (PID present) → running=1
    assert 'krab_launchd_running{label="ai.krab.core"} 1' in text
    # Не-запущенный → running=0
    assert 'krab_launchd_running{label="ai.krab.daily-maintenance"} 0' in text


def test_collect_metrics_placeholder_when_snapshot_empty():
    # Cold-boot: snapshot пустой — должны быть placeholder'ы (alert не "no data")
    mod._SNAPSHOT.clear()

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert 'krab_launchd_last_exit_status{label="none"} 0' in text
    assert 'krab_launchd_running{label="none"} 0' in text


@pytest.mark.asyncio
async def test_monitor_start_runs_initial_snapshot():
    # Start должен выполнить snapshot синхронно до первого sleep.
    mod._SNAPSHOT.clear()
    monitor = LaunchdHealthMonitor(
        interval_sec=3600,  # достаточно большой чтобы loop не дёргался
        runner=lambda: _SAMPLE_OUTPUT,
        now_fn=lambda: 42.0,
    )
    try:
        monitor.start()
        assert "ai.krab.core" in mod.get_snapshot()
        assert mod.get_last_snapshot_ts() == 42.0
    finally:
        monitor.stop()
