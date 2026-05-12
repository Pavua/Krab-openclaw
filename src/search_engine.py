from __future__ import annotations

import asyncio

from structlog import get_logger

from .cache_manager import search_cache
from .core.metrics.search import BRAVE_REQUEST_COST_EUR, record_search_call
from .mcp_client import mcp_manager

logger = get_logger(__name__)


async def search_brave(query: str) -> str:
    """
    Выполняет поиск через Brave Search MCP.

    Wave 120: метрики counts + projected cost (€0.0046/запрос для Brave Pro).
    Кэш-хиты не тарифицируются и не учитываются как новый вызов.
    """
    try:
        # Check cache (TTL 1 hour) — кэш-хиты не идут в counters (нет внешнего вызова).
        cached = search_cache.get(query)
        if cached:
            logger.info("search_cache_hit", query=query)
            return f"{cached}\n\n_(восстановлено из кэша)_"

        try:
            results = await mcp_manager.search_web(query)
        except asyncio.TimeoutError:
            record_search_call("brave", "timeout", 0.0)
            raise
        except Exception:
            record_search_call("brave", "error", 0.0)
            raise

        # Cache result
        if results and "❌" not in results:
            search_cache.set(query, results, ttl=3600)
            record_search_call("brave", "ok", BRAVE_REQUEST_COST_EUR)
        else:
            # MCP вернул error-маркер без exception — считаем как error (не списываем cost).
            record_search_call("brave", "error", 0.0)

        return results
    except (OSError, ValueError, KeyError, AttributeError, RuntimeError) as e:
        logger.error(
            "search_brave_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        return f"❌ Ошибка поиска: {str(e)}"


async def close_search():
    """Закрытие сессий поиска"""
    await mcp_manager.stop_all()
