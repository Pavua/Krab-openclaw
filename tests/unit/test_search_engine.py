# -*- coding: utf-8 -*-
"""
Тесты для src/search_engine.py.

Покрытие:
1. Кэш-хит — возвращает кэшированный результат с пометкой.
2. Промах кэша — вызывает mcp_manager.search_web.
3. Успешный результат сохраняется в кэш (TTL 3600).
4. Результат с ❌ — не кэшируется.
5. Пустой результат — не кэшируется.
6. OSError из mcp_manager — возвращает строку с ❌.
7. ValueError из mcp_manager — возвращает строку с ❌.
8. AttributeError из mcp_manager — возвращает строку с ❌.
9. close_search вызывает mcp_manager.stop_all.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.search_engine as search_engine_module
from src.search_engine import close_search, search_brave


class TestSearchBrave:
    """Тесты функции search_brave."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_with_marker(self) -> None:
        """Кэш-хит: возвращается кэшированный результат с пометкой '_(восстановлено из кэша)_'."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = "Результат из кэша"

        with patch.object(search_engine_module, "search_cache", mock_cache):
            result = await search_brave("тест запрос")

        assert "Результат из кэша" in result
        assert "_(восстановлено из кэша)_" in result

    @pytest.mark.asyncio
    async def test_cache_miss_calls_mcp_manager(self) -> None:
        """Промах кэша: вызывается mcp_manager.search_web."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(return_value="Свежий результат")

        with patch.object(search_engine_module, "search_cache", mock_cache), \
             patch.object(search_engine_module, "mcp_manager", mock_mcp):
            result = await search_brave("новый запрос")

        mock_mcp.search_web.assert_called_once_with("новый запрос")
        assert result == "Свежий результат"

    @pytest.mark.asyncio
    async def test_successful_result_is_cached(self) -> None:
        """Успешный результат сохраняется в кэш с TTL 3600."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(return_value="Хороший результат")

        with patch.object(search_engine_module, "search_cache", mock_cache), \
             patch.object(search_engine_module, "mcp_manager", mock_mcp):
            await search_brave("запрос для кэша")

        mock_cache.set.assert_called_once_with("запрос для кэша", "Хороший результат", ttl=3600)

    @pytest.mark.asyncio
    async def test_error_result_not_cached(self) -> None:
        """Результат с ❌ не кэшируется."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(return_value="❌ Ошибка поиска")

        with patch.object(search_engine_module, "search_cache", mock_cache), \
             patch.object(search_engine_module, "mcp_manager", mock_mcp):
            result = await search_brave("провальный запрос")

        mock_cache.set.assert_not_called()
        assert result == "❌ Ошибка поиска"

    @pytest.mark.asyncio
    async def test_empty_result_not_cached(self) -> None:
        """Пустой результат не кэшируется."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(return_value="")

        with patch.object(search_engine_module, "search_cache", mock_cache), \
             patch.object(search_engine_module, "mcp_manager", mock_mcp):
            await search_brave("пустой запрос")

        mock_cache.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_os_error_returns_error_string(self) -> None:
        """OSError из mcp_manager обрабатывается — возвращается строка с ❌."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(side_effect=OSError("connection refused"))

        with patch.object(search_engine_module, "search_cache", mock_cache), \
             patch.object(search_engine_module, "mcp_manager", mock_mcp):
            result = await search_brave("запрос с ошибкой")

        assert "❌" in result
        assert "connection refused" in result

    @pytest.mark.asyncio
    async def test_value_error_returns_error_string(self) -> None:
        """ValueError из mcp_manager обрабатывается — возвращается строка с ❌."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(side_effect=ValueError("invalid query"))

        with patch.object(search_engine_module, "search_cache", mock_cache), \
             patch.object(search_engine_module, "mcp_manager", mock_mcp):
            result = await search_brave("невалидный запрос")

        assert "❌" in result
        assert "invalid query" in result

    @pytest.mark.asyncio
    async def test_attribute_error_returns_error_string(self) -> None:
        """AttributeError (например, mcp не запущен) обрабатывается — строка с ❌."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_mcp = MagicMock()
        mock_mcp.search_web = AsyncMock(side_effect=AttributeError("NoneType has no attribute"))

        with patch.object(search_engine_module, "search_cache", mock_cache), \
             patch.object(search_engine_module, "mcp_manager", mock_mcp):
            result = await search_brave("запрос attribute error")

        assert "❌" in result


class TestCloseSearch:
    """Тесты функции close_search."""

    @pytest.mark.asyncio
    async def test_close_search_calls_stop_all(self) -> None:
        """close_search вызывает mcp_manager.stop_all()."""
        mock_mcp = MagicMock()
        mock_mcp.stop_all = AsyncMock()

        with patch.object(search_engine_module, "mcp_manager", mock_mcp):
            await close_search()

        mock_mcp.stop_all.assert_called_once()
