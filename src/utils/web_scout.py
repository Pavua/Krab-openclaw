# -*- coding: utf-8 -*-
"""
Web Scout (Search) Utility
Использует DuckDuckGo для поиска свежих новостей и информации.
"""

import logging
from duckduckgo_search import DDGS
from typing import List, Dict

logger = logging.getLogger("WebScout")

class WebScout:
    def __init__(self, max_results: int = 5):
        self.max_results = max_results

    async def search(self, query: int, region: str = "ru-ru", max_results: int = None) -> List[Dict]:
        """Поиск в вебе."""
        limit = max_results if max_results else self.max_results
        results = []
        try:
            with DDGS() as ddgs:
                ddgs_gen = ddgs.text(query, region=region, safesearch='off', timelimit='d')
                for i, r in enumerate(ddgs_gen):
                    if i >= limit:
                        break
                    results.append({
                        "title": r.get('title'),
                        "href": r.get('href'),
                        "body": r.get('body')
                    })
            return results
        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            return []

    async def search_news(self, query: str, region: str = "ru-ru", max_results: int = None) -> List[Dict]:
        """Поиск свежих новостей."""
        limit = max_results if max_results else self.max_results
        results = []
        try:
            with DDGS() as ddgs:
                ddgs_gen = ddgs.news(query, region=region, safesearch='off', timelimit='d')
                for i, r in enumerate(ddgs_gen):
                    if i >= limit:
                        break
                    results.append({
                        "title": r.get('title'),
                        "date": r.get('date'),
                        "body": r.get('body'),
                        "source": r.get('source'),
                        "url": r.get('url')
                    })
            return results
        except Exception as e:
            logger.error(f"❌ News search error: {e}")
            return []

    def format_results(self, results: List[Dict]) -> str:
        """Форматирование результатов для промпта AI."""
        if not results:
            return "Результатов не найдено."
        
        output = ""
        for i, r in enumerate(results, 1):
            if 'date' in r: # News format
                output += f"{i}. [{r['date']}] {r['title']}\n   Источник: {r['source']}\n   Суть: {r['body']}\n   URL: {r['url']}\n\n"
            else: # Text search format
                output += f"{i}. {r['title']}\n   Суть: {r['body']}\n   URL: {r['href']}\n\n"
        return output
