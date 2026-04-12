# -*- coding: utf-8 -*-
"""Тесты для cost_budget alert в proactive_watch."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture()
def _mock_inbox():
    """Mock inbox_service для изоляции тестов."""
    with patch("src.core.proactive_watch.inbox_service") as mock:
        mock.build_identity.return_value = {
            "operator_id": "test",
            "account_id": "test",
            "channel_id": "system",
            "team_id": "owner",
            "trace_id": "test",
            "approval_scope": "owner",
        }
        yield mock


def _make_service():
    from src.core.proactive_watch import ProactiveWatchService

    return ProactiveWatchService()


def _patch_ca(budget: float, spent: float):
    """Патчит cost_analytics с заданными budget и spent."""
    ca_mock = type("CA", (), {
        "get_monthly_budget_usd": lambda self: budget,
        "get_monthly_cost_usd": lambda self: spent,
    })()
    return patch("src.core.cost_analytics.cost_analytics", ca_mock)


def test_cost_budget_no_budget_set(_mock_inbox):
    """��лерт не срабатывает если бюджет не установлен (0)."""
    svc = _make_service()
    with _patch_ca(0, 0):
        assert svc._check_cost_budget() is False
    _mock_inbox.upsert_item.assert_not_called()


def test_cost_budget_under_threshold(_mock_inbox):
    """Алерт не срабатывает при расходах <80% бюджета."""
    svc = _make_service()
    with _patch_ca(50.0, 30.0):
        assert svc._check_cost_budget() is False
    _mock_inbox.upsert_item.assert_not_called()


def test_cost_budget_warning_at_80pct(_mock_inbox):
    """Warning алерт при расходах >80% бюджета."""
    svc = _make_service()
    with _patch_ca(50.0, 42.0):
        assert svc._check_cost_budget() is True
    call_kwargs = _mock_inbox.upsert_item.call_args
    assert call_kwargs is not None
    assert call_kwargs.kwargs["severity"] == "warning"


def test_cost_budget_error_at_100pct(_mock_inbox):
    """Error алерт при превышении бюджета."""
    svc = _make_service()
    with _patch_ca(50.0, 55.0):
        assert svc._check_cost_budget() is True
    call_kwargs = _mock_inbox.upsert_item.call_args
    assert call_kwargs is not None
    assert call_kwargs.kwargs["severity"] == "error"
    assert "Exceeded" in call_kwargs.kwargs["title"]
