
from .mcp_client import mcp_manager
from .cache_manager import search_cache
from structlog import get_logger

logger = get_logger(__name__)

async def search_brave(query: str) -> str:
    """
    Выполняет поиск через Brave Search MCP.
    """
    try:
        # Check cache (TTL 1 hour)
        cached = search_cache.get(query)
        if cached:
            logger.info("search_cache_hit", query=query)
            return f"{cached}\n\n_(восстановлено из кэша)_"

        results = await mcp_manager.search_web(query)
        
        # Cache result
        if results and "❌" not in results:
            search_cache.set(query, results, ttl=3600)
            
        return results
    except (OSError, ValueError, KeyError, AttributeError, RuntimeError) as e:
        logger.error("search_brave_failed", error=str(e))
        return f"❌ Ошибка поиска: {str(e)}"

async def close_search():
    """Закрытие сессий поиска"""
    await mcp_manager.stop_all()
