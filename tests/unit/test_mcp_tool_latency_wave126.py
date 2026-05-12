# -*- coding: utf-8 -*-
"""Wave 126: MCP per-tool latency metrics tests."""

from __future__ import annotations

import time

import pytest

from src.core.metrics import mcp_tools as mt


def _hist_count(server: str, tool: str) -> float:
    """Возвращает сумму observe-ов через _sum (для проверки факта записи)."""
    h = mt.krab_mcp_tool_duration_seconds
    if h is None:  # prometheus_client отсутствует — sentinel
        return -1.0
    # _sum/_count доступны через collect()
    for metric in h.collect():
        for sample in metric.samples:
            if (
                sample.name.endswith("_count")
                and sample.labels.get("server") == server
                and sample.labels.get("tool") == tool
            ):
                return float(sample.value)
    return 0.0


def _counter_value(server: str, tool: str, outcome: str) -> float:
    c = mt.krab_mcp_tool_calls_total
    if c is None:
        return -1.0
    for metric in c.collect():
        for sample in metric.samples:
            if (
                sample.labels.get("server") == server
                and sample.labels.get("tool") == tool
                and sample.labels.get("outcome") == outcome
            ):
                return float(sample.value)
    return 0.0


def test_split_tool_name_server_tool() -> None:
    assert mt._split_tool_name("github__create_issue") == ("github", "create_issue")


def test_split_tool_name_native() -> None:
    assert mt._split_tool_name("peekaboo") == ("native", "peekaboo")
    assert mt._split_tool_name("web_search") == ("native", "web_search")


def test_split_tool_name_voice_prefix() -> None:
    assert mt._split_tool_name("voice:speak") == ("voice", "speak")


def test_split_tool_name_empty_fallback() -> None:
    assert mt._split_tool_name("") == ("unknown", "unknown")
    assert mt._split_tool_name("   ") == ("unknown", "unknown")


def test_record_tool_call_ok_increments_histogram_and_counter() -> None:
    before_h = _hist_count("github", "wave126_ok")
    before_c = _counter_value("github", "wave126_ok", "ok")
    mt.record_tool_call(
        full_tool_name="github__wave126_ok",
        duration_seconds=0.42,
        outcome="ok",
    )
    after_h = _hist_count("github", "wave126_ok")
    after_c = _counter_value("github", "wave126_ok", "ok")
    if before_h < 0:  # prometheus_client unavailable
        pytest.skip("prometheus_client not installed")
    assert after_h == before_h + 1
    assert after_c == before_c + 1


def test_record_tool_call_invalid_outcome_coerced_to_error() -> None:
    before = _counter_value("native", "wave126_bad", "error")
    mt.record_tool_call(
        full_tool_name="wave126_bad",
        duration_seconds=0.1,
        outcome="garbage",
    )
    after = _counter_value("native", "wave126_bad", "error")
    if before < 0:
        pytest.skip("prometheus_client not installed")
    assert after == before + 1


def test_tool_latency_timer_records_ok_on_clean_exit() -> None:
    before = _counter_value("github", "wave126_timer_ok", "ok")
    with mt.ToolLatencyTimer("github__wave126_timer_ok"):
        time.sleep(0.01)
    after = _counter_value("github", "wave126_timer_ok", "ok")
    if before < 0:
        pytest.skip("prometheus_client not installed")
    assert after == before + 1


def test_tool_latency_timer_records_error_on_exception() -> None:
    before = _counter_value("github", "wave126_timer_err", "error")
    with pytest.raises(ValueError):
        with mt.ToolLatencyTimer("github__wave126_timer_err"):
            raise ValueError("boom")
    after = _counter_value("github", "wave126_timer_err", "error")
    if before < 0:
        pytest.skip("prometheus_client not installed")
    assert after == before + 1


def test_tool_latency_timer_records_timeout_on_timeout_error() -> None:
    before = _counter_value("native", "wave126_timer_to", "timeout")
    with pytest.raises(TimeoutError):
        with mt.ToolLatencyTimer("wave126_timer_to"):
            raise TimeoutError("slow")
    after = _counter_value("native", "wave126_timer_to", "timeout")
    if before < 0:
        pytest.skip("prometheus_client not installed")
    assert after == before + 1


def test_tool_latency_timer_explicit_mark_timeout() -> None:
    before = _counter_value("github", "wave126_marked_to", "timeout")
    with mt.ToolLatencyTimer("github__wave126_marked_to") as t:
        t.mark_timeout()
    after = _counter_value("github", "wave126_marked_to", "timeout")
    if before < 0:
        pytest.skip("prometheus_client not installed")
    assert after == before + 1
