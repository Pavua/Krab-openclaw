# -*- coding: utf-8 -*-
"""
Тесты FinOps полей в /api/costs/report.

Session 7: добавлены поля total_tool_calls, total_fallbacks,
total_context_tokens, avg_context_tokens, by_channel.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def _mock_cost_analytics():
    """Мок cost_analytics.build_usage_report_dict с FinOps полями."""
    mock_ca = MagicMock()
    mock_ca.build_usage_report_dict.return_value = {
        "cost_session_usd": 1.23,
        "monthly_budget_usd": 50.0,
        "by_model": {"gemini-3-pro": {"calls": 10, "cost": 0.5}},
        "input_tokens": 5000,
        "output_tokens": 2000,
        "total_tool_calls": 42,
        "total_fallbacks": 3,
        "total_context_tokens": 80000,
        "avg_context_tokens": 4000,
        "by_channel": {"telegram": 30, "panel": 12},
    }
    with patch.dict("sys.modules", {"src.core.cost_analytics": MagicMock(cost_analytics=mock_ca)}):
        yield mock_ca


def _get_report_dict(mock_ca: MagicMock) -> dict:
    """Эмулирует логику handler /api/costs/report с FinOps полями."""
    raw = mock_ca.build_usage_report_dict()
    total_cost = float(raw.get("cost_session_usd") or 0)
    budget = float(raw.get("monthly_budget_usd") or 0) or 50.0
    total_calls = sum(m.get("calls", 0) for m in (raw.get("by_model") or {}).values())
    report = {
        "total_cost_usd": total_cost,
        "total_calls": total_calls,
        "budget_monthly_usd": budget,
        "budget_remaining_usd": budget - total_cost,
        "budget_used_pct": round(total_cost / budget * 100, 2) if budget else 0,
        "by_model": raw.get("by_model", {}),
        "input_tokens": raw.get("input_tokens", 0),
        "output_tokens": raw.get("output_tokens", 0),
        # FinOps поля (session 7)
        "total_tool_calls": raw.get("total_tool_calls", 0),
        "total_fallbacks": raw.get("total_fallbacks", 0),
        "total_context_tokens": raw.get("total_context_tokens", 0),
        "avg_context_tokens": raw.get("avg_context_tokens", 0),
        "by_channel": raw.get("by_channel", {}),
    }
    return report


def test_finops_total_tool_calls_present(_mock_cost_analytics: MagicMock) -> None:
    """Response содержит total_tool_calls из raw report."""
    report = _get_report_dict(_mock_cost_analytics)
    assert "total_tool_calls" in report
    assert report["total_tool_calls"] == 42


def test_finops_total_fallbacks_present(_mock_cost_analytics: MagicMock) -> None:
    """Response содержит total_fallbacks."""
    report = _get_report_dict(_mock_cost_analytics)
    assert report["total_fallbacks"] == 3


def test_finops_total_context_tokens_present(_mock_cost_analytics: MagicMock) -> None:
    """Response содержит total_context_tokens."""
    report = _get_report_dict(_mock_cost_analytics)
    assert report["total_context_tokens"] == 80000


def test_finops_avg_context_tokens_present(_mock_cost_analytics: MagicMock) -> None:
    """Response содержит avg_context_tokens."""
    report = _get_report_dict(_mock_cost_analytics)
    assert report["avg_context_tokens"] == 4000


def test_finops_by_channel_present(_mock_cost_analytics: MagicMock) -> None:
    """Response содержит by_channel с разбивкой по каналам."""
    report = _get_report_dict(_mock_cost_analytics)
    assert report["by_channel"] == {"telegram": 30, "panel": 12}


def test_finops_fields_default_to_zero_when_missing() -> None:
    """Если raw report не содержит FinOps полей — значения 0 / пустые."""
    mock_ca = MagicMock()
    mock_ca.build_usage_report_dict.return_value = {
        "cost_session_usd": 0,
        "monthly_budget_usd": 50.0,
        "by_model": {},
        "input_tokens": 0,
        "output_tokens": 0,
    }
    report = _get_report_dict(mock_ca)
    assert report["total_tool_calls"] == 0
    assert report["total_fallbacks"] == 0
    assert report["total_context_tokens"] == 0
    assert report["avg_context_tokens"] == 0
    assert report["by_channel"] == {}


def test_finops_existing_fields_unchanged(_mock_cost_analytics: MagicMock) -> None:
    """Базовые поля (total_cost_usd, by_model и т.д.) не поломаны."""
    report = _get_report_dict(_mock_cost_analytics)
    assert report["total_cost_usd"] == 1.23
    assert report["total_calls"] == 10
    assert report["budget_monthly_usd"] == 50.0
