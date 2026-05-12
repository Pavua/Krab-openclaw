# -*- coding: utf-8 -*-
"""
Wave 120: тесты search engine analytics (Brave count + cost).

Используем `_value.get()` у prometheus Counter'ов для inspect значения
до и после вызова. Caveat: counter — process-global, потому делаем
delta-сравнения, а не абсолютные значения.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.search_engine as search_engine_module
from src.core.metrics import search as search_metrics
from src.core.metrics.search import (
    BRAVE_REQUEST_COST_EUR,
    krab_search_calls_total,
    krab_search_cost_eur_total,
    record_search_call,
)
from src.search_engine import search_brave


def _counter_value(counter, **labels) -> float:
    """Прочитать текущее значение Counter'a с заданными labels."""
    try:
        return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]
    except Exception:
        return 0.0


class TestRecordSearchCall:
    """Юнит-тесты helper'a record_search_call()."""

    def test_increments_calls_counter_on_ok(self) -> None:
        before = _counter_value(krab_search_calls_total, provider="brave", status="ok")
        record_search_call("brave", "ok", BRAVE_REQUEST_COST_EUR)
        after = _counter_value(krab_search_calls_total, provider="brave", status="ok")
        assert after - before == pytest.approx(1.0)

    def test_adds_cost_on_ok(self) -> None:
        before = _counter_value(krab_search_cost_eur_total, provider="brave")
        record_search_call("brave", "ok", BRAVE_REQUEST_COST_EUR)
        after = _counter_value(krab_search_cost_eur_total, provider="brave")
        assert after - before == pytest.approx(BRAVE_REQUEST_COST_EUR)

    def test_error_does_not_add_cost(self) -> None:
        """Error path: counter calls increments, cost — нет."""
        cost_before = _counter_value(krab_search_cost_eur_total, provider="brave")
        calls_before = _counter_value(krab_search_calls_total, provider="brave", status="error")

        record_search_call("brave", "error", 0.0)

        cost_after = _counter_value(krab_search_cost_eur_total, provider="brave")
        calls_after = _counter_value(krab_search_calls_total, provider="brave", status="error")

        assert calls_after - calls_before == pytest.approx(1.0)
        assert cost_after == pytest.approx(cost_before)

    def test_timeout_status_supported(self) -> None:
        before = _counter_value(krab_search_calls_total, provider="brave", status="timeout")
        record_search_call("brave", "timeout", 0.0)
        after = _counter_value(krab_search_calls_total, provider="brave", status="timeout")
        assert after - before == pytest.approx(1.0)

    def test_fail_safe_no_raise_on_prom_failure(self) -> None:
        """Если prometheus_client кидает — record_search_call глотает (hot-path safe)."""
        broken = MagicMock()
        broken.labels = MagicMock(side_effect=RuntimeError("boom"))

        with patch.object(search_metrics, "krab_search_calls_total", broken):
            # Не должно бросить.
            record_search_call("brave", "ok", BRAVE_REQUEST_COST_EUR)


class TestSearchBraveMetricsWiring:
    """Интеграционные тесты: search_brave() пишет метрики."""

    @pytest.mark.asyncio
    async def test_ok_path_records_call_and_cost(self) -> None:
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(return_value="Хороший результат")

        calls_before = _counter_value(krab_search_calls_total, provider="brave", status="ok")
        cost_before = _counter_value(krab_search_cost_eur_total, provider="brave")

        with (
            patch.object(search_engine_module, "search_cache", mock_cache),
            patch.object(search_engine_module, "mcp_manager", mock_mcp),
        ):
            await search_brave("wave 120 query")

        calls_after = _counter_value(krab_search_calls_total, provider="brave", status="ok")
        cost_after = _counter_value(krab_search_cost_eur_total, provider="brave")

        assert calls_after - calls_before == pytest.approx(1.0)
        assert cost_after - cost_before == pytest.approx(BRAVE_REQUEST_COST_EUR)

    @pytest.mark.asyncio
    async def test_cache_hit_does_not_record_call(self) -> None:
        """Кэш-хит — внешнего вызова нет, метрики не двигаются."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = "cached payload"

        calls_before = _counter_value(krab_search_calls_total, provider="brave", status="ok")
        cost_before = _counter_value(krab_search_cost_eur_total, provider="brave")

        with patch.object(search_engine_module, "search_cache", mock_cache):
            result = await search_brave("cached query")

        assert "cached payload" in result
        assert _counter_value(krab_search_calls_total, provider="brave", status="ok") == calls_before
        assert _counter_value(krab_search_cost_eur_total, provider="brave") == cost_before

    @pytest.mark.asyncio
    async def test_exception_path_records_error_without_cost(self) -> None:
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(side_effect=OSError("connection refused"))

        calls_before = _counter_value(krab_search_calls_total, provider="brave", status="error")
        cost_before = _counter_value(krab_search_cost_eur_total, provider="brave")

        with (
            patch.object(search_engine_module, "search_cache", mock_cache),
            patch.object(search_engine_module, "mcp_manager", mock_mcp),
        ):
            result = await search_brave("err query")

        assert "❌" in result
        assert (
            _counter_value(krab_search_calls_total, provider="brave", status="error") - calls_before
            == pytest.approx(1.0)
        )
        assert _counter_value(krab_search_cost_eur_total, provider="brave") == pytest.approx(
            cost_before
        )

    @pytest.mark.asyncio
    async def test_timeout_path_records_timeout_status(self) -> None:
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(side_effect=asyncio.TimeoutError())

        calls_before = _counter_value(
            krab_search_calls_total, provider="brave", status="timeout"
        )

        with (
            patch.object(search_engine_module, "search_cache", mock_cache),
            patch.object(search_engine_module, "mcp_manager", mock_mcp),
        ):
            # asyncio.TimeoutError IS TimeoutError (Py 3.11+) → OSError subclass →
            # ловится outer except-фильтром и возвращается строкой с ❌.
            # Главное — что timeout-counter инкрементируется ДО reraise.
            result = await search_brave("timeout query")
        assert "❌" in result

        assert (
            _counter_value(krab_search_calls_total, provider="brave", status="timeout")
            - calls_before
            == pytest.approx(1.0)
        )

    @pytest.mark.asyncio
    async def test_error_marker_result_records_error_without_cost(self) -> None:
        """MCP вернул `❌ ...` без exception — error status, cost не списан."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(return_value="❌ rate limit")

        calls_before = _counter_value(krab_search_calls_total, provider="brave", status="error")
        cost_before = _counter_value(krab_search_cost_eur_total, provider="brave")

        with (
            patch.object(search_engine_module, "search_cache", mock_cache),
            patch.object(search_engine_module, "mcp_manager", mock_mcp),
        ):
            result = await search_brave("rate limit query")

        assert result == "❌ rate limit"
        assert (
            _counter_value(krab_search_calls_total, provider="brave", status="error") - calls_before
            == pytest.approx(1.0)
        )
        assert _counter_value(krab_search_cost_eur_total, provider="brave") == pytest.approx(
            cost_before
        )
