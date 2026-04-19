# -*- coding: utf-8 -*-
"""Тесты для get_top_usage и /api/commands/usage/top."""

from __future__ import annotations

import pytest

from src.core import command_registry as cr


@pytest.fixture(autouse=True)
def _reset_usage():
    """Сброс состояния счётчиков между тестами."""
    cr._reset_usage_for_tests()
    yield
    cr._reset_usage_for_tests()


def test_top_usage_sorted_desc_by_count():
    now = 1_000_000.0
    cr._reset_usage_for_tests(
        counts={"ask": 10, "search": 30, "memo": 5, "voice": 20},
        last_ts={"ask": now, "search": now, "memo": now, "voice": now},
        now_fn=lambda: now,
    )
    result = cr.get_top_usage(limit=5, days=7)
    assert [item["command"] for item in result["top"]] == ["search", "voice", "ask", "memo"]
    assert result["top"][0]["count"] == 30
    assert result["unique_commands"] == 4
    assert result["total_invocations"] == 65


def test_top_usage_limit_caps_results():
    now = 2_000_000.0
    counts = {f"cmd{i}": i + 1 for i in range(30)}
    last_ts = {f"cmd{i}": now for i in range(30)}
    cr._reset_usage_for_tests(counts=counts, last_ts=last_ts, now_fn=lambda: now)

    result = cr.get_top_usage(limit=10, days=7)
    assert len(result["top"]) == 10
    # Самый большой count первый
    assert result["top"][0]["command"] == "cmd29"
    assert result["top"][0]["count"] == 30


def test_top_usage_filters_by_window():
    now = 10_000_000.0
    # cmd_old последний раз 10 дней назад, cmd_fresh — вчера
    cr._reset_usage_for_tests(
        counts={"cmd_old": 100, "cmd_fresh": 5},
        last_ts={"cmd_old": now - 10 * 86400, "cmd_fresh": now - 1 * 86400},
        now_fn=lambda: now,
    )
    result = cr.get_top_usage(limit=20, days=7)
    names = [item["command"] for item in result["top"]]
    assert "cmd_old" not in names
    assert "cmd_fresh" in names
    assert result["unique_commands"] == 1


def test_top_usage_legacy_entries_without_ts_excluded_from_window():
    now = 5_000_000.0
    cr._reset_usage_for_tests(
        counts={"legacy": 50, "modern": 3},
        last_ts={"modern": now},
        now_fn=lambda: now,
    )
    result = cr.get_top_usage(limit=20, days=7)
    assert [item["command"] for item in result["top"]] == ["modern"]


def test_top_usage_days_none_disables_window():
    now = 5_000_000.0
    cr._reset_usage_for_tests(
        counts={"legacy": 50, "modern": 3},
        last_ts={"modern": now},
        now_fn=lambda: now,
    )
    result = cr.get_top_usage(limit=20, days=None)
    names = {item["command"] for item in result["top"]}
    assert names == {"legacy", "modern"}
    assert result["window_days"] is None


def test_top_usage_empty_storage():
    cr._reset_usage_for_tests()
    result = cr.get_top_usage(limit=20, days=7)
    assert result["top"] == []
    assert result["total_invocations"] == 0
    assert result["unique_commands"] == 0


def test_top_usage_limit_minimum_one():
    now = 1.0
    cr._reset_usage_for_tests(
        counts={"ask": 1},
        last_ts={"ask": now},
        now_fn=lambda: now,
    )
    # limit=0 должен стать 1
    result = cr.get_top_usage(limit=0, days=7)
    assert result["limit"] == 1
    assert len(result["top"]) == 1


def test_top_usage_stable_order_on_equal_count():
    now = 1.0
    cr._reset_usage_for_tests(
        counts={"b": 5, "a": 5, "c": 5},
        last_ts={"b": now, "a": now, "c": now},
        now_fn=lambda: now,
    )
    result = cr.get_top_usage(limit=5, days=7)
    # При равных count — алфавит
    assert [item["command"] for item in result["top"]] == ["a", "b", "c"]


def test_bump_command_updates_ts():
    clock = [42.0]
    cr._reset_usage_for_tests(now_fn=lambda: clock[0])

    cr.bump_command("ask")
    clock[0] = 100.0
    cr.bump_command("ask")

    result = cr.get_top_usage(limit=5, days=None)
    entry = result["top"][0]
    assert entry["command"] == "ask"
    assert entry["count"] == 2
    assert entry["last_used_ts"] == 100.0


def test_top_usage_response_shape():
    now = 1.0
    cr._reset_usage_for_tests(
        counts={"ask": 1},
        last_ts={"ask": now},
        now_fn=lambda: now,
    )
    result = cr.get_top_usage(limit=5, days=7)
    assert set(result.keys()) == {
        "window_days",
        "limit",
        "top",
        "total_invocations",
        "unique_commands",
    }
    assert set(result["top"][0].keys()) == {"command", "count", "last_used_ts"}
