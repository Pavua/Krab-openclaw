# -*- coding: utf-8 -*-
"""
src/core/swarm.py
~~~~~~~~~~~~~~~~~
Роевой оркестратор и Multi-Agent Room для кооперативного решения задач.

Зачем нужен модуль:
- сохранить совместимость с R17-тестами и утраченной функциональностью после рефакторинга;
- дать стабильный контур «аналитик -> критик -> интегратор» для сложных запросов;
- изолировать логику роя от transport/runtime-слоя (router передается извне);
- поддерживать межкомандное делегирование через SwarmBus ([DELEGATE: team]).
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from typing import Any, Callable

from .logger import get_logger
from .swarm_channels import swarm_channels
from .swarm_memory import swarm_memory

logger = get_logger(__name__)

# Паттерн для детектирования директив делегирования в ответе роли.
# Форматы: [DELEGATE: coders], [DELEGATE:traders], [DELEGATE: аналитика]
_DELEGATE_PATTERN = re.compile(
    r"\[DELEGATE:\s*([a-zA-Zа-яА-Я_\-]+)\]",
    re.IGNORECASE,
)


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
# R17: Multi-Agent Room MVP  |  R18: delegation support via SwarmBus
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

    R18: Если ответ роли содержит [DELEGATE: <team>], AgentRoom диспатчит
    подзадачу в указанную команду через SwarmBus и инжектирует результат
    в контекст следующей роли.
    """

    def __init__(self, roles: list[dict[str, str]] | None = None, *, role_context_clip: int = 1200) -> None:
        self.roles = roles or DEFAULT_AGENT_ROLES
        self.role_context_clip = max(200, int(role_context_clip))
        logger.info("agent_room_initialized", roles=[r.get("name", "agent") for r in self.roles])

    async def run_round(
        self,
        topic: str,
        router: Any,
        *,
        _bus: Any = None,
        _depth: int = 0,
        _router_factory: Any = None,
        _team_name: str = "",
    ) -> str:
        """
        Запускает полный роевой раунд по теме `topic`.

        Если роль возвращает [DELEGATE: <team>] и предоставлен _bus (SwarmBus),
        задача диспатчится в указанную команду. Результат инжектируется в
        накопленный контекст для следующих ролей.
        """
        t0 = time.monotonic()
        accumulated_context = ""
        round_results: list[dict[str, str]] = []
        delegation_results: list[str] = []

        # Inject контекста из памяти предыдущих прогонов
        memory_context = ""
        if _team_name:
            memory_context = swarm_memory.get_context_for_injection(_team_name)
            if memory_context:
                accumulated_context = memory_context + "\n\n"
                logger.info("agent_room_memory_injected", team=_team_name,
                            context_len=len(memory_context))

        logger.info("agent_room_round_started", topic=topic, roles=len(self.roles), depth=_depth)

        # Live broadcast: анонс начала раунда в swarm-группу
        if _team_name and _depth == 0:
            swarm_channels.mark_round_active(_team_name)
            await swarm_channels.broadcast_round_start(team=_team_name, topic=topic)

        for role in self.roles:
            name = str(role.get("name", "agent"))
            emoji = str(role.get("emoji", "🤖"))
            title = str(role.get("title", name))
            hint = str(role.get("system_hint", "")).strip()

            # Проверяем intervention от владельца перед каждой ролью
            if _team_name:
                intervention = swarm_channels.get_pending_intervention(_team_name)
                if intervention:
                    accumulated_context += intervention
                    logger.info("agent_room_intervention_applied", team=_team_name, role=name)

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

            # Live broadcast: публикуем ответ роли в swarm-группу
            if _team_name and _depth == 0:
                await swarm_channels.broadcast_role_step(
                    team=_team_name, role_name=name, role_emoji=emoji,
                    role_title=title, text=clipped,
                )

            # R18: Детектируем директиву делегирования [DELEGATE: team]
            if _bus is not None and _router_factory is not None:
                m = _DELEGATE_PATTERN.search(clipped)
                if m:
                    delegate_team = m.group(1).strip()
                    # Извлекаем задачу: текст после [DELEGATE: team] или весь ответ
                    delegate_topic = _DELEGATE_PATTERN.sub("", clipped).strip() or topic
                    logger.info(
                        "agent_room_delegation_detected",
                        role=name,
                        target_team=delegate_team,
                        depth=_depth,
                    )
                    delegate_result = await _bus.dispatch(
                        source_team=_team_name or "default",
                        target_team=delegate_team,
                        topic=delegate_topic,
                        router_factory=_router_factory,
                        depth=_depth,
                    )
                    # Инжектируем результат делегирования в контекст
                    delegation_summary = (
                        f"\n\n📬 **Результат от команды {delegate_team}:**\n{delegate_result[:800]}"
                    )
                    clipped += delegation_summary
                    delegation_results.append(f"→ {delegate_team}: задача выполнена")
                    logger.info("agent_room_delegation_injected", role=name, target=delegate_team)

            round_results.append({"role": name, "emoji": emoji, "title": title, "text": clipped})
            accumulated_context += f"[{emoji} {title}]:\n{clipped}\n\n"

        header = f"🐝 **Swarm Room: {topic}**\n\n"
        body = ""
        for result in round_results:
            body += f"**{result['emoji']} {result['title']}:**\n{result['text']}\n\n"

        if delegation_results:
            body += f"📡 **Делегирование:** {', '.join(delegation_results)}\n"

        full_result = header + body.strip()

        # Live broadcast: итог раунда + снимаем active
        if _team_name and _depth == 0:
            last_role_text = round_results[-1]["text"] if round_results else ""
            await swarm_channels.broadcast_round_end(team=_team_name, summary=last_role_text)
            swarm_channels.mark_round_done(_team_name)

        # Сохраняем результат в персистентную память (только top-level раунды)
        if _team_name and _depth == 0:
            duration = time.monotonic() - t0
            swarm_memory.save_run(
                team=_team_name,
                topic=topic,
                result=full_result,
                delegations=delegation_results,
                duration_sec=duration,
            )

        logger.info("agent_room_round_completed", topic=topic, delegations=len(delegation_results))
        return full_result

    async def run_loop(
        self,
        topic: str,
        router: Any,
        *,
        rounds: int = 2,
        max_rounds: int = 3,
        next_round_clip: int = 4000,
        _bus: Any = None,
        _router_factory: Any = None,
        _team_name: str = "",
    ) -> str:
        """
        Запускает несколько раундов роя с итеративной доработкой результата.

        Идея:
        - Раунд 1 создает первичное решение.
        - Следующие раунды перерабатывают решение с учетом критики из предыдущего шага.
        """
        safe_max = max(1, int(max_rounds))
        safe_rounds = max(1, min(int(rounds), safe_max))
        safe_clip = max(500, int(next_round_clip))

        logger.info(
            "agent_room_loop_started",
            topic=topic,
            rounds=safe_rounds,
            max_rounds=safe_max,
        )

        base_topic = str(topic or "").strip()
        current_topic = base_topic
        sections: list[str] = []

        for idx in range(safe_rounds):
            round_no = idx + 1
            round_result = await self.run_round(
                current_topic,
                router,
                _bus=_bus,
                _depth=0,
                _router_factory=_router_factory,
                _team_name=_team_name,
            )
            sections.append(f"## Раунд {round_no}/{safe_rounds}\n{round_result}")

            if round_no >= safe_rounds:
                continue

            # Для следующего раунда даем сжатый контекст предыдущего результата.
            clipped = round_result[:safe_clip]
            current_topic = (
                "Улучши и уточни предыдущее решение. "
                "Сделай более практичный, проверяемый и устойчивый план.\n\n"
                f"Исходная тема:\n{base_topic}\n\n"
                f"Результат предыдущего раунда:\n{clipped}"
            )

        logger.info("agent_room_loop_completed", topic=topic, rounds=safe_rounds)
        return f"🐝 **Swarm Loop: {base_topic}**\n\n" + "\n\n".join(sections)

