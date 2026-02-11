# -*- coding: utf-8 -*-
"""
Web Scout Pro v2.0 — Умный поиск и Deep Research.

Возможности:
- search(): быстрый поиск (DuckDuckGo, до 10 результатов)
- search_news(): свежие новости с датами
- deep_research(): многоходовой анализ — 3 волны поиска с разных ракурсов
- summarize_url(): извлечение текста из URL (readability)

Используется в: !scout, !nexus, !news, !research (новый)
Связь: handlers/tools.py → WebScout → AI (model_manager)
"""

import structlog
import asyncio
import re
from duckduckgo_search import DDGS
from typing import List, Dict, Optional

logger = structlog.get_logger("WebScout")


class WebScout:
    """
    Основной модуль поиска в интернете для Краба.
    DuckDuckGo — не требует API-ключа, работает всегда.
    """

    def __init__(self, max_results: int = 5):
        self.max_results = max_results

    async def search(self, query: str, max_results: int = None, region: str = "ru-ru") -> List[Dict]:
        """
        Быстрый поиск в вебе.
        Возвращает list[dict] с title, href, body.
        """
        limit = max_results or self.max_results
        results = []
        try:
            with DDGS() as ddgs:
                ddgs_gen = ddgs.text(query, region=region, safesearch='off', timelimit='d')
                for i, r in enumerate(ddgs_gen):
                    if i >= limit:
                        break
                    results.append({
                        "title": r.get('title', ''),
                        "href": r.get('href', ''),
                        "body": r.get('body', '')
                    })
            logger.info(f"🔍 Search: '{query}' → {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            return []

    async def search_news(self, query: str, max_results: int = None, region: str = "ru-ru") -> List[Dict]:
        """Поиск свежих новостей с датами и источниками."""
        limit = max_results or self.max_results
        results = []
        try:
            with DDGS() as ddgs:
                ddgs_gen = ddgs.news(query, region=region, safesearch='off', timelimit='w')
                for i, r in enumerate(ddgs_gen):
                    if i >= limit:
                        break
                    results.append({
                        "title": r.get('title', ''),
                        "date": r.get('date', ''),
                        "body": r.get('body', ''),
                        "source": r.get('source', ''),
                        "url": r.get('url', '')
                    })
            logger.info(f"🗞️ News: '{query}' → {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"❌ News search error: {e}")
            return []

    async def deep_research(self, query: str, router=None) -> str:
        """
        Deep Research Pro — многоходовой анализ.

        Алгоритм:
        1. Волна 1: прямой поиск по запросу (10 результатов)
        2. AI генерирует 3 уточняющих подзапроса
        3. Волна 2: поиск по каждому подзапросу (5 результатов)
        4. AI компилирует финальный аналитический отчёт

        Возвращает: готовый отчёт (str) или "Нет данных"
        """
        logger.info(f"🧪 Deep Research started: '{query}'")

        # === Волна 1: Основной поиск ===
        wave1 = await self.search(query, max_results=10)
        wave1_news = await self.search_news(query, max_results=5)
        wave1_text = self.format_results(wave1 + wave1_news)

        if not wave1 and not wave1_news:
            return "❌ Не удалось найти информацию по запросу."

        # === Генерация подзапросов через AI ===
        sub_queries = []
        if router:
            try:
                sub_q_prompt = (
                    f"На основе темы '{query}' и предварительных данных:\n\n"
                    f"{wave1_text[:2000]}\n\n"
                    "Сгенерируй РОВНО 3 уточняющих поисковых запроса, "
                    "которые раскроют тему глубже. "
                    "Ответь строго в формате:\n1. запрос 1\n2. запрос 2\n3. запрос 3"
                )
                sub_q_response = await router.route_query(sub_q_prompt, task_type="chat")
                # Парсим подзапросы из ответа AI
                lines = sub_q_response.strip().split('\n')
                for line in lines:
                    # Убираем нумерацию "1. ", "2. " etc.
                    cleaned = re.sub(r'^\d+[\.\)]\s*', '', line.strip())
                    if cleaned and len(cleaned) > 5:
                        sub_queries.append(cleaned)
                sub_queries = sub_queries[:3]  # Максимум 3
                logger.info(f"🔀 Sub-queries generated: {sub_queries}")
            except Exception as e:
                logger.warning(f"Sub-query generation failed: {e}")

        # === Волна 2: Уточняющие поиски ===
        wave2_text = ""
        for sq in sub_queries:
            results = await self.search(sq, max_results=5)
            if results:
                wave2_text += f"\n--- Подзапрос: {sq} ---\n"
                wave2_text += self.format_results(results)
            await asyncio.sleep(0.3)  # Антибан DuckDuckGo

        # === Финальный анализ через AI ===
        if router:
            try:
                final_prompt = (
                    f"# Deep Research Report: {query}\n\n"
                    f"## Данные из основного поиска:\n{wave1_text}\n\n"
                    f"## Данные из уточняющих поисков:\n{wave2_text}\n\n"
                    "---\n"
                    "Составь COMPREHENSIVE аналитический отчёт:\n"
                    "1. **Ключевые факты** — основная информация по теме\n"
                    "2. **Тренды** — что происходит сейчас, куда движется\n"
                    "3. **Риски и проблемы** — потенциальные угрозы\n"
                    "4. **Прогноз** — что ожидать в ближайшие месяцы\n"
                    "5. **Источники** — ключевые URL\n\n"
                    "Пиши на русском (если не попросили иное)."
                )
                report = await router.route_query(
                    final_prompt,
                    task_type="reasoning"
                )
                logger.info(f"✅ Deep Research completed: '{query}'")
                return report
            except Exception as e:
                logger.error(f"Deep Research AI analysis failed: {e}")
                return f"📊 Собранные данные:\n\n{wave1_text}\n{wave2_text}"
        else:
            return f"📊 Собранные данные:\n\n{wave1_text}\n{wave2_text}"

    def format_results(self, results: List[Dict]) -> str:
        """Форматирование результатов для AI-промпта."""
        if not results:
            return "Результатов не найдено."

        output = ""
        for i, r in enumerate(results, 1):
            if 'date' in r and r['date']:
                # Формат для новостей
                output += (
                    f"{i}. [{r['date']}] {r['title']}\n"
                    f"   Источник: {r.get('source', '—')}\n"
                    f"   Суть: {r['body']}\n"
                    f"   URL: {r.get('url', '')}\n\n"
                )
            else:
                # Формат для обычного поиска
                output += (
                    f"{i}. {r['title']}\n"
                    f"   Суть: {r['body']}\n"
                    f"   URL: {r.get('href', '')}\n\n"
                )
        return output
