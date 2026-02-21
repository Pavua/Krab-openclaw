# -*- coding: utf-8 -*-
"""
src/core/swarm.py
~~~~~~~~~~~~~~~~~
Оркестратор для параллельного выполнения задач (Swarm Intelligence).
Реализован в рамках Фазы 10.

Обеспечивает:
1. Параллельный вызов инструментов (parallel_exec).
2. Автономное принятие решений (autonomous_decision).
"""

import asyncio
import inspect
import structlog
from typing import List, Dict, Any, Callable

logger = structlog.get_logger("Swarm")

class SwarmTask:
    """Описание отдельной задачи для выполнения в рое."""
    def __init__(self, name: str, func: Callable, *args, **kwargs):
        self.name = name
        self.func = func
        self.args = args
        self.kwargs = kwargs

class SwarmOrchestrator:
    def __init__(self, tool_handler, router=None):
        self.tools = tool_handler
        self.router = router
        logger.info("🐝 SwarmOrchestrator v2.1 initialized")

    async def execute_parallel(self, tasks: List[SwarmTask]) -> Dict[str, Any]:
        """Запускает задачи параллельно и собирает результаты."""
        logger.info(f"🚀 Running {len(tasks)} tasks in parallel")
        
        async def _run_safe(task: SwarmTask):
            try:
                result = task.func(*task.args, **task.kwargs)
                return task.name, await self._resolve_maybe_awaitable(result)
            except Exception as e:
                logger.error(f"Task {task.name} failed", error=str(e))
                return task.name, f"Error: {e}"

        coroutines = [_run_safe(t) for t in tasks]
        results = await asyncio.gather(*coroutines)
        return dict(results)

    @staticmethod
    async def _resolve_maybe_awaitable(value: Any) -> Any:
        """Возвращает значение, дожидаясь awaitable только при необходимости."""
        if inspect.isawaitable(value):
            return await value
        return value

    async def autonomous_decision(self, query: str, **kwargs) -> str:
        """
        [PHASE 10] Автономно решает, какие инструменты нужны, 
        запускает их и объединяет ответ.
        [v11.3] Добавлена защита от рекурсии через skip_swarm.
        """
        if kwargs.get("skip_swarm"):
            logger.info("⏩ Swarm skipping (recursion guard active)")
            if self.router:
                routed = await self._resolve_maybe_awaitable(
                    self.router.route_query(query, skip_swarm=True)
                )
                if isinstance(routed, str) and routed.strip():
                    return routed
            return "Swarm skipped: recursion guard."

        logger.info("🧠 Swarm Autonomous Decision", query=query)
        
        # Если роутер доступен, мы можем спросить его о плане
        plan = None
        # ... (логика планирования может быть расширена здесь)

        # Имитируем параллельный сбор данных (Search + RAG)
        tasks = []
        lowered = query.lower()
        
        # Helper for calling tools
        async def call_tool(name, **tool_kwargs):
            if hasattr(self.tools, "execute_named_tool"):
                return await self._resolve_maybe_awaitable(
                    self.tools.execute_named_tool(name, **tool_kwargs)
                )
            
            # Legacy/Mock Fallback
            if name == "web_search" and hasattr(self.tools, "scout"):
                return await self._resolve_maybe_awaitable(
                    self.tools.scout.search(tool_kwargs.get("query", ""))
                )
            if name == "rag_search" and hasattr(self.tools, "rag"):
                return self.tools.rag.query(tool_kwargs.get("query", ""))
            return f"Error: Tool {name} not found in handler"

        if any(w in lowered for w in ["найди", "поищи", "новости", "гугл", "интернет"]):
             tasks.append(SwarmTask("WebSearch", call_tool, "web_search", query=query))
             
        if any(w in lowered for w in ["вспомни", "память", "архив", "говорил"]):
             tasks.append(SwarmTask("Memory", call_tool, "rag_search", query=query))

        if not tasks:
            # Если ничего не выбрали, спросим роутер напрямую, запрещая повторный вход в Swarm
            if self.router:
                return await self.router.route_query(query, skip_swarm=True)
            return "Недостаточно контекста для Swarm."

        # Выполняем параллельно
        results = await self.execute_parallel(tasks)
        
        # Формируем обогащенный контекст
        context = "[SWARM]\n"
        for name, res in results.items():
            context += f"--- Source: {name} ---\n{res}\n"
        
        final_prompt = f"Данные из роя инструментов:\n{context}\n\nОригинальный запрос: {query}\n\nСформулируй финальный ответ."
        
        if self.router:
            # Передаем skip_swarm=True, чтобы роутер не пытался снова запустить execute_tool_chain
            try:
                routed = await self._resolve_maybe_awaitable(
                    self.router.route_query(final_prompt, skip_swarm=True)
                )
                if isinstance(routed, str) and routed.strip():
                    return routed
            except Exception as e:
                logger.warning("Swarm router fallback to raw context", error=str(e))
        
        return f"✅ Собранные данные из роя:\n{context}"
