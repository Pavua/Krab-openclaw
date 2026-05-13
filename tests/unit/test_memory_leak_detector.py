# -*- coding: utf-8 -*-
"""Wave 205: тесты memory_leak_detector."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import memory_leak_detector as mld
from src.core.memory_leak_detector import (
    DEFAULT_THRESHOLD_MB_PER_HOUR,
    LeakAnalysis,
    MemorySnapshot,
    analyze_trend,
    append_snapshot,
    capture_snapshot,
    get_prometheus_state,
    read_snapshots,
    run_once,
    snapshots_path,
)


@pytest.fixture
def runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолированная runtime-state директория через ENV override."""
    d = tmp_path / "krab_runtime_state"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(d))
    # Reset prom singletons между тестами.
    mld._LAST_RSS_BYTES[0] = 0
    mld._LAST_VMS_BYTES[0] = 0
    mld._LAST_SWAP_BYTES[0] = 0
    mld._LAST_GROWTH_MB_PER_HOUR[0] = 0.0
    mld._LEAK_SUSPECTED_FLAG[0] = 0
    return d


def _make_snap(rss_mb: float, ts: float | None = None) -> MemorySnapshot:
    return MemorySnapshot(
        ts=ts if ts is not None else time.time(),
        iso="2026-05-13T00:00:00+00:00",
        pid=99999,
        rss_bytes=int(rss_mb * 1024 * 1024),
        vms_bytes=int(rss_mb * 2 * 1024 * 1024),
        swap_bytes=0,
        num_threads=10,
        open_files=20,
        tracemalloc_top=None,
    )


def test_capture_snapshot_returns_realistic_values() -> None:
    """capture_snapshot реально читает память текущего процесса."""
    snap = capture_snapshot()
    assert snap is not None
    assert snap.pid > 0
    assert snap.rss_bytes > 0  # any process has > 0 RSS
    assert snap.vms_bytes >= snap.rss_bytes or snap.vms_bytes > 0


def test_append_and_read_roundtrip(runtime_dir: Path) -> None:
    """append → read возвращает identical данные."""
    snap = _make_snap(100.0)
    path = append_snapshot(snap, runtime_state_dir=runtime_dir)
    assert path.exists()
    rows = read_snapshots(runtime_state_dir=runtime_dir)
    assert len(rows) == 1
    assert rows[0]["rss_bytes"] == snap.rss_bytes
    assert rows[0]["pid"] == 99999


def test_append_rotates_at_max(runtime_dir: Path) -> None:
    """Старые snapshots обрезаются до max_snapshots."""
    for i in range(15):
        snap = _make_snap(100.0 + i, ts=time.time() - (15 - i) * 60)
        append_snapshot(snap, runtime_state_dir=runtime_dir, max_snapshots=10)
    rows = read_snapshots(runtime_state_dir=runtime_dir)
    assert len(rows) == 10
    # Должны остаться последние 10 (rss 105..114).
    assert rows[0]["rss_bytes"] == int(105 * 1024 * 1024)
    assert rows[-1]["rss_bytes"] == int(114 * 1024 * 1024)


def test_analyze_trend_empty_returns_not_suspected(runtime_dir: Path) -> None:
    analysis = analyze_trend(snapshots=[], runtime_state_dir=runtime_dir)
    assert isinstance(analysis, LeakAnalysis)
    assert analysis.samples == 0
    assert analysis.suspected is False


def test_analyze_trend_flat_rss_not_suspected(runtime_dir: Path) -> None:
    """Постоянный RSS → no leak."""
    now = time.time()
    rows = [
        {"ts": now - 7200, "rss_bytes": 500 * 1024 * 1024},
        {"ts": now - 5400, "rss_bytes": 500 * 1024 * 1024},
        {"ts": now - 3600, "rss_bytes": 500 * 1024 * 1024},
        {"ts": now - 1800, "rss_bytes": 500 * 1024 * 1024},
        {"ts": now, "rss_bytes": 500 * 1024 * 1024},
    ]
    analysis = analyze_trend(snapshots=rows, window_hours=24)
    assert analysis.suspected is False
    assert abs(analysis.growth_mb_per_hour) < 1.0


def test_analyze_trend_strong_growth_flagged(runtime_dir: Path) -> None:
    """RSS растёт +100 МБ за час → выше default threshold (50) → suspected."""
    now = time.time()
    rows = [
        {"ts": now - 3600, "rss_bytes": 500 * 1024 * 1024},
        {"ts": now - 2700, "rss_bytes": 525 * 1024 * 1024},
        {"ts": now - 1800, "rss_bytes": 550 * 1024 * 1024},
        {"ts": now - 900, "rss_bytes": 575 * 1024 * 1024},
        {"ts": now, "rss_bytes": 600 * 1024 * 1024},
    ]
    analysis = analyze_trend(snapshots=rows, window_hours=24)
    assert analysis.suspected is True
    assert analysis.growth_mb_per_hour > DEFAULT_THRESHOLD_MB_PER_HOUR


