# -*- coding: utf-8 -*-
"""
Тесты для truthful automation-layer `translator_finish_gate`.

Зачем нужны:
- чтобы разбор `devicectl` и launch-attempt не ломался при мелких доработках helper'а;
- чтобы `locked` на устройстве не интерпретировался как кодовый регресс.
"""

from __future__ import annotations

from src.core.translator_finish_gate import (
    classify_launch_attempt,
    parse_devicectl_apps_output,
    parse_pytest_summary,
)


def test_parse_devicectl_apps_output_finds_installed_app() -> None:
    stdout = """
Name        Bundle Identifier                                           Version   Bundle Version
----------  ---------------------------------------------------------   -------   --------------
KrabVoice   com.antigravity.krabvoice.user3.macbook.pro.pablito.local   0.2.0     1
""".strip()

    parsed = parse_devicectl_apps_output(
        stdout, "com.antigravity.krabvoice.user3.macbook.pro.pablito.local"
    )

    assert parsed["installed"] is True
    assert parsed["app_name"] == "KrabVoice"
    assert parsed["version"] == "0.2.0"
    assert parsed["bundle_version"] == "1"


def test_parse_devicectl_apps_output_handles_missing_app() -> None:
    stdout = """
Name        Bundle Identifier                                           Version   Bundle Version
----------  ---------------------------------------------------------   -------   --------------
OtherApp    com.example.other                                           1.0       7
""".strip()

    parsed = parse_devicectl_apps_output(
        stdout, "com.antigravity.krabvoice.user3.macbook.pro.pablito.local"
    )

    assert parsed["installed"] is False
    assert parsed["bundle_id"] == "com.antigravity.krabvoice.user3.macbook.pro.pablito.local"


def test_classify_launch_attempt_marks_locked_device_as_non_regression() -> None:
    result = classify_launch_attempt(
        returncode=1,
        stdout="",
        stderr="Unable to launch com.antigravity.krabvoice.user3.macbook.pro.pablito.local because the device was not, or could not be, unlocked. Locked",
    )

    assert result["status"] == "locked"
    assert result["blocked_by_device_lock"] is True


def test_classify_launch_attempt_marks_success() -> None:
    result = classify_launch_attempt(
        returncode=0,
        stdout="Launched application with pid 123",
        stderr="",
    )

    assert result["status"] == "launched"
    assert result["blocked_by_device_lock"] is False


def test_parse_pytest_summary_counts_quiet_dots() -> None:
    summary = parse_pytest_summary(
        ".................                                                        [100%]"
    )

    assert summary["passed_count"] == 17
    assert summary["failed_count"] == 0
