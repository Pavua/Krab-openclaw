# -*- coding: utf-8 -*-
"""
Research Pipeline для Swarm — выделенный модуль (Phase 7).

Инкапсулирует всю бизнес-логику `!swarm research`:
- формирование промпта с обязательным web_search
- запуск AgentRoom через router
- сохранение артефакта в swarm_artifact_store
- конфигурируемые параметры: max_sources, max_rounds, output_format
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import TYPE_CHECKING, Callable

from .logger import get_logger
from .swarm import AgentRoom
from .swarm_artifact_store import swarm_artifact_store
from .swarm_bus import TEAM_REGISTRY

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Конфигурация пайплайна
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ResearchConfig:
    """Параметры research pipeline."""

    # Максимальное кол-во источников, которые просим найти
    max_sources: int = 5
    # Кол-во раундов AgentRoom (пока pipeline делает один раунд, резерв для future)
    max_rounds: int = 1
    # Формат вывода: 'structured' (Summary/Key Findings/Sources) или 'free'
    output_format: str = "structured"
    # Команда свёрма, которая выполняет research
    team_key: str = "analysts"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class SwarmResearchPipeline:
    """
    Research Pipeline для Swarm.

    Пример использования::

        pipeline = SwarmResearchPipeline(config=ResearchConfig(max_sources=7))
        result = await pipeline.run(
            raw_topic="тренды AI 2025",
            router_factory=my_router_factory,
            swarm_bus=swarm_bus,
        )
    """

    def __init__(self, config: ResearchConfig | None = None) -> None:
        self.config = config or ResearchConfig()

    # ------------------------------------------------------------------
    # Формирование промпта
    # ------------------------------------------------------------------

    def build_prompt(self, raw_topic: str) -> str:
        """Собирает промпт с требованиями к web_search и структуре ответа."""
        if self.config.output_format == "structured":
            structure_hint = (
                "Структурируй результат: Summary, Key Findings, Sources "
                f"(не менее {self.config.max_sources} источников)."
            )
        else:
            structure_hint = f"Найди не менее {self.config.max_sources} источников по теме."

        return (
            f"Проведи исследование по теме: {raw_topic}. "
            "Обязательно используй web_search для поиска актуальной информации. " + structure_hint
        )

    # ------------------------------------------------------------------
    # Основной метод
    # ------------------------------------------------------------------

    async def run(
        self,
        raw_topic: str,
        *,
        router_factory: Callable[[str], object],
        swarm_bus: object,
    ) -> str:
        """
        Запускает research pipeline.

        Args:
            raw_topic: Тема исследования (без префикса 'research').
            router_factory: Фабрика роутеров `(team_name) -> RouterAdapter`.
            swarm_bus: SwarmBus для межкомандного broadcast.

        Returns:
            Финальный текст исследования.
        """
        team_key = self.config.team_key
        research_prompt = self.build_prompt(raw_topic)

        logger.info(
            "research_pipeline_start",
            topic=raw_topic,
            team=team_key,
            max_sources=self.config.max_sources,
            output_format=self.config.output_format,
        )

        # Замер времени по стадиям — setup/round/persist
        t0 = time.monotonic()

        roles = TEAM_REGISTRY.get(team_key)
        room = AgentRoom(roles=roles)
        router = router_factory(team_key)
        t_setup = time.monotonic() - t0

        result_text = await room.run_round(
            research_prompt,
            router,
            _bus=swarm_bus,
            _router_factory=router_factory,
            _team_name=team_key,
        )
        t_round = time.monotonic() - t0 - t_setup

        # Сохраняем артефакт с меткой [research] в thread executor,
        # чтобы sync file I/O не блокировал event loop
        await asyncio.to_thread(
            swarm_artifact_store.save_round_artifact,
            team=team_key,
            topic=f"[research] {raw_topic}",
            result=result_text,
        )
        t_persist = time.monotonic() - t0 - t_setup - t_round
        t_total = time.monotonic() - t0

        logger.info(
            "research_pipeline_stage_timings",
            topic=raw_topic,
            setup_ms=round(t_setup * 1000, 1),
            round_ms=round(t_round * 1000, 1),
            persist_ms=round(t_persist * 1000, 1),
            total_ms=round(t_total * 1000, 1),
        )

        logger.info(
            "research_pipeline_done",
            topic=raw_topic,
            result_len=len(result_text),
        )

        return result_text


# ---------------------------------------------------------------------------
# Синглтон (удобство для обычного использования)
# ---------------------------------------------------------------------------

_default_pipeline: SwarmResearchPipeline | None = None


def get_research_pipeline(config: ResearchConfig | None = None) -> SwarmResearchPipeline:
    """Возвращает пайплайн с дефолтной конфигурацией (синглтон)."""
    global _default_pipeline
    if _default_pipeline is None or config is not None:
        _default_pipeline = SwarmResearchPipeline(config=config)
    return _default_pipeline
