# -*- coding: utf-8 -*-
"""
Тесты для Signal Ops Guard.
Много моков для изоляции сетевых и файловых активностей.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from scripts.signal_ops_guard import _count_pattern_hits, _detect_issue


def test_count_pattern_hits() -> None:
    logs = [
        {"message": "some normal info fetch success"},
        {"message": "Signal SSE stream error retry 1"},
        {"message": "Rate Limited wait 60s"},
        {"message": "another fetch failed on get"},
    ]
    counts = _count_pattern_hits(logs)
    assert counts.get(r"Signal SSE stream error", 0) == 1
    assert counts.get(r"Rate Limited", 0) == 1
    assert counts.get(r"fetch failed", 0) == 1
    assert counts.get(r"\b429\b", 0) == 0


def test_detect_issue_probe_failed() -> None:
    logs: list[dict[str, Any]] = []
    issue = _detect_issue("probe_failed", logs)
    assert issue is not None
    assert issue.code == "signal_probe_failed"
    assert issue.severity == "high"


def test_detect_issue_missing() -> None:
    logs: list[dict[str, Any]] = []
    issue = _detect_issue("missing", logs)
    assert issue is not None
    assert issue.code == "signal_missing_or_not_configured"
    assert issue.severity == "critical"


def test_detect_issue_sse_instability() -> None:
    logs = [
        {"message": "Signal SSE stream error 1"},
        {"message": "Signal SSE stream error 2"},
        {"message": "Signal SSE stream error 3"},
    ]
    issue = _detect_issue("works", logs)
    assert issue is not None
    assert issue.code == "signal_sse_instability"
    assert issue.severity == "high"


def test_detect_issue_not_registered() -> None:
    logs = [
        {"message": "not registered user"},
    ]
    issue = _detect_issue("works", logs)
    assert issue is not None
    assert issue.code == "signal_not_registered"
    assert issue.severity == "critical"


def test_detect_issue_rate_limit() -> None:
    logs = [
        {"message": "Rate Limited for fetching profile"},
    ]
    issue = _detect_issue("works", logs)
    assert issue is not None
    assert issue.code == "signal_rate_limited"
    assert issue.severity == "high"


def test_detect_issue_no_problems() -> None:
    logs = [
        {"message": "Signal SSE connected"},
        {"message": "normal logs"},
    ]
    issue = _detect_issue("works", logs)
    assert issue is None
