"""
Web Search Skill - Поиск информации через Brave Search API
"""
import os
from urllib.parse import quote_plus

import httpx
import structlog

logger = structlog.get_logger(__name__)

async def search_web(query: str) -> str:
    """Ищет информацию в интернете через Brave Search API"""
    # Совместимость с двумя именами env-переменной:
    # - BRAVE_SEARCH_API_KEY (новое/явное)
    # - BRAVE_API_KEY (legacy)
    api_key = (os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY") or "").strip()
    
    if not api_key:
        # Fallback to simple DuckDuckGo link if no API key
        return f"🔍 Я не нашел ключа Brave Search. Вот ссылка для ручного поиска: https://duckduckgo.com/?q={query.replace(' ', '+')}"

    try:
        async with httpx.AsyncClient() as client:
            headers = {
                "Accept": "application/json",
                "X-Subscription-Token": api_key
            }
            safe_query = quote_plus(query)
            url = f"https://api.search.brave.com/res/v1/web/search?q={safe_query}&count=3"
            response = await client.get(url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                return f"❌ Ошибка Brave Search ({response.status_code}): {response.text}"
                
            data = response.json()
            results = data.get("web", {}).get("results", [])
            
            if not results:
                return "🔍 По твоему запросу ничего не найдено."
            
            formatted_results = []
            for res in results:
                title = res.get("title", "Без названия")
                description = res.get("description", "")
                url = res.get("url", "")
                formatted_results.append(f"🔹 **[{title}]({url})**\n{description}")
            
            return "🔍 **Результаты поиска:**\n\n" + "\n\n".join(formatted_results)
            
    except Exception as e:
        logger.error("brave_search_failed", error=str(e))
        return f"❌ Ошибка при поиске: {str(e)}"
