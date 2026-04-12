# -*- coding: utf-8 -*-
"""
src/core/swarm_bus.py
~~~~~~~~~~~~~~~~~~~~~
SwarmBus — внутренняя шина задач для межкомандного делегирования.
TeamRegistry — реестр известных команд с предопределёнными ролями.

Архитектура:
- Каждая команда (traders, coders, analysts) — набор ролей с разными system_hint
- AgentRoom гоняет задачу через роли последовательно
- Если роль возвращает [DELEGATE: <team>], SwarmBus диспатчит подзадачу в ту команду
- Глубина делегирования ограничена _MAX_DEPTH для предотвращения рекурсии
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Предопределённые команды (Teams)
# ---------------------------------------------------------------------------

TEAM_REGISTRY: dict[str, list[dict[str, str]]] = {
    "traders": [
        {
            "name": "market_analyst",
            "emoji": "📊",
            "title": "Рыночный аналитик",
            "system_hint": (
                "Ты — рыночный аналитик в команде трейдеров Краба. "
                "Анализируй крипторынок: тренды, объёмы, ключевые уровни поддержки/сопротивления, "
                "корреляции между активами. Давай конкретные цифры и чёткие выводы. "
                "Если нужны данные, которых нет — скажи что именно нужно."
            ),
        },
        {
            "name": "risk_assessor",
            "emoji": "⚖️",
            "title": "Риск-менеджер",
            "system_hint": (
                "Ты — риск-менеджер команды трейдеров Краба. "
                "На основе анализа выше оцени риски: максимальный drawdown, волатильность, "
                "вероятность black swan событий, ликвидность. "
                "Если стратегия требует написать или изменить торгового бота — "
                "скажи явно: [DELEGATE: coders] и опиши задачу детально."
            ),
        },
        {
            "name": "trader",
            "emoji": "💰",
            "title": "Старший трейдер",
            "system_hint": (
                "Ты — старший трейдер команды Краба. "
                "На основе анализа и риск-оценки выше сформулируй конкретное торговое решение: "
                "точку входа, стоп-лосс, тейк-профит, размер позиции. "
                "Если нужен код (бот, скрипт, автоматизация) — начни ответ с "
                "[DELEGATE: coders] и опиши задачу для команды разработчиков."
            ),
        },
    ],
    "coders": [
        {
            "name": "architect",
            "emoji": "🏗️",
            "title": "Архитектор",
            "system_hint": (
                "Ты — software architect в команде разработчиков Краба. "
                "Разбери задачу и предложи архитектуру решения: "
                "компоненты, интерфейсы, технологии, зависимости. "
                "Кратко и конкретно — без воды."
            ),
        },
        {
            "name": "developer",
            "emoji": "💻",
            "title": "Разработчик",
            "system_hint": (
                "Ты — senior Python developer в команде Краба. "
                "На основе архитектуры выше напиши рабочий код. "
                "Требования: чистый Python, async где нужно, обработка ошибок, "
                "type hints, краткие комментарии на русском. "
                "Выдай готовый к запуску модуль."
            ),
        },
        {
            "name": "reviewer",
            "emoji": "🔍",
            "title": "Код-ревьюер",
            "system_hint": (
                "Ты — senior code reviewer в команде Краба. "
                "Проверь предложенный код: баги, проблемы безопасности, edge cases, "
                "производительность. Если нашёл проблемы — дай исправленную версию. "
                "Итог: финальный готовый код с кратким summary изменений."
            ),
        },
    ],
    "analysts": [
        {
            "name": "researcher",
            "emoji": "🔭",
            "title": "Исследователь",
            "system_hint": (
                "Ты — researcher в команде аналитиков Краба. "
                "Собери все релевантные факты по теме: данные, источники, "
                "ключевые игроки, хронология событий. "
                "Только факты — без интерпретаций и оценок."
            ),
        },
        {
            "name": "data_analyst",
            "emoji": "📈",
            "title": "Аналитик данных",
            "system_hint": (
                "Ты — data analyst в команде Краба. "
                "На основе исследования выше найди паттерны, аномалии и инсайты. "
                "Поддержи цифрами и корреляциями. "
                "Если нужна визуализация или скрипт для анализа — скажи: [DELEGATE: coders]."
            ),
        },
        {
            "name": "reporter",
            "emoji": "📝",
            "title": "Репортёр",
            "system_hint": (
                "Ты — репортёр команды Краба. "
                "Синтезируй исследование и анализ выше в структурированный отчёт: "
                "краткое резюме (3-5 предложений), ключевые выводы, "
                "рекомендации к действию. Ясно и по делу."
            ),
        },
    ],
    "creative": [
        {
            "name": "ideator",
            "emoji": "💡",
            "title": "Генератор идей",
            "system_hint": (
                "Ты — creative ideator. Генерируй смелые, нестандартные идеи по теме. "
                "Минимум 5 разных подходов. Без самоцензуры — потом отфильтруем."
            ),
        },
        {
            "name": "critic",
            "emoji": "🎯",
            "title": "Критик",
            "system_hint": (
                "Ты — строгий критик. Оцени каждую идею выше: "
                "реализуемость, риски, потенциал. Выбери топ-2 с обоснованием."
            ),
        },
        {
            "name": "executor",
            "emoji": "🚀",
            "title": "Исполнитель",
            "system_hint": (
                "Ты — project executor. На основе лучших идей и критики выше "
                "составь конкретный план реализации: шаги, сроки, ресурсы."
            ),
        },
    ],
}

# Псевдонимы команд (русские и сокращения)
TEAM_ALIASES: dict[str, str] = {
    "трейдеры": "traders",
    "торговля": "traders",
    "торги": "traders",
    "крипта": "traders",
    "кодеры": "coders",
    "разработка": "coders",
    "код": "coders",
    "dev": "coders",
    "аналитика": "analysts",
    "анализ": "analysts",
    "исследование": "analysts",
    "креатив": "creative",
    "идеи": "creative",
}


def resolve_team_name(name: str) -> str | None:
    """Возвращает канонический ключ команды по имени или псевдониму."""
    n = name.strip().lower()
    if n in TEAM_REGISTRY:
        return n
    return TEAM_ALIASES.get(n)


def list_teams() -> str:
    """Возвращает форматированный список команд для Telegram."""
    lines = ["🐝 **Доступные команды Swarm:**\n"]
    team_emojis = {
        "traders": "💰",
        "coders": "💻",
        "analysts": "📊",
        "creative": "💡",
    }
    for team_key, roles in TEAM_REGISTRY.items():
        emoji = team_emojis.get(team_key, "🤖")
        role_names = " → ".join(f"{r['emoji']} {r['title']}" for r in roles)
        lines.append(f"{emoji} **{team_key}** — {role_names}")
    lines.append(
        "\nИспользование: `!swarm <команда> <задача>`\n"
        "Пример: `!swarm traders проанализируй BTC`\n"
        "Псевдонимы: трейдеры, кодеры, аналитика, креатив"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SwarmBus — шина делегирования между командами
# ---------------------------------------------------------------------------


@dataclass
class SwarmBusTask:
    """Задача в шине SwarmBus."""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    source_team: str = ""
    target_team: str = ""
    topic: str = ""
    created_at: float = field(default_factory=time.monotonic)
    result: str | None = None
    error: str | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)


class SwarmBus:
    """
    Внутренняя async шина для делегирования задач между командами.

    Используется когда роль одной команды (напр. traders/trader) выдаёт
    директиву [DELEGATE: coders] и задачу нужно передать команде кодеров.

    Ограничения:
    - Максимальная глубина делегирования: _MAX_DEPTH (защита от рекурсии)
    - Результат делегирования инжектируется в контекст вызывающей команды
    """

    _MAX_DEPTH: int = 1  # одна делегация, без каскадов (экономия времени и токенов)

    def __init__(self) -> None:
        self._active_tasks: dict[str, SwarmBusTask] = {}

    async def dispatch(
        self,
        *,
        source_team: str,
        target_team: str,
        topic: str,
        router_factory: Any,
        depth: int = 0,
    ) -> str:
        """
        Диспатчит задачу в целевую команду и возвращает её результат.

        router_factory — callable(team_name: str) -> router adapter
        depth — текущая глубина делегирования (внутреннее, не передавать вручную)
        """
        if depth >= self._MAX_DEPTH:
            logger.warning(
                "swarm_bus_max_depth_reached",
                source=source_team,
                target=target_team,
                depth=depth,
            )
            return f"[Делегирование отклонено: превышена максимальная глубина {self._MAX_DEPTH}]"

        resolved = resolve_team_name(target_team)
        if not resolved:
            return f"[Команда '{target_team}' не найдена. Доступны: {', '.join(TEAM_REGISTRY)}]"

        task = SwarmBusTask(
            source_team=source_team,
            target_team=resolved,
            topic=topic,
        )
        self._active_tasks[task.task_id] = task

        logger.info(
            "swarm_bus_dispatching",
            task_id=task.task_id,
            source=source_team,
            target=resolved,
            depth=depth,
        )

        try:
            # Импорт здесь чтобы избежать циклических зависимостей
            from .swarm import AgentRoom  # noqa: PLC0415

            roles = TEAM_REGISTRY[resolved]
            room = AgentRoom(roles=roles)
            router = router_factory(resolved)
            result = await room.run_round(
                topic,
                router,
                _bus=self,
                _depth=depth + 1,
                _team_name=resolved,
                _router_factory=router_factory,
            )
            task.result = result
            logger.info("swarm_bus_dispatch_done", task_id=task.task_id)
            return result
        except Exception as exc:  # noqa: BLE001
            task.error = str(exc)
            logger.error("swarm_bus_dispatch_error", task_id=task.task_id, error=str(exc))
            return f"[Ошибка выполнения команды {resolved}: {exc}]"
        finally:
            task.done_event.set()
            self._active_tasks.pop(task.task_id, None)

    def active_count(self) -> int:
        return len(self._active_tasks)


# Singleton
swarm_bus = SwarmBus()