def test_analyze_trend_growth_below_threshold_not_flagged(runtime_dir: Path) -> None:
    """Малый рост (~10 МБ/час) ниже threshold → not suspected."""
    now = time.time()
    rows = [
        {"ts": now - 3600 * 4, "rss_bytes": 500 * 1024 * 1024},
        {"ts": now - 3600 * 3, "rss_bytes": 510 * 1024 * 1024},
        {"ts": now - 3600 * 2, "rss_bytes": 520 * 1024 * 1024},
        {"ts": now - 3600, "rss_bytes": 530 * 1024 * 1024},
        {"ts": now, "rss_bytes": 540 * 1024 * 1024},
    ]
    analysis = analyze_trend(snapshots=rows, window_hours=24)
    assert analysis.suspected is False
    assert 5.0 < analysis.growth_mb_per_hour < 50.0


def test_analyze_trend_too_few_samples_not_flagged(runtime_dir: Path) -> None:
    """Меньше 4 samples в окне → suspected=False даже при big growth."""
    now = time.time()
    rows = [
        {"ts": now - 3600, "rss_bytes": 100 * 1024 * 1024},
        {"ts": now, "rss_bytes": 500 * 1024 * 1024},
    ]
    analysis = analyze_trend(snapshots=rows, window_hours=24)
    assert analysis.suspected is False


def test_run_once_updates_prometheus_state(runtime_dir: Path) -> None:
    """run_once обновляет module-level prom gauges."""
    result = run_once(runtime_state_dir=runtime_dir)
    assert result["enabled"] is True
    assert result["captured"] is True
    state = get_prometheus_state()
    assert state["krab_process_rss_bytes"] > 0
    assert state["krab_memory_leak_suspected"] in (0, 1)


def test_run_once_disabled_via_env(runtime_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_MEMORY_LEAK_DETECTOR_ENABLED=0 → run_once skip."""
    monkeypatch.setenv("KRAB_MEMORY_LEAK_DETECTOR_ENABLED", "0")
    result = run_once(runtime_state_dir=runtime_dir)
    assert result == {"enabled": False}


def test_run_once_fires_sentry_when_leak_detected(
    runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При обнаружении leak вызывается capture_message + cooldown пишется."""
    # Заранее засеваем строго растущую историю → leak detection триггернётся.
    now = time.time()
    path = snapshots_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(6):
            row = {
                "ts": now - (6 - i) * 600,  # каждые 10 мин
                "iso": "2026-05-13T00:00:00+00:00",
                "pid": 1,
                "rss_bytes": (300 + i * 100) * 1024 * 1024,  # +100MB/10мин → 600МБ/ч
                "vms_bytes": 0,
                "swap_bytes": 0,
                "num_threads": 1,
                "open_files": 0,
                "tracemalloc_top": None,
            }
            fh.write(json.dumps(row) + "\n")

    captured: list[tuple[str, dict]] = []

    def fake_capture_message(msg: str, level: str = "info", **extras) -> None:  # noqa: ARG001
        captured.append((msg, extras))

    monkeypatch.setattr(
        "src.core.sentry_integration.capture_message",
        fake_capture_message,
    )

    # capture_snapshot мочим что бы вернуть высокий RSS — не зависит от текущего процесса.
    fake_snap = _make_snap(900.0, ts=now)
    with patch.object(mld, "capture_snapshot", return_value=fake_snap):
        result = run_once(runtime_state_dir=runtime_dir)

    assert result["analysis"]["suspected"] is True
    assert result["sentry_fired"] is True
    assert any(msg == "memory_leak_suspected" for msg, _ in captured)

    # Cooldown файл создан → второй запуск НЕ fires.
    captured.clear()
    with patch.object(mld, "capture_snapshot", return_value=fake_snap):
        result2 = run_once(runtime_state_dir=runtime_dir)
    assert result2["sentry_fired"] is False
    assert captured == []


def test_read_snapshots_skips_malformed_lines(runtime_dir: Path) -> None:
    """Битые строки JSONL не ломают чтение."""
    path = snapshots_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": 1, "rss_bytes": 100})
        + "\n"
        + "garbage_line\n"
        + json.dumps({"ts": 2, "rss_bytes": 200})
        + "\n",
        encoding="utf-8",
    )
    rows = read_snapshots(runtime_state_dir=runtime_dir)
    assert len(rows) == 2
    assert rows[0]["ts"] == 1
    assert rows[1]["ts"] == 2
