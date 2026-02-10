# -*- coding: utf-8 -*-
"""
Agent Workflow Manager (Phase 6.1).
Управляет автономными цепочками рассуждений (Chain of Thought).
"""

import structlog
from typing import List, Dict, Any, Optional

logger = structlog.get_logger("AgentManager")

class AgentWorkflow:
    def __init__(self, router, memory, security):
        self.router = router
        self.memory = memory
        self.security = security

    async def solve_complex_task(self, prompt: str, chat_id: int) -> str:
        """
        Реализует Swarm Intelligence (Phase 8):
        1. Извлечение памяти (Summary + Context).
        2. Глубокое планирование (Reasoning).
        3. Параллельное выполнение подзадач (в будущем) / Исполнение плана.
        """
        logger.info("🚀 Swarm Agent Started", prompt=prompt[:50], chat_id=chat_id)
        
        # 0. Извлекаем долгосрочную память (Summary)
        summary = self.memory.get_summary(chat_id)
        summary_context = f"\n### КРАТКАЯ ПРЕДЫСТОРИЯ ЧАТА:\n{summary}" if summary else ""

        # 1. Цепочка рассуждений (Planner)
        thought_prompt = f"""{summary_context}
### ЗАДАЧА:
{prompt}

### ИНСТРУКЦИЯ ДЛЯ ПЛАНИРОВЩИКА:
Ты — Главный Архитектор Swarm-системы. 
1. Проанализируй текущую задачу с учетом предыстории.
2. Разбей её на 2-4 конкретные подзадачи.
3. Опиши стратегию решения. 
Не давай ответ сразу. Только структурный план."""

        plan = await self.router.route_query(thought_prompt, task_type='reasoning')
        logger.info("✅ Plan Generated", plan_len=len(plan))

        # 2. Исполнение (Executor)
        # TODO: В будущем здесь будет asyncio.gather для подзадач
        execution_prompt = f"""{summary_context}
### ГЛАВНАЯ ЦЕЛИ:
{prompt}

### УТВЕРЖДЕННЫЙ ПЛАН:
{plan}

### ИНСТРУКЦИЯ ДЛЯ ИСПОЛНИТЕЛЯ:
Строго следуй плану. Предоставь профессиональный, законченный результат.
Если в плане были расчеты или код — выполни их безупречно."""

        final_answer = await self.router.route_query(execution_prompt, task_type='chat')
        
        logger.info("🏁 Swarm Agent Finished")
        return f"🧠 **Архитектура решения:**\n{plan}\n\n✅ **Результат исполнения:**\n{final_answer}"

