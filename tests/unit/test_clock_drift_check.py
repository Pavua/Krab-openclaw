"""Тесты clock_drift_check."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.core.clock_drift_check import (
    ClockDriftResult,
    _parse_offset,
    check_clock_drift,
    check_clock_drift_sync,
)


def _fake_completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["sntp"], returncode=returncode, stdout=stdout, stderr=""
    )


def test_parse_offset_extracts_signed_float():
    out = "+0.045678 +/- 0.010 time.apple.com 17.253.0.101"
    assert _parse_offset(out) == pytest.approx(0.045678)


def test_parse_offset_negative():
    out = "-1.234567 +/- 0.020 time.apple.com 17.253.0.101"
    assert _parse_offset(out) == pytest.approx(-1.234567)


def test_parse_offset_returns_none_on_garbage():
    assert _parse_offset("no valid floats here\n") is None


def test_status_ok_small_offset():
    with patch("src.core.clock_drift_check.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed("+0.045678 +/- 0.010 time.apple.com 1.2.3.4")
        result = check_clock_drift_sync()
    assert result.status == "ok"
    assert result.ntp_offset_sec == pytest.approx(0.045678)
    assert "time.apple.com" in result.message


def test_status_drift_warning_above_5s():
    with patch("src.core.clock_drift_check.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed("+7.500000 +/- 0.010 time.apple.com 1.2.3.4")
        result = check_clock_drift_sync()
    assert result.status == "drift_warning"
    assert result.ntp_offset_sec == pytest.approx(7.5)


def test_status_drift_critical_above_30s():
    with patch("src.core.clock_drift_check.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed("-45.100000 +/- 0.010 time.apple.com 1.2.3.4")
        result = check_clock_drift_sync()
    assert result.status == "drift_critical"
    assert result.ntp_offset_sec == pytest.approx(-45.1)


def test_status_unavailable_on_nonzero_rc():
    with patch("src.core.clock_drift_check.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed("", returncode=1)
        result = check_clock_drift_sync()
    assert result.status == "unavailable"
    assert "rc=1" in result.message


def test_status_unavailable_on_timeout():
    with patch("src.core.clock_drift_check.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["sntp"], timeout=8)
        result = check_clock_drift_sync()
    assert result.status == "unavailable"
    assert "sntp failed" in result.message


def test_status_unavailable_on_oserror():
    with patch("src.core.clock_drift_check.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("sntp not found")
        result = check_clock_drift_sync()
    assert result.status == "unavailable"


def test_status_unavailable_on_parse_failure():
    with patch("src.core.clock_drift_check.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed("completely unparseable garbage\n")
        result = check_clock_drift_sync()
    assert result.status == "unavailable"
    assert result.message == "parse failed"


@pytest.mark.asyncio
async def test_async_wrapper_delegates_to_sync():
    fake = ClockDriftResult(local_ts=1.0, ntp_offset_sec=0.01, status="ok", message="stub")
    with patch(
        "src.core.clock_drift_check.check_clock_drift_sync", return_value=fake
    ) as mock_sync:
        result = await check_clock_drift("pool.ntp.org")
    mock_sync.assert_called_once_with("pool.ntp.org")
    assert result is fake
