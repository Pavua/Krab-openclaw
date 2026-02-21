# -*- coding: utf-8 -*-
"""
Agent Workflow Manager v2.0 (Phase 8.1).
Управляет автономными цепочками рассуждений (Chain of Thought).
"""

import structlog
from typing import List, Dict, Any, Optional
from src.core.agent_executor import AgentExecutor

logger = structlog.get_logger("AgentManager")

class AgentWorkflow:
    def __init__(self, router, memory, security, tools=None):
        self.router = router
        self.memory = memory
        self.security = security
        self.tools = tools
        self.executor = None
        
        if tools:
            self.executor = AgentExecutor(router, tools, memory)

    async def solve_complex_task(self, prompt: str, chat_id: int) -> str:
        """
        Реализует автономное решение через ReAct или Consilium.
        """
        logger.info("🚀 Agent Workflow Triggered", prompt=prompt[:50], chat_id=chat_id)
        
        # Если доступны инструменты, используем ReAct Executor
        if self.executor:
            try:
                result = await self.executor.run(prompt, chat_id)
                return f"🤖 **Autonomous Result:**\n\n{result}"
            except Exception as e:
                logger.error("Agent Executor failed", error=str(e))
                return f"⚠️ Ошибка при выполнении автономной задачи: {e}"
        
        # Fallback на старую логику Plan-Execute если инструментов нет
        logger.warning("No tools available for ReAct, falling back to simple logic")
        summary = self.memory.get_summary(chat_id)
        summary_context = f"\n### КРАТКАЯ ПРЕДЫСТОРИЯ ЧАТА:\n{summary}" if summary else ""

        thought_prompt = f"""{summary_context}
### ЗАДАЧА:
{prompt}

### ИНСТРУКЦИЯ ДЛЯ ПЛАНИРОВЩИКА:
1. Проанализируй текущую задачу.
2. Разбей её на подзадачи.
3. Опиши стратегию решения.
Не давай ответ сразу. Только план."""

        plan = await self.router.route_query(thought_prompt, task_type='reasoning')
        
        execution_prompt = f"""{summary_context}
### ГЛАВНАЯ ЦЕЛЬ:
{prompt}

### ПЛАН:
{plan}

### ИНСТРУКЦИЯ:
Предоставь финальный результат согласно плану."""

        final_answer = await self.router.route_query(execution_prompt, task_type='chat')
        return f"🧠 **Архитектура решения:**\n{plan}\n\n✅ **Результат:**\n{final_answer}"
