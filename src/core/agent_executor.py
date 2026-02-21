# -*- coding: utf-8 -*-
"""
ReAct Agent Executor v1.0 (Phase 8.1).
Реализует цикл Reason-Act-Observe для автономного решения задач.
"""

import structlog
import json
import asyncio
from typing import List, Dict, Any, Optional

logger = structlog.get_logger("AgentExecutor")

REACT_PROMPT = """
Ты — Автономный Агент Krab. Твоя цель — решить задачу пользователя, используя доступные инструменты.
Ты работаешь в цикле: Мысль (Thought) -> Действие (Action) -> Наблюдение (Observation).

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
{tool_registry}

ТВОЙ ОТВЕТ ДОЛЖЕН БЫТЬ СТРОГО В ФОРМАТЕ JSON:
{{
  "thought": "Твои рассуждения о том, что нужно сделать дальше",
  "action": "имя_инструмента", 
  "action_input": {{ "arg1": "value1" }},
  "final_answer": "Финальный ответ (заполни только если задача решена)"
}}

Если тебе нужно больше одного действия — делай их по очереди. После каждого действия ты получишь 'observation' (результат).
Максимальное количество шагов: {max_steps}.

ТЕКУЩАЯ ЗАДАЧА:
{query}

ПРЕДЫСТОРИЯ ЧАТА (КРАТКО):
{summary}
"""

class AgentExecutor:
    def __init__(self, router, tools, memory):
        self.router = router
        self.tools = tools
        self.memory = memory
        self.max_steps = 5

    async def run(self, query: str, chat_id: int) -> str:
        """Запуск цикла ReAct."""
        summary = self.memory.get_summary(chat_id) or "Нет предыстории."
        tool_registry = self.tools.get_tool_registry()
        
        history = []
        
        logger.info("🤖 ReAct Loop Started", query=query, chat_id=chat_id)
        
        for step in range(self.max_steps):
            # Формируем промпт с учетом истории шагов
            steps_context = "\n".join(history)
            prompt = REACT_PROMPT.format(
                tool_registry=tool_registry,
                max_steps=self.max_steps,
                query=query,
                summary=summary
            )
            if history:
                prompt += f"\n\nТЕКУЩИЙ ПРОГРЕСС:\n{steps_context}"

            # Вызываем LLM
            response_raw = await self.router.route_query(prompt, task_type='reasoning')
            
            try:
                # Очищаем ответ от markdown блоков если они есть
                clean_json = response_raw.strip()
                if "```json" in clean_json:
                    clean_json = clean_json.split("```json")[1].split("```")[0].strip()
                elif "```" in clean_json:
                    clean_json = clean_json.split("```")[1].split("```")[0].strip()
                
                decision = json.loads(clean_json)
            except Exception as e:
                logger.error("❌ Failed to parse agent decision", error=str(e), raw=response_raw)
                return f"⚠️ Ошибка в рассуждениях агента: {e}. Я получил: {response_raw}"

            thought = decision.get("thought", "...")
            action = decision.get("action")
            action_input = decision.get("action_input", {})
            final_answer = decision.get("final_answer")

            logger.info(f"Step {step+1}: {thought}", action=action)

            if final_answer:
                logger.info("✅ Final Answer Reached")
                return final_answer

            if action:
                # Добавляем chat_id в аргументы инструмента для напоминаний и прочего
                action_input["chat_id"] = chat_id
                
                # Выполняем инструмент
                observation = await self.tools.execute_named_tool(action, **action_input)
                logger.info(f"Observation: {str(observation)[:100]}...")
                
                # Добавляем в историю для следующего шага
                history.append(f"Step {step+1}:\nThought: {thought}\nAction: {action}({action_input})\nObservation: {observation}")
            else:
                # Если нет действия и нет финального ответа — что-то пошло не так
                return f"⚠️ Агент зашел в тупик на шаге {step+1}."

        return "⏳ Превышено максимальное количество шагов (5). Текущие результаты: " + "\n".join(history)
