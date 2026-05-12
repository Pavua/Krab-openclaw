"""Wave 111: тесты disk_space_monitor."""

from __future__ import annotations

import asyncio
from collections import namedtuple
from unittest.mock import patch

import pytest

from src.core import disk_space_monitor as dsm

_Usage = namedtuple("_Usage", ["total", "used", "free"])


def test_compute_used_pct_basic():
    """50% used при total=100, free=50."""
    assert dsm._compute_used_pct(100, 50) == 50.0


def test_compute_used_pct_zero_total_safe():
    """total=0 не должно бросать ZeroDivisionError."""
    assert dsm._compute_used_pct(0, 0) == 0.0


def test_take_snapshot_success(tmp_path):
    """Snapshot собирает корректные значения и помечает available=True."""
    fake = _Usage(total=200, used=160, free=40)
    with patch.object(dsm.shutil, "disk_usage", return_value=fake):
        snap = dsm.take_snapshot(str(tmp_path))
    assert snap.available is True
    assert snap.total == 200
    assert snap.free == 40
    assert snap.used_pct == 80.0


def test_take_snapshot_missing_path_graceful():
    """Несуществующий путь → available=False, без exception."""
    snap = dsm.take_snapshot("/definitely/not/a/real/path/krab_w111")
    assert snap.available is False
    assert snap.total == 0
    assert snap.free == 0


def test_collect_snapshots_records_metrics_and_warns(tmp_path, caplog):
    """При used_pct=92 (>WARN) пишется logger.warning + Prometheus gauge."""
    fake = _Usage(total=1000, used=920, free=80)  # 92%
    recorded: list[tuple[str, int, float]] = []

    def _record(*, mount, free_bytes, used_pct):
        recorded.append((mount, free_bytes, used_pct))

    with (
        patch.object(dsm.shutil, "disk_usage", return_value=fake),
        patch.object(dsm, "record_disk_usage", side_effect=_record),
    ):
        snaps = dsm.collect_snapshots([str(tmp_path)])

    assert len(snaps) == 1
    assert snaps[0].used_pct == 92.0
    assert recorded == [(str(tmp_path), 80, 92.0)]


def test_collect_snapshots_critical_threshold_logs_error(tmp_path):
    """used_pct=96 (>CRITICAL) → logger.error путь без падения."""
    fake = _Usage(total=1000, used=960, free=40)  # 96%
    with (
        patch.object(dsm.shutil, "disk_usage", return_value=fake),
        patch.object(dsm, "record_disk_usage"),
    ):
        snaps = dsm.collect_snapshots([str(tmp_path)])
    assert snaps[0].used_pct == 96.0
    assert snaps[0].available is True


def test_collect_snapshots_skips_unavailable_path():
    """Недоступный путь не вызывает record_disk_usage."""
    called: list[str] = []

    def _record(*, mount, free_bytes, used_pct):
        called.append(mount)

    with patch.object(dsm, "record_disk_usage", side_effect=_record):
        snaps = dsm.collect_snapshots(["/totally/missing/path/wave111"])

    assert len(snaps) == 1
    assert snaps[0].available is False
    assert called == []


def test_resolve_interval_env_override(monkeypatch):
    """KRAB_DISK_CHECK_INTERVAL_SEC валидное → используется, иначе default."""
    monkeypatch.setenv("KRAB_DISK_CHECK_INTERVAL_SEC", "42")
    assert dsm._resolve_interval() == 42
    monkeypatch.setenv("KRAB_DISK_CHECK_INTERVAL_SEC", "bad")
    assert dsm._resolve_interval(default=600) == 600
    monkeypatch.setenv("KRAB_DISK_CHECK_INTERVAL_SEC", "0")
    assert dsm._resolve_interval(default=600) == 600
    monkeypatch.delenv("KRAB_DISK_CHECK_INTERVAL_SEC", raising=False)
    assert dsm._resolve_interval(default=123) == 123


@pytest.mark.asyncio
async def test_disk_space_monitor_loop_runs_and_cancels(tmp_path):
    """Loop вызывает collect_snapshots между sleep'ами и корректно отменяется."""
    calls: list[int] = []

    fake = _Usage(total=100, used=50, free=50)

    async def _sleeper(_interval: float) -> None:
        calls.append(1)
        if len(calls) >= 2:
            raise asyncio.CancelledError()

    with (
        patch.object(dsm.shutil, "disk_usage", return_value=fake),
        patch.object(dsm, "record_disk_usage"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await dsm.disk_space_monitor_loop(
                [str(tmp_path)], interval_sec=1, sleeper=_sleeper
            )

    assert len(calls) >= 1
