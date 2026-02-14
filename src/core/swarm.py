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
    def __init__(self, tool_handler, router=None):
        self.tools = tool_handler
        self.router = router
        # PersonaManager is available via self.router.persona (set in main.py)

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

    async def consilium_reasoning(self, query: str) -> str:
        """
        [PHASE 4.1] Consilium Mode: Multi-agent debate.
        1. Architect: Designs solution.
        2. Coder/Expert: Implements.
        3. Critic: Checks for flaws.
        """
        if not self.router or not getattr(self.router, "persona", None):
            return "⚠️ Consilium недоступен: router/persona не инициализированы."

        logger.info("🏛️ Entering Consilium Mode", query=query[:50])
        
        # Step 1: Architect Plan
        architect_prompt = f"{self.router.persona.get_role_prompt('architect')}\n\nЗАДАЧА: {query}\n\nРазработай верхнеуровневый план решения."
        plan = await self.router.route_query(architect_prompt, task_type='reasoning')
        
        # Step 2: Expert Implementation
        expert_prompt = f"{self.router.persona.get_role_prompt('coder')}\n\nПЛАН: {plan}\n\nРеализуй решение согласно плану."
        solution = await self.router.route_query(expert_prompt, task_type='chat')
        
        # Step 3: Critic Review
        critic_prompt = f"{self.router.persona.get_role_prompt('critic')}\n\nРЕШЕНИЕ: {solution}\n\nНайди ошибки или предложи улучшения."
        feedback = await self.router.route_query(critic_prompt, task_type='reasoning')
        
        # Final Consolidation
        final_prompt = f"### ARCHITECT PLAN:\n{plan}\n\n### EXPERT SOLUTION:\n{solution}\n\n### CRITIC FEEDBACK:\n{feedback}\n\n### TASK:\nНа основе дискуссии выше, выдай финальный идеальный результат."
        final_result = await self.router.route_query(final_prompt, task_type='chat')
        
        return f"🌟 **Consilium Result:**\n\n{final_result}\n\n--- \n🏛️ *Agents involved: Architect, Coder, Critic*"

    async def autonomous_decision(self, query: str) -> str:
        # ... (rest of the code same or improved)
        lower_query = query.lower()
        if "подумай глубоко" in lower_query or "консилиум" in lower_query:
            return await self.consilium_reasoning(query)
        
        tasks_to_run = []
        # ... existing logic ...
        if "поищи" in lower_query or "найди" in lower_query:
             if hasattr(self.tools, "scout") and getattr(self.tools, "scout", None):
                 tasks_to_run.append(SwarmTask("WebSearch", self.tools.scout.search, query))
             
        if "вспомни" in lower_query or "память" in lower_query:
             if hasattr(self.tools, "rag") and getattr(self.tools, "rag", None):
                 tasks_to_run.append(SwarmTask("RAG", self.tools.rag.query, query))
             
        if "файл" in lower_query or "папк" in lower_query:
            if self.tools.mcp:
                tasks_to_run.append(SwarmTask("Filesystem", self.tools.call_mcp_tool, "filesystem", "list_directory", {"path": "."}))

        if not tasks_to_run:
            return None

        results = await self.execute_parallel(tasks_to_run)
        
        formatted = []
        for name, res in results.items():
            if name == "WebSearch":
                if hasattr(self.tools, "scout") and getattr(self.tools, "scout", None) and hasattr(self.tools.scout, "format_results"):
                    res = self.tools.scout.format_results(res)
            formatted.append(f"### [SWARM] {name}:\n{res}")
            
        return "\n\n".join(formatted)
