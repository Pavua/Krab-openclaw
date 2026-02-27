"""
Web Search Skill - Поиск информации через Brave Search API
"""
import httpx
import structlog
from typing import Optional
from src.config import config

logger = structlog.get_logger(__name__)

async def search_web(query: str) -> str:
    """Ищет информацию в интернете через Brave Search API"""
    api_key = config.BRAVE_SEARCH_API_KEY
    
    if not api_key:
        # Fallback to simple DuckDuckGo link if no API key
        return f"🔍 Я не нашел ключа Brave Search. Вот ссылка для ручного поиска: https://duckduckgo.com/?q={query.replace(' ', '+')}"

    try:
        async with httpx.AsyncClient() as client:
            headers = {
                "Accept": "application/json",
                "X-Subscription-Token": api_key
            }
            url = f"https://api.search.brave.com/res/v1/web/search?q={query}&count=3"
            response = await client.get(url, headers=headers)
            
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
            
    except (httpx.HTTPError, OSError, KeyError) as e:
        logger.error("brave_search_failed", error=str(e))
        return f"❌ Ошибка при поиске: {str(e)}"
