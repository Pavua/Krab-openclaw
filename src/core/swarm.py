# -*- coding: utf-8 -*-
"""
Swarm Orchestrator v1.0 (Phase 10).
Система "Роя" для параллельного выполнения задач несколькими инструментами и моделями.
Позволяет ускорить получение контекста и объединить результаты из разных источников.
"""

import asyncio
import structlog
from typing import List, Dict, Any, Callable

logger = structlog.get_logger("SwarmOrchestrator")

class SwarmTask:
    def __init__(self, name: str, func: Callable, *args, **kwargs):
        self.name = name
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.result = None
        self.error = None

class SwarmOrchestrator:
    def __init__(self, tool_handler):
        self.tools = tool_handler

    async def execute_parallel(self, tasks: List[SwarmTask]) -> Dict[str, Any]:
        """
        Запускает список задач параллельно и собирает результаты.
        """
        logger.info(f"🐝 Swarm Activated: Executing {len(tasks)} tasks in parallel")
        
        async def _run_task(task: SwarmTask):
            try:
                if asyncio.iscoroutinefunction(task.func):
                    task.result = await task.func(*task.args, **task.kwargs)
                else:
                    task.result = task.func(*task.args, **task.kwargs)
            except Exception as e:
                task.error = str(e)
                logger.error(f"🐝 Swarm Task Error ({task.name}): {e}")

        # Запускаем все задачи одновременно
        await asyncio.gather(*[_run_task(t) for t in tasks])
        
        # Формируем отчет
        results = {}
        for t in tasks:
            results[t.name] = t.result if not t.error else f"Error: {t.error}"
            
        return results

    async def autonomous_decision(self, query: str) -> str:
        """
        Принимает решение о запуске "Роя" на основе запроса.
        Если запрос комплексный (например, "найди в почте и поищи в гугле"), 
        оркестратор запускает несколько инструментов сразу.
        """
        # (v1.0) Упрощенный мапинг — в будущем заменить на LLM-планировщик
        tasks_to_run = []
        lower_query = query.lower()
        
        # Анализ на параллельность
        if "поищи" in lower_query or "найди" in lower_query:
             tasks_to_run.append(SwarmTask("WebSearch", self.tools.scout.search, query))
             
        if "вспомни" in lower_query or "память" in lower_query:
             tasks_to_run.append(SwarmTask("RAG", self.tools.rag.query, query))
             
        if "файл" in lower_query or "папк" in lower_query:
            if self.tools.mcp:
                tasks_to_run.append(SwarmTask("Filesystem", self.tools.call_mcp_tool, "filesystem", "list_directory", {"path": "."}))

        if not tasks_to_run:
            return await self.tools.execute_tool_chain(query)

        results = await self.execute_parallel(tasks_to_run)
        
        # Форматируем общий ответ
        formatted = []
        for name, res in results.items():
            if name == "WebSearch":
                res = self.tools.scout.format_results(res)
            formatted.append(f"### [SWARM] {name}:\n{res}")
            
        return "\n\n".join(formatted)
