# -*- coding: utf-8 -*-
"""
src/core/swarm.py
~~~~~~~~~~~~~~~~~
Роевой оркестратор и Multi-Agent Room для кооперативного решения задач.

Зачем нужен модуль:
- сохранить совместимость с R17-тестами и утраченной функциональностью после рефакторинга;
- дать стабильный контур «аналитик -> критик -> интегратор» для сложных запросов;
- изолировать логику роя от transport/runtime-слоя (router передается извне).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from .logger import get_logger

logger = get_logger(__name__)


class SwarmTask:
    """Описание отдельной задачи для параллельного выполнения в рое."""

    def __init__(self, name: str, func: Callable, *args: Any, **kwargs: Any) -> None:
        self.name = name
        self.func = func
        self.args = args
        self.kwargs = kwargs


class SwarmOrchestrator:
    """
    Базовый оркестратор параллельных задач.

    Оставлен для обратной совместимости и будущего расширения.
    """

    def __init__(self, tool_handler: Any, router: Any | None = None) -> None:
        self.tools = tool_handler
        self.router = router
        logger.info("swarm_orchestrator_initialized")

    async def execute_parallel(self, tasks: list[SwarmTask]) -> dict[str, Any]:
        """Запускает список задач параллельно и возвращает агрегированные результаты."""
        logger.info("swarm_parallel_started", tasks=len(tasks))

        async def _run_safe(task: SwarmTask) -> tuple[str, Any]:
            try:
                result = task.func(*task.args, **task.kwargs)
                return task.name, await self._resolve_maybe_awaitable(result)
            except Exception as exc:  # noqa: BLE001
                logger.error("swarm_task_failed", task=task.name, error=str(exc))
                return task.name, f"Error: {exc}"

        results = await asyncio.gather(*[_run_safe(task) for task in tasks])
        return dict(results)

    @staticmethod
    async def _resolve_maybe_awaitable(value: Any) -> Any:
        """Дожидается awaitable-значений, но пропускает обычные типы без накладных расходов."""
        if inspect.isawaitable(value):
            return await value
        return value


# ---------------------------------------------------------------------------
# R17: Multi-Agent Room MVP
# ---------------------------------------------------------------------------

DEFAULT_AGENT_ROLES = [
    {
        "name": "analyst",
        "emoji": "🔬",
        "title": "Аналитик",
        "system_hint": (
            "Ты — аналитик. Разбери тему детально: выдели ключевые факты, "
            "тренды и цифры. Без лишних слов, только суть."
        ),
    },
    {
        "name": "critic",
        "emoji": "🎯",
        "title": "Критик",
        "system_hint": (
            "Ты — критик. Учитывая анализ выше, найди слабые стороны, "
            "риски и упущенные нюансы. Будь конкретен."
        ),
    },
    {
        "name": "integrator",
        "emoji": "🧠",
        "title": "Интегратор",
        "system_hint": (
            "Ты — интегратор. Учитывая анализ и критику выше, сформулируй "
            "финальный вывод с четкими рекомендациями."
        ),
    },
]


class AgentRoom:
    """
    Последовательный оркестратор «комнаты агентов».

    Контракт с роутером:
    - должен поддерживать `await route_query(prompt, skip_swarm=True)`.
    - сам роутер отвечает за transport, модель и retry policy.
    """

    def __init__(self, roles: list[dict[str, str]] | None = None, *, role_context_clip: int = 1200) -> None:
        self.roles = roles or DEFAULT_AGENT_ROLES
        self.role_context_clip = max(200, int(role_context_clip))
        logger.info("agent_room_initialized", roles=[r.get("name", "agent") for r in self.roles])

    async def run_round(self, topic: str, router: Any) -> str:
        """
        Запускает полный роевой раунд по теме `topic`.

        Порядок ролей:
        1) Аналитик
        2) Критик (видит результат аналитика)
        3) Интегратор (видит результаты аналитика и критика)
        """
        accumulated_context = ""
        round_results: list[dict[str, str]] = []

        logger.info("agent_room_round_started", topic=topic, roles=len(self.roles))

        for role in self.roles:
            name = str(role.get("name", "agent"))
            emoji = str(role.get("emoji", "🤖"))
            title = str(role.get("title", name))
            hint = str(role.get("system_hint", "")).strip()

            if accumulated_context:
                prompt = (
                    f"{hint}\n\n"
                    f"--- Контекст предыдущих ролей ---\n{accumulated_context}\n"
                    f"---\n\nТема: {topic}"
                )
            else:
                prompt = f"{hint}\n\nТема: {topic}"

            try:
                response = await router.route_query(prompt, skip_swarm=True)
            except Exception as exc:  # noqa: BLE001
                response = f"[Ошибка роли {name}: {exc}]"
                logger.warning("agent_room_role_failed", role=name, error=str(exc))

            clipped = str(response or "").strip()[: self.role_context_clip]
            if not clipped:
                clipped = "[Пустой ответ роли: проверьте контекст, лимиты или состояние модели]"
                logger.warning("agent_room_role_empty_response", role=name, topic=topic)
            round_results.append({"role": name, "emoji": emoji, "title": title, "text": clipped})
            accumulated_context += f"[{emoji} {title}]:\n{clipped}\n\n"

        header = f"🐝 **Swarm Room: {topic}**\n\n"
        body = ""
        for result in round_results:
            body += f"**{result['emoji']} {result['title']}:**\n{result['text']}\n\n"

        logger.info("agent_room_round_completed", topic=topic)
        return header + body.strip()
